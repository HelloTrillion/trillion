import { describe, expect, it } from 'vitest';
import { INITIAL, type View, backgroundLine, boardOf, boardOpen, busyOf, dueLabel, expire, localClock, micFailed, quietLine, readyConversation, reduce, sent, unreadFor, toolLabel, waiting } from '../src/view';

const feed = (msgs: Record<string, unknown>[], from: View = INITIAL): View =>
  msgs.reduce((v, m) => reduce(v, m as { type: string }), from);

const card = { type: 'confirm.request', pending_id: 'p1', summary: 'Create a.txt', expires_at: 1000, tap_only: false };

describe('view', () => {
  it('keeps the conversation title and follows a new one', () => {
    let v = feed([{ type: 'session', id: 'c1', agent_name: 'Nova', model: null, title: null }]);
    expect(v.title).toBeNull();
    v = feed([{ type: 'conversation.title', id: 'c1', title: 'The trip' }], v);
    expect(v.title).toBe('The trip');
    v = feed([{ type: 'conversation.title', id: 'other', title: 'Not ours' }], v);
    expect(v.title).toBe('The trip');
  });

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

  it('labels a confirm of an already-settled card as such, not as a second success', () => {
    expect(toolLabel('confirm_pending_action', 'already_resolved', true)).toBe('Already settled');
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

  it("keeps the host's name for the tool and why it needs the user", () => {
    const v = feed([{ ...card, tool: 'fs_write', tool_label: 'Change a file', reason: 'write' }]);
    expect(v.entries[0]).toMatchObject({ toolLabel: 'Change a file', reason: 'write' });
  });

  it('falls back to the tool name, and no reason, when the core sent neither', () => {
    expect(feed([{ ...card, tool: 'send_message' }]).entries[0]).toMatchObject({ toolLabel: 'Send message', reason: null });
    expect(feed([{ ...card, tool: 'x', reason: 'nonsense' }]).entries[0]).toMatchObject({ reason: null });
    expect(feed([card]).entries[0]).toMatchObject({ toolLabel: 'An action' });
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

  it('trims an interrupted reply to what was heard', () => {
    const v = feed([{ type: 'reply.delta', text: 'One. Two. Three.' }, { type: 'interrupted', kept: 'One.…' }]);
    expect(v.entries[0]).toEqual({ kind: 'assistant', text: 'One.…', interrupted: true });
  });

  it('shows a reply cut off before a word was heard as a reload does', () => {
    const live = feed([{ type: 'reply.delta', text: 'One. Two.' }, { type: 'interrupted', kept: '' }]);
    // Saved as the marker alone, which a reload shows as its text.
    const reloaded = feed([{ type: 'history', messages: [{ role: 'assistant', text: '[interrupted]' }] }]);
    expect(live.entries).toEqual(reloaded.entries);
  });

  it('keeps the full text when nothing says what was heard', () => {
    const v = feed([{ type: 'reply.delta', text: 'Typed answer.' }, { type: 'interrupted' }]);
    expect(v.entries[0]).toEqual({ kind: 'assistant', text: 'Typed answer.', interrupted: true });
  });

  it('trims a finished reply that was talked over while it still played', () => {
    const v = feed([
      { type: 'reply.delta', text: 'One. Two.' },
      { type: 'tool.start', call_id: 'c', name: 'x' },
      { type: 'reply.delta', text: 'Three. Four.' },
      { type: 'turn.complete' },
      { type: 'interrupted', kept: 'Three.…' },
    ]);
    expect(v.entries.map((e) => (e.kind === 'assistant' ? [e.text, e.interrupted] : e.kind))).toEqual([
      ['One. Two.', false],
      'tool',
      ['Three.…', true],
    ]);
    expect(v).toMatchObject({ replyOpen: false, busy: false });
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

  it('shows the app\'s line from history as a host line, never as the user\'s', () => {
    const v = feed([
      { type: 'history', messages: [{ role: 'host', text: 'Building your day' }, { role: 'assistant', text: 'Two meetings.' }] },
    ]);
    expect(v.entries).toEqual([
      { kind: 'host', text: 'Building your day' },
      { kind: 'assistant', text: 'Two meetings.', interrupted: false },
    ]);
  });

  it('draws the host line as it arrives, before the turn it starts', () => {
    const v = feed([
      { type: 'session', id: 'd1', agent_name: 'Nova', model: null, title: null },
      { type: 'host', text: 'Building your day' },
      { type: 'state', orb: 'processing' },
    ]);
    expect(v.entries).toEqual([{ kind: 'host', text: 'Building your day' }]);
    expect(feed([{ type: 'host' }]).entries).toEqual([]);
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
      expect(ready.listening).toEqual({ kind: 'deepgram', streaming: true, turns: false });
      expect(feed([{ type: 'listening', listening: { kind: 'deepgram', streaming: true, turns: true } }]).listening?.turns).toBe(true);
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
      expect(feed([{ type: 'error', code: 'no_listening' }], ready).banner).toMatch(/Settings, under Hearing/);
      expect(micFailed(open, 'denied')).toMatchObject({ mic: false });
      expect(micFailed(open, 'denied').banner).toMatch(/did not allow the microphone/);
      // A microphone sending pure silence says where to choose another (Safari and an audio interface).
      expect(micFailed(open, 'silent').banner).toMatch(/sending silence.*Settings, under Hearing/);
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

describe('notices from reminders and timed asks', () => {
  const ready = feed([{ type: 'session', id: 'c1', agent_name: 'Nova', model: { model: 'm' } }]);
  const due = Date.UTC(2026, 9, 24, 20, 0);
  const notice = (over: Record<string, unknown> = {}) => ({
    type: 'notice',
    code: 'schedule',
    id: 'n1',
    kind: 'reminder',
    title: 'Stretch',
    text: 'Time to stretch.',
    due_ms: due,
    due: '4:00 pm',
    late: false,
    ...over,
  });

  it('arrives as her message', () => {
    const v = feed([notice()], ready);
    expect(v.entries).toEqual([
      {
        kind: 'scheduled',
        id: 'n1',
        what: 'reminder',
        title: 'Stretch',
        text: 'Time to stretch.',
        dueMs: due,
        due: '4:00 pm',
        late: false,
        conversationId: null,
        link: null,
        detail: null,
        quiet: null,
        spoken: true,
      },
    ]);
  });

  it('keeps a ready line with the conversation it opens, and names it to be marked unread', () => {
    const msg = notice({ kind: 'ready', title: 'Your Monday revenue is ready', text: 'Your Monday revenue is ready.', conversation_id: 'r1' });
    const v = feed([msg], ready);
    expect(v.entries[0]).toMatchObject({ kind: 'scheduled', what: 'ready', title: 'Your Monday revenue is ready', conversationId: 'r1' });
    expect(readyConversation(msg)).toBe('r1');
    expect(readyConversation(notice())).toBeNull();
    expect(readyConversation(notice({ kind: 'ready' }))).toBeNull();
    expect(readyConversation({ type: 'state', kind: 'ready', conversation_id: 'r1' })).toBeNull();
  });

  it('marks unread only a conversation other than the open one', () => {
    const msg = notice({ kind: 'ready', conversation_id: 'r1' });
    expect(unreadFor(msg, ready)).toBe('r1');
    expect(unreadFor(notice({ kind: 'ready', conversation_id: 'c1' }), ready)).toBeNull();
    expect(unreadFor(notice(), ready)).toBeNull();
  });

  it('draws a ready line again on reload from the host line that kept it', () => {
    const v = feed(
      [
        {
          type: 'history',
          messages: [
            { role: 'host', text: 'Your Monday revenue is ready', conversation_id: 'r1' },
            { role: 'host', text: 'Building your day' },
          ],
        },
      ],
      ready,
    );
    expect(v.entries).toEqual([
      { kind: 'host', text: 'Your Monday revenue is ready', conversationId: 'r1' },
      { kind: 'host', text: 'Building your day' },
    ]);
  });

  it('waits for a reply that is still arriving, then follows it', () => {
    const v = feed([{ type: 'reply.delta', text: 'Half a' }, notice(), { type: 'reply.delta', text: ' reply.' }], ready);
    expect(v.entries).toEqual([{ kind: 'assistant', text: 'Half a reply.', interrupted: false }]);
    const after = reduce(v, { type: 'turn.complete' });
    expect(after.entries.map((e) => e.kind)).toEqual(['assistant', 'scheduled']);
    expect(after.held).toEqual([]);
  });

  it('waits while she works, and follows a reply that was stopped', () => {
    const v = feed([{ type: 'state', orb: 'processing' }, notice()], ready);
    expect(v.entries).toEqual([]);
    expect(reduce(v, { type: 'interrupted' }).entries.map((e) => e.kind)).toEqual(['scheduled']);
  });

  it('is not held for ever by a turn that failed without completing', () => {
    const v = feed([{ type: 'reply.delta', text: 'Half' }, notice(), { type: 'error', code: 'internal' }, { type: 'state', orb: 'error' }], ready);
    expect(v.entries.map((e) => e.kind)).toEqual(['assistant', 'scheduled']);
  });

  it('is shown once, however often it is sent', () => {
    expect(feed([notice(), notice()], ready).entries).toHaveLength(1);
  });

  it('never carries words for a missed or failed one', () => {
    const v = feed([notice({ kind: 'missed', text: 'should not show' })], ready);
    expect(v.entries[0]).toMatchObject({ kind: 'scheduled', what: 'missed', text: null });
  });

  it('ignores one with no id or an unknown kind', () => {
    expect(feed([notice({ id: '' }), notice({ kind: 'other' })], ready).entries).toEqual([]);
  });

  it('labels a late one with the due time the core gave, in the schedule\'s own zone', () => {
    const v = feed([notice({ late: true, due: 'Fri 11:44 pm' })], ready);
    expect(v.entries[0]).toMatchObject({ due: 'Fri 11:44 pm', late: true });
    expect(dueLabel('Fri 11:44 pm')).toBe('due Fri 11:44 pm');
    expect(quietLine({ what: 'missed', title: 'Email', due: '4:00 pm' })).toBe('Missed: Email (due 4:00 pm)');
    expect(quietLine({ what: 'failed', title: 'Email', due: 'Sat 4:00 pm' })).toBe("Didn't finish: Email (due Sat 4:00 pm)");
  });

  it('falls back to this browser\'s clock only when the core gave no words', () => {
    expect(localClock(due, 'America/New_York')).toBe('4:00 pm');
    expect(localClock(Date.UTC(2026, 9, 24, 13, 5), 'America/New_York')).toBe('9:05 am');
    const v = feed([notice({ due: '' })], ready);
    expect((v.entries[0] as { due: string }).due).toBe(localClock(due));
  });
});

describe('notice speech never makes the page busy', () => {
  const ready = feed([{ type: 'session', id: 'c1', agent_name: 'Nova', model: { model: 'm' } }]);

  it('is busy for a turn, and not for a notice', () => {
    expect(busyOf('processing', false)).toBe(true);
    expect(busyOf('speaking', false)).toBe(true);
    expect(busyOf('speaking', true)).toBe(false);
    expect(busyOf('idle', false)).toBe(false);
    const notice = reduce(ready, { type: 'state', orb: 'speaking', notice: true });
    expect(notice).toMatchObject({ orb: 'speaking', busy: false, noticeSpeaking: true });
    expect(waiting(notice)).toBe(false);
    const turn = reduce(ready, { type: 'state', orb: 'speaking' });
    expect(turn).toMatchObject({ orb: 'speaking', busy: true, noticeSpeaking: false });
  });

  it('puts a message refused as busy back in the box, and off the screen', () => {
    const v = reduce(sent(ready, 'hi'), { type: 'error', code: 'busy' });
    expect(v.entries.filter((e) => e.kind === 'user')).toEqual([]);
    expect(v.draft).toBe('hi');
    expect(v.busy).toBe(false);
    expect(v.banner).toBe('Still answering the last message.');
  });

  it('puts a message refused while a routine builds back in the box, and says why', () => {
    const v = reduce(sent(ready, 'hi'), { type: 'error', code: 'routine_building' });
    expect(v.entries.filter((e) => e.kind === 'user')).toEqual([]);
    expect(v.draft).toBe('hi');
    expect(v.busy).toBe(false);
    expect(v.banner).toBe("She's building this conversation's routine right now. Try again in a moment.");
    // Spoken, with nothing typed: the reason alone.
    expect(reduce(ready, { type: 'error', code: 'routine_building' }).banner).toBe(v.banner);
  });

  it('puts words heard while a routine builds in the box, so nothing said is lost', () => {
    const heard = feed([{ type: 'mic.start' }, { type: 'transcript.partial', text: 'show me more' }, { type: 'mic.stop', reason: 'end' }], ready);
    const v = reduce(heard, { type: 'error', code: 'routine_building' });
    expect(v).toMatchObject({ hearing: '', draft: 'show me more', entries: [] });
    expect(v.banner).toBe("She's building this conversation's routine right now. Try again in a moment.");
  });

  it('keeps a message once her turn has started, and earlier ones of the same words', () => {
    const before = feed([{ type: 'history', messages: [{ role: 'user', text: 'hi' }] }], ready);
    const started = reduce(sent(before, 'hi'), { type: 'state', orb: 'processing' });
    expect(started.pending).toBeNull();
    // A later busy (a listen refused, say) takes nothing back.
    const later = reduce(started, { type: 'error', code: 'busy' });
    expect(later.entries.filter((e) => e.kind === 'user')).toHaveLength(2);
    expect(later.draft).toBeNull();
    const refused = reduce(sent(before, 'hi'), { type: 'error', code: 'busy' });
    expect(refused.entries).toEqual([{ kind: 'user', text: 'hi' }]);
  });

  it('forgets a message once any other error ends it, so a later busy takes nothing back', () => {
    const failed = reduce(sent(ready, 'hi'), { type: 'error', code: 'internal' });
    expect(failed.pending).toBeNull();
    const later = reduce(failed, { type: 'error', code: 'busy' });
    expect(later.entries).toEqual([{ kind: 'user', text: 'hi' }]);
    expect(later.draft).toBeNull();
  });

  it('never takes back what she heard', () => {
    const v = feed([{ type: 'transcript.final', text: 'hello' }, { type: 'error', code: 'busy' }], ready);
    expect(v.entries).toEqual([{ kind: 'user', text: 'hello' }]);
    expect(v.draft).toBeNull();
  });

  it('keeps the board the core sends, and drops it for a new conversation', () => {
    const board = { title: 'October spend', open: true, widgets: [{ id: 'w1', type: 'stat', size: 'small', label: 'Total', value: '$41' }] };
    let v = feed([{ type: 'session', id: 'c1', agent_name: 'Nova', model: null }, { type: 'board', board }]);
    expect(v.board).toEqual(board);
    v = feed([{ type: 'board', board: { ...board, open: false } }], v);
    expect(v.board?.open).toBe(false);
    v = feed([{ type: 'session', id: 'c2', agent_name: 'Nova', model: null }], v);
    expect(v.board).toBeNull();
    v = feed([{ type: 'board', board: null }], v);
    expect(v.board).toBeNull();
  });
});

describe('boardOf', () => {
  it('reads a board, sizing widgets medium unless they say otherwise', () => {
    const b = boardOf({ title: 'T', open: true, widgets: [{ id: 'w1', type: 'note', text: 'hi' }, { id: 'w2', type: 'stat', size: 'wide' }] });
    expect(b?.widgets.map((w) => [w.id, w.size])).toEqual([['w1', 'medium'], ['w2', 'wide']]);
    expect(b?.widgets[0]?.['text']).toBe('hi');
  });

  it('is null for anything that is not a board, and skips widgets without an id or type', () => {
    expect(boardOf(null)).toBeNull();
    expect(boardOf('board')).toBeNull();
    expect(boardOf([])).toBeNull();
    const b = boardOf({ widgets: [null, { type: 'note' }, { id: 'w3' }, { id: 'w4', type: 'list', size: 'huge' }] });
    expect(b).toEqual({ title: '', open: false, widgets: [{ id: 'w4', type: 'list', size: 'medium' }] });
  });

  it('follows the user opening or putting away a board at once', () => {
    const v = reduce(INITIAL, { type: 'board', board: { title: 'T', open: false, widgets: [] } });
    expect(boardOpen(v, true).board?.open).toBe(true);
    expect(boardOpen(v, false)).toBe(v);
    expect(boardOpen(INITIAL, true)).toBe(INITIAL);
  });
});

describe('background reads', () => {
  const start = { type: 'session', id: 'c1', agent_name: 'Nova', model: null, title: null };
  const call = [
    { type: 'state', orb: 'processing' },
    { type: 'tool.start', call_id: 'k1', name: 'stripe_revenue' },
    { type: 'tool.end', call_id: 'k1', ok: true, summary: 'background' },
  ];

  it('says a read sent to the background is working there', () => {
    const v = feed([start, ...call]);
    const chip = v.entries[0];
    expect(chip).toMatchObject({ kind: 'tool', outcome: 'background', ok: true });
    expect(toolLabel('stripe_revenue', 'background', true)).toBe('stripe_revenue: working in background');
  });

  it('is not busy while the read runs once her turn has ended', () => {
    const v = feed([start, ...call, { type: 'reply.delta', text: 'Pulling your Stripe numbers now.' }, { type: 'turn.complete' }, { type: 'state', orb: 'idle' }, { type: 'jobs', count: 1 }]);
    expect(v.busy).toBe(false);
    expect(waiting(v)).toBe(false);
    expect(v.jobs).toBe(1);
  });

  it('counts reads in the background, in words, and clears at none', () => {
    expect(INITIAL.jobs).toBe(0);
    let v = feed([start, { type: 'jobs', count: 1 }]);
    expect(backgroundLine(v.jobs)).toBe('1 read in background');
    v = feed([{ type: 'jobs', count: 2 }], v);
    expect(backgroundLine(v.jobs)).toBe('2 reads in background');
    v = feed([{ type: 'jobs', count: 0 }], v);
    expect(v.jobs).toBe(0);
    expect(backgroundLine(v.jobs)).toBe('');
    expect(feed([{ type: 'jobs', count: 'x' }]).jobs).toBe(0);
  });

  it('forgets the count on another conversation, which sends its own', () => {
    let v = feed([start, { type: 'jobs', count: 2 }]);
    v = feed([{ ...start, id: 'c2' }], v);
    expect(v.jobs).toBe(0);
  });

  it('shows a background read that came in as a host line on reload', () => {
    const v = feed([
      start,
      {
        type: 'history',
        messages: [
          { role: 'user', text: 'How did Stripe do?' },
          { role: 'assistant', text: 'Pulling your Stripe numbers now.' },
          { role: 'host', text: 'Stripe numbers are in' },
          { role: 'assistant', text: 'Revenue is up 12%.' },
        ],
      },
    ]);
    expect(v.entries[2]).toEqual({ kind: 'host', text: 'Stripe numbers are in' });
  });
});
