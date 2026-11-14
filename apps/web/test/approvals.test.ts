import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  OUTCOME_MS,
  type Strip,
  type StripItem,
  approvalsOf,
  createApprovals,
  collapseAt,
  modeOf,
  outcomeWords,
  placement,
  reasonWords,
  ringLeft,
  spokenHint,
  toStamp,
} from '../src/approvals';
import { EXPIRED_NOTE, type CardStatus, type Entry } from '../src/view';

const card = (pendingId: string, status: CardStatus = 'waiting', note = ''): Entry => ({
  kind: 'card',
  pendingId,
  summary: `Do ${pendingId}`,
  expiresAt: 0,
  tapOnly: false,
  status,
  note,
  toolLabel: 'Change a file',
  reason: 'write',
});

describe('approvalsOf', () => {
  const entries: Entry[] = [
    card('a', 'done'),
    { kind: 'user', text: 'hi' },
    card('b'),
    card('c', 'declined'),
    card('d'),
  ];

  it('lists waiting cards oldest first, so a new one arrives below, then settled ones newest first', () => {
    const ids = approvalsOf(entries, new Set(['a', 'b', 'c', 'd']), new Set()).map((c) => c.pendingId);
    expect(ids).toEqual(['b', 'd', 'c', 'a']);
  });

  it('leaves out the ones the user dismissed and the ones in the conversation', () => {
    const ids = approvalsOf(entries, new Set(['a', 'b', 'c']), new Set(['c'])).map((c) => c.pendingId);
    expect(ids).toEqual(['b', 'a']);
  });
});

describe('modeOf and collapseAt', () => {
  it('asks while waiting, and stays on the outcome while it works', () => {
    expect(modeOf('waiting', null, 0)).toBe('ask');
    expect(modeOf('working', null, 99_999)).toBe('outcome');
  });

  it('shows an outcome for four seconds, then collapses to a row', () => {
    expect(collapseAt(1000)).toBe(1000 + OUTCOME_MS);
    expect(OUTCOME_MS).toBe(4000);
    expect(modeOf('done', 1000, 4999)).toBe('outcome');
    expect(modeOf('done', 1000, 5000)).toBe('row');
    expect(modeOf('declined', null, 5000)).toBe('outcome');
  });
});

describe('words', () => {
  it('says why it needs the user, in plain words', () => {
    expect(reasonWords('irreversible')).toBe("This can't be undone.");
    expect(reasonWords('spend')).toBe('This spends money.');
    expect(reasonWords('write')).toBe('This makes a change.');
    expect(reasonWords('read')).toBe('');
    expect(reasonWords(null)).toBe('');
  });

  it('says whether a spoken yes is enough', () => {
    expect(spokenHint(false)).toBe('Or just say yes');
    expect(spokenHint(true)).toBe("Tap to approve: a spoken yes isn't enough for this");
  });

  it('names each outcome', () => {
    expect(outcomeWords('done', 'Done.')).toBe('Approved · done');
    expect(outcomeWords('declined', '')).toBe('Declined');
    expect(outcomeWords('failed', 'It failed. Nothing was changed.')).toBe("Didn't go through: it failed. Nothing was changed.");
    expect(outcomeWords('closed', EXPIRED_NOTE)).toBe('Expired · ask her again');
    expect(outcomeWords('closed', 'A newer request replaced this one.')).toBe("Didn't go through: a newer request replaced this one.");
    expect(outcomeWords('working', '')).toMatch(/^Approved/);
  });
});

describe('placement', () => {
  it('puts it on the board on a desktop with Settings closed', () => {
    expect(placement({ phone: false, settingsShowing: false })).toBe('board');
  });

  it('keeps it in the conversation on a phone or with Settings showing', () => {
    expect(placement({ phone: true, settingsShowing: false })).toBe('conversation');
    expect(placement({ phone: false, settingsShowing: true })).toBe('conversation');
  });
});

describe('toStamp', () => {
  const cards = [card('a'), card('b'), card('c', 'done')];

  it('stamps a waiting card once its full card is drawn, wherever that is', () => {
    expect(toStamp(cards, new Set(['a']), new Set())).toEqual(['a']);
    expect(toStamp(cards, new Set(['a', 'b']), new Set(['a']))).toEqual(['b']);
  });

  it('never stamps twice, a settled card, or one that is not drawn', () => {
    expect(toStamp(cards, new Set(['a']), new Set(['a']))).toEqual([]);
    expect(toStamp(cards, new Set(['c']), new Set())).toEqual([]);
    expect(toStamp(cards, new Set(), new Set())).toEqual([]);
  });
});

describe('ringLeft', () => {
  it('is the share of the time left, from when it was asked', () => {
    expect(ringLeft(0, 1000, 0)).toBe(1);
    expect(ringLeft(0, 1000, 250)).toBe(0.75);
    expect(ringLeft(0, 1000, 2000)).toBe(0);
    expect(ringLeft(500, 500, 500)).toBe(0);
  });
});

/** The wiring with a stand-in strip and workspace: what the page would do, without a DOM. */
function harness(where = { phone: false, settingsShowing: false }) {
  let entries: Entry[] = [];
  let showing = false;
  let drawn: StripItem[] = [];
  const sent: Record<string, unknown>[] = [];
  const calls = { show: 0, close: 0, toConversation: 0 };
  const strip: Strip = {
    el: {} as HTMLElement,
    draw: (items) => {
      drawn = [...items];
    },
    drawnFull: () => new Set(showing ? drawn.filter((i) => i.mode === 'ask').map((i) => i.card.pendingId) : []),
    focus: () => true,
    dispose: () => {
      drawn = [];
    },
  };
  const a = createApprovals({
    send: (b) => sent.push(b),
    entries: () => entries,
    where: () => where,
    boardShowing: () => showing,
    showBoard: () => {
      calls.show++;
      showing = true;
      strip.draw(a.pinned().items);
    },
    closeBoard: () => {
      calls.close++;
      showing = false;
      a.closed(false);
    },
    hasWidgets: () => false,
    toConversation: () => calls.toConversation++,
    strip: () => strip,
  });
  return {
    a,
    sent,
    calls,
    showing: () => showing,
    drawn: () => drawn,
    /** A message from the core: decided before it is applied, synced after, as main.ts does. */
    ask(id: string) {
      const placed = a.request(id);
      if (!entries.some((e) => e.kind === 'card' && e.pendingId === id)) entries = [...entries, card(id)];
      a.sync();
      return placed;
    },
    settle(id: string, status: CardStatus) {
      entries = entries.map((e) => (e.kind === 'card' && e.pendingId === id ? { ...e, status } : e));
      a.sync();
    },
    history() {
      entries = [];
      a.sync();
    },
    userCloses() {
      showing = false;
      a.closed(false);
    },
  };
}

describe('createApprovals', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('opens the board for a new card and stamps it once, when drawn there', () => {
    const t = harness();
    expect(t.ask('a')).toBe('board');
    expect(t.showing()).toBe(true);
    expect(t.a.onBoard('a')).toBe(true);
    t.a.sync();
    expect(t.sent).toEqual([{ type: 'confirm.shown', pending_id: 'a' }]);
  });

  it('keeps it in the conversation with Settings showing, stamped as drawn there', () => {
    const t = harness({ phone: false, settingsShowing: true });
    expect(t.ask('a')).toBe('conversation');
    expect(t.showing()).toBe(false);
    expect(t.sent).toEqual([{ type: 'confirm.shown', pending_id: 'a' }]);
  });

  it('closes a board opened only for approvals once the outcome has been seen', () => {
    vi.useFakeTimers();
    const t = harness();
    t.ask('a');
    t.settle('a', 'done');
    expect(t.showing()).toBe(true);
    vi.advanceTimersByTime(OUTCOME_MS + 50);
    expect(t.calls.close).toBe(1);
    expect(t.showing()).toBe(false);
  });

  it('survives a reconnect: the board stays while the cards are gone, and the re-sent card is on it', () => {
    const t = harness();
    t.ask('a');
    t.history();
    expect(t.calls.close).toBe(0);
    expect(t.showing()).toBe(true);
    expect(t.ask('a')).toBe('board');
    expect(t.showing()).toBe(true);
    expect(t.drawn().map((i) => i.card.pendingId)).toEqual(['a']);
    expect(t.sent).toHaveLength(1);
  });

  it('opens the board again for a re-sent card when the board had closed meanwhile', () => {
    const t = harness();
    t.ask('a');
    t.history();
    t.userCloses();
    const shows = t.calls.show;
    expect(t.ask('a')).toBe('board');
    expect(t.calls.show).toBe(shows + 1);
    expect(t.showing()).toBe(true);
  });

  it('puts a waiting card back in the conversation when the user closes the board, without stamping again', () => {
    const t = harness();
    t.ask('a');
    t.userCloses();
    expect(t.a.onBoard('a')).toBe(false);
    expect(t.calls.toConversation).toBe(1);
    t.a.sync();
    expect(t.sent).toHaveLength(1);
    // Announced again later, it stays in the conversation.
    expect(t.ask('a')).toBe('conversation');
  });

  it('starts afresh for another conversation', () => {
    const t = harness();
    t.ask('a');
    t.a.reset();
    expect(t.a.onBoard('a')).toBe(false);
    expect(t.drawn()).toEqual([]);
  });
});
