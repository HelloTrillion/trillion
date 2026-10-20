import { describe, expect, it } from 'vitest';
import { INITIAL, type View, expire, micFailed, reduce, sent, toolLabel, waiting } from '../src/view';

const feed = (msgs: Record<string, unknown>[], from: View = INITIAL): View =>
  msgs.reduce((v, m) => reduce(v, m as { type: string }), from);

const card = { type: 'confirm.request', pending_id: 'p1', summary: 'Create a.txt', expires_at: 1000, tap_only: false };

describe('view', () => {
  it('becomes ready on session and keeps the model name', () => {
    const v = feed([{ type: 'session', id: 'c1', agent_name: 'Nova', model: { provider: 'x', model: 'm' }, granted_roots: ['/a'] }]);
    expect(v).toMatchObject({ link: 'ready', conversationId: 'c1', agentName: 'Nova', model: 'm', grantedRoots: ['/a'] });
  });

  it('follows a rename on every open page', () => {
    const v = feed([{ type: 'session', id: 'c1', agent_name: 'Nova', model: null }, { type: 'identity', agent_name: 'Vega' }]);
    expect(v.agentName).toBe('Vega');
  });

  it('says plainly when earlier messages were summarized, and keeps them on screen', () => {
    const v = feed([{ type: 'reply.delta', text: 'Earlier' }, { type: 'notice', code: 'compacted' }]);
    expect(v.entries[0]).toEqual({ kind: 'assistant', text: 'Earlier', interrupted: false });
    expect(v.entries[1]).toMatchObject({ kind: 'notice' });
    expect((v.entries[1] as { text: string }).text).toContain('summarized the earlier messages');
  });

  it('puts her one sentence for a failed leg in the banner', () => {
    for (const code of ['provider', 'listening', 'speech']) {
      const v = feed([{ type: 'error', code, kind: 'unreachable', sentence: 'I cannot reach it, so here is what next.' }]);
      expect(v.banner).toBe('I cannot reach it, so here is what next.');
    }
    expect(feed([{ type: 'error', code: 'provider', kind: 'unreachable' }]).banner).toBe(
      'The model provider could not be reached.',
    );
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

  describe('listening', () => {
    const ears = { kind: 'deepgram', choice: 'en', streaming: true };
    const ready = feed([{ type: 'session', id: 'c1', model: { model: 'm' }, listening: ears }]);

    it('knows whether she can hear, and follows a change in settings', () => {
      expect(ready.listening).toEqual({ kind: 'deepgram', streaming: true });
      expect(feed([{ type: 'session', id: 'c1', model: null }]).listening).toBeNull();
      expect(feed([{ type: 'listening', listening: null }], ready).listening).toBeNull();
      expect(feed([{ type: 'listening', listening: { kind: 'whisper', streaming: false } }]).listening?.streaming).toBe(false);
    });

    it('shows the words as they are made out, then makes them the user message', () => {
      let v = feed([{ type: 'mic.start' }, { type: 'state', orb: 'listening' }], ready);
      expect(v).toMatchObject({ mic: true, busy: false, hearing: '' });
      v = feed([{ type: 'transcript.partial', text: 'good' }, { type: 'transcript.partial', text: 'good morning' }], v);
      expect(v.hearing).toBe('good morning');
      v = feed([{ type: 'mic.stop', reason: 'end' }, { type: 'state', orb: 'processing' }], v);
      expect(v).toMatchObject({ mic: false, hearing: 'good morning', busy: true });
      v = feed([{ type: 'transcript.final', text: 'Good morning.' }], v);
      expect(v.hearing).toBe('');
      expect(v.entries).toEqual([{ kind: 'user', text: 'Good morning.' }]);
      expect(waiting(v)).toBe(true);
    });

    it('throws the words away when listening is cancelled or nothing was said', () => {
      const heard = feed([{ type: 'mic.start' }, { type: 'transcript.partial', text: 'delete every' }], ready);
      for (const reason of ['cancelled', 'nothing', 'failed', 'inaudible']) {
        const v = feed([{ type: 'mic.stop', reason }], heard);
        expect(v).toMatchObject({ mic: false, hearing: '', entries: [] });
      }
      // Words that arrive after the microphone closed are not shown.
      expect(feed([{ type: 'mic.stop', reason: 'cancelled' }, { type: 'transcript.partial', text: 'late' }], heard).hearing).toBe('');
    });

    it('says why she cannot hear, and that typing still works', () => {
      const open = feed([{ type: 'mic.start' }], ready);
      const dead = feed([{ type: 'error', code: 'listening', kind: 'inaudible' }], open);
      expect(dead.mic).toBe(false);
      expect(dead.banner).toMatch(/can't hear your microphone.*still type/);
      expect(feed([{ type: 'error', code: 'listening', kind: 'unauthorized' }], open).banner).toMatch(/rejected the key/);
      expect(feed([{ type: 'error', code: 'listening', kind: 'mystery' }], open).banner).toMatch(/Listening failed/);
      expect(feed([{ type: 'error', code: 'no_listening' }], ready).banner).toMatch(/Mic settings/);
      expect(micFailed(open, 'denied')).toMatchObject({ mic: false });
      expect(micFailed(open, 'denied').banner).toMatch(/did not allow the microphone/);
      expect(micFailed(open, 'no_device').banner).toMatch(/No microphone/);
      // Opening the microphone again clears the old complaint.
      expect(feed([{ type: 'mic.start' }], dead).banner).toBeNull();
    });

    it('explains sound with no words in it', () => {
      const v = feed([{ type: 'notice', code: 'listening', reason: 'unheard' }], ready);
      expect(v.entries).toEqual([{ kind: 'notice', text: 'I heard something, but could not make out any words.' }]);
    });
  });

  it('labels memory in plain words and everything else by its name', () => {
    expect(toolLabel('memory_write', null, true)).toBe('Remembering …');
    expect(toolLabel('memory_write', 'succeeded', true)).toBe('Remembered');
    expect(toolLabel('memory_recall', 'succeeded', true)).toBe('Checked what she remembers');
    // A refusal keeps the tool's name, so what failed is never hidden behind a friendly word.
    expect(toolLabel('memory_write', 'invalid_args', false)).toBe('memory_write: invalid args');
    expect(toolLabel('memory_forget', 'confirmation_required', true)).toBe('memory_forget: confirmation required');
    expect(toolLabel('file_read', null, true)).toBe('file_read …');
    expect(toolLabel('file_read', 'succeeded', true)).toBe('file_read: succeeded');
  });
});
