import { describe, expect, it } from 'vitest';
import { INITIAL, type View, expire, reduce, sent, waiting } from '../src/view';

const feed = (msgs: Record<string, unknown>[], from: View = INITIAL): View =>
  msgs.reduce((v, m) => reduce(v, m as { type: string }), from);

const card = { type: 'confirm.request', pending_id: 'p1', summary: 'Create a.txt', expires_at: 1000, tap_only: false };

describe('view', () => {
  it('becomes ready on session and keeps the model name', () => {
    const v = feed([{ type: 'session', id: 'c1', agent_name: 'Nova', model: { provider: 'x', model: 'm' }, granted_roots: ['/a'] }]);
    expect(v).toMatchObject({ link: 'ready', conversationId: 'c1', agentName: 'Nova', model: 'm', grantedRoots: ['/a'] });
  });

  it('has no model at rung 0', () => {
    expect(feed([{ type: 'session', id: 'c1', model: null }]).model).toBeNull();
  });

  it('joins streamed text into one reply', () => {
    const v = feed([
      { type: 'reply.delta', text: 'Hel' },
      { type: 'reply.delta', text: 'lo' },
    ]);
    expect(v.entries).toEqual([{ kind: 'assistant', text: 'Hello', interrupted: false }]);
  });

  it('starts a new reply after a tool, rather than appending to the old one', () => {
    const v = feed([
      { type: 'reply.delta', text: 'One' },
      { type: 'tool.start', call_id: 't1', name: 'file_read', risk: 'read' },
      { type: 'tool.end', call_id: 't1', ok: true, summary: 'succeeded' },
      { type: 'reply.delta', text: 'Two' },
    ]);
    expect(v.entries.map((e) => e.kind)).toEqual(['assistant', 'tool', 'assistant']);
    expect(v.entries[1]).toMatchObject({ outcome: 'succeeded', ok: true });
  });

  it('shows the user message at once and marks the view busy', () => {
    const v = sent(INITIAL, 'hi');
    expect(v.busy).toBe(true);
    expect(v.entries).toEqual([{ kind: 'user', text: 'hi' }]);
  });

  it('follows the orb, and is busy only while working', () => {
    expect(feed([{ type: 'state', orb: 'processing' }]).busy).toBe(true);
    expect(feed([{ type: 'state', orb: 'idle' }]).busy).toBe(false);
    expect(feed([{ type: 'state', orb: 'nonsense' }]).orb).toBe('idle');
  });

  it('shows a card once, however many times it is announced', () => {
    const v = feed([card, card]);
    expect(v.entries.filter((e) => e.kind === 'card')).toHaveLength(1);
    expect(v.entries[0]).toMatchObject({ status: 'waiting', summary: 'Create a.txt' });
  });

  it.each([
    ['succeeded', '', 'done'],
    ['cancelled', '', 'declined'],
    ['failed', '', 'failed'],
    ['unknown', '', 'unknown'],
    ['refused', 'expired', 'closed'],
  ])('settles a card that %s (%s) as %s', (status, reason, expected) => {
    const v = feed([card, { type: 'confirm.resolved', pending_id: 'p1', status, reason }]);
    expect(v.entries[0]).toMatchObject({ kind: 'card', status: expected });
  });

  it('never calls a refused or failed card done', () => {
    for (const status of ['refused', 'failed', 'unknown', 'cancelled']) {
      const v = feed([card, { type: 'confirm.resolved', pending_id: 'p1', status }]);
      const entry = v.entries[0];
      expect(entry?.kind === 'card' && entry.note).not.toMatch(/^Done/);
    }
  });

  it('closes a card whose time ran out on screen', () => {
    const waiting = feed([card]);
    expect(expire(waiting, 999)).toBe(waiting);
    expect(expire(waiting, 1000).entries[0]).toMatchObject({ status: 'closed' });
  });

  it('starts a new reply for a new turn', () => {
    const v = feed([
      { type: 'reply.delta', text: 'Shall I?' },
      { type: 'turn.complete' },
      { type: 'reply.delta', text: 'Done.' },
    ]);
    expect(v.entries.map((e) => e.kind === 'assistant' && e.text)).toEqual(['Shall I?', 'Done.']);
  });

  it('marks the reply that was interrupted', () => {
    const v = feed([{ type: 'reply.delta', text: 'So' }, { type: 'interrupted' }, { type: 'reply.delta', text: 'New' }]);
    expect(v.entries).toEqual([
      { kind: 'assistant', text: 'So', interrupted: true },
      { kind: 'assistant', text: 'New', interrupted: false },
    ]);
  });

  it('signs out on an unauthenticated error and names a provider failure', () => {
    expect(feed([{ type: 'error', code: 'unauthenticated' }]).link).toBe('unauthenticated');
    expect(feed([{ type: 'error', code: 'provider', kind: 'unauthorized' }]).banner).toMatch(/rejected the key/);
  });

  it('restores a conversation from history', () => {
    const v = feed([{ type: 'history', messages: [{ role: 'user', text: 'a' }, { role: 'assistant', text: 'b' }] }]);
    expect(v.entries.map((e) => e.kind)).toEqual(['user', 'assistant']);
  });

  it('ignores messages it does not know', () => {
    expect(reduce(INITIAL, { type: 'win' })).toBe(INITIAL);
  });
});

describe('voice in the view', () => {
  const speech = { kind: 'system', where: 'client', voice: 'v', sample_rate: null };

  it('learns how she speaks from the session and from a change in settings', () => {
    expect(feed([{ type: 'session', id: 'c', model: null, speech }]).speech).toEqual(speech);
    expect(feed([{ type: 'speech', speech: null }]).speech).toBeNull();
    expect(feed([{ type: 'speech', speech: { kind: 'x', where: 'elsewhere' } }]).speech).toBeNull();
  });

  it('says her voice failed and that the reply goes on as text', () => {
    const v = feed([{ type: 'reply.delta', text: 'Still here.' }, { type: 'error', code: 'speech', kind: 'rate_limited' }]);
    expect(v.banner).toMatch(/voice provider is rate-limiting.*answering in text/i);
    expect(v.entries).toHaveLength(1);
  });

  it('stops for good when the conversation moves to another tab', () => {
    expect(feed([{ type: 'error', code: 'replaced' }]).link).toBe('replaced');
  });

  it('says she is thinking for as long as nothing is arriving', () => {
    const ready = feed([{ type: 'session', id: 'c1', model: { model: 'm' } }]);
    expect(waiting(ready)).toBe(false);
    // A reasoning model can be silent for a long time after the message is sent.
    let v = feed([{ type: 'state', orb: 'processing' }], sent(ready, 'hi'));
    expect(waiting(v)).toBe(true);
    v = feed([{ type: 'state', orb: 'speaking' }, { type: 'reply.delta', text: 'Hel' }], v);
    expect(waiting(v)).toBe(false);
    // Between a tool call and what she says next.
    v = feed([{ type: 'state', orb: 'processing' }, { type: 'tool.start', call_id: 't1', name: 'file_read' }], v);
    expect(waiting(v)).toBe(true);
    v = feed([{ type: 'reply.delta', text: 'Done.' }, { type: 'turn.complete' }], v);
    expect(waiting(v)).toBe(false);
    expect(waiting(feed([{ type: 'interrupted' }], sent(ready, 'hi')))).toBe(false);
    expect(waiting({ ...sent(ready, 'hi'), link: 'offline' })).toBe(false);
  });
});
