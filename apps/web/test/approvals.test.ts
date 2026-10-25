import { describe, expect, it } from 'vitest';
import {
  OUTCOME_MS,
  approvalsOf,
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

  it('lists the cards on the board, newest first', () => {
    const ids = approvalsOf(entries, new Set(['a', 'b', 'c']), new Set()).map((c) => c.pendingId);
    expect(ids).toEqual(['c', 'b', 'a']);
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
    expect(outcomeWords('closed', EXPIRED_NOTE)).toBe("Expired · she'll ask again");
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
