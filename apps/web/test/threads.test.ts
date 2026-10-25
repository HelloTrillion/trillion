import { describe, expect, it } from 'vitest';
import {
  appended,
  escapeAction,
  cursorOf,
  groupByDay,
  listPath,
  moved,
  putBack,
  retitle,
  rowTime,
  threadsOf,
  untitled,
  without,
  type Thread,
} from '../src/threads';

const day = (iso: string): number => new Date(iso).getTime();
const t = (id: string, at: string): Thread => ({ id, title: id, lastMessageAt: day(at) });

describe('groupByDay', () => {
  const now = day('2026-10-24T15:00:00');
  it('sorts threads into today, yesterday, the last week and older', () => {
    const groups = groupByDay(
      [t('a', '2026-10-24T09:00:00'), t('b', '2026-10-23T23:59:00'), t('c', '2026-10-20T12:00:00'), t('d', '2026-09-01T12:00:00')],
      now,
    );
    expect(groups.map((g) => [g.label, g.threads.map((x) => x.id)])).toEqual([
      ['Today', ['a']], ['Yesterday', ['b']], ['Previous 7 days', ['c']], ['Older', ['d']],
    ]);
  });

  it('leaves out empty groups', () => {
    expect(groupByDay([t('a', '2026-10-24T01:00:00')], now).map((g) => g.label)).toEqual(['Today']);
  });

  it('uses local midnight', () => {
    expect(groupByDay([t('a', '2026-10-24T00:00:01')], day('2026-10-24T23:59:00'))[0]?.label).toBe('Today');
  });
});

describe('untitled', () => {
  it('falls back for a missing or blank title', () => {
    expect(untitled({ id: 'a', title: null, lastMessageAt: 0 })).toBe('New conversation');
    expect(untitled({ id: 'a', title: '  ', lastMessageAt: 0 })).toBe('New conversation');
    expect(untitled({ id: 'a', title: 'The trip', lastMessageAt: 0 })).toBe('The trip');
  });
});

describe('threadsOf', () => {
  it('reads rows and drops malformed ones', () => {
    const body = {
      conversations: [
        { id: 'a', title: 'Trip', last_message_at: 5 },
        { id: 'b', title: null, last_message_at: 4 },
        { id: 7, title: 'x', last_message_at: 3 },
        { id: 'c', title: 'x', last_message_at: '3' },
        null,
        'junk',
      ],
      more: true,
    };
    expect(threadsOf(body)).toEqual({
      threads: [
        { id: 'a', title: 'Trip', lastMessageAt: 5 },
        { id: 'b', title: null, lastMessageAt: 4 },
      ],
      more: true,
    });
  });

  it('treats a non-string title as none', () => {
    expect(threadsOf({ conversations: [{ id: 'a', title: 3, last_message_at: 1 }] }).threads[0]?.title).toBeNull();
  });

  it('defaults more to false and copes with junk', () => {
    expect(threadsOf({ conversations: [] }).more).toBe(false);
    expect(threadsOf({ conversations: [], more: 'yes' }).more).toBe(false);
    expect(threadsOf(null)).toEqual({ threads: [], more: false });
    expect(threadsOf({ conversations: 'x' })).toEqual({ threads: [], more: false });
  });
});

describe('retitle', () => {
  it('changes only the matching row', () => {
    const rows = [t('a', '2026-10-24T09:00:00'), t('b', '2026-10-24T08:00:00')];
    const next = retitle(rows, 'b', 'New name');
    expect(next.map((x) => x.title)).toEqual(['a', 'New name']);
    expect(next[0]).toBe(rows[0]);
    expect(rows[1]?.title).toBe('b');
  });

  it('leaves the rows alone when none matches', () => {
    expect(retitle([t('a', '2026-10-24T09:00:00')], 'z', 'x').map((x) => x.title)).toEqual(['a']);
  });
});

describe('listPath', () => {
  it('asks for page one with the search words', () => {
    expect(listPath('', null)).toBe('/api/conversations?q=');
    expect(listPath('the trip & more', null)).toBe('/api/conversations?q=the+trip+%26+more');
  });

  it('sends both halves of the cursor for a later page', () => {
    expect(listPath('x', { at: 12, id: 'c1' })).toBe('/api/conversations?q=x&before=12&before_id=c1');
  });

  it('trims the search and keeps it within what the core accepts', () => {
    expect(listPath('  hi  ', null)).toBe('/api/conversations?q=hi');
    expect(new URLSearchParams(listPath('a'.repeat(300), null).split('?')[1]).get('q')).toHaveLength(200);
  });
});

describe('cursorOf', () => {
  it('is the last row, or nothing', () => {
    expect(cursorOf([])).toBeNull();
    const rows = [t('a', '2026-10-24T09:00:00'), t('b', '2026-10-24T08:00:00')];
    expect(cursorOf(rows)).toEqual({ at: rows[1]!.lastMessageAt, id: 'b' });
  });
});

describe('appended', () => {
  it('adds a page, skipping rows already shown', () => {
    const a = t('a', '2026-10-24T09:00:00');
    const b = t('b', '2026-10-24T08:00:00');
    const c = t('c', '2026-10-24T07:00:00');
    expect(appended([a, b], [b, c]).map((x) => x.id)).toEqual(['a', 'b', 'c']);
  });
});

describe('without and putBack', () => {
  const rows = ['a', 'b', 'c'].map((id) => t(id, '2026-10-24T09:00:00'));
  it('takes a row out and remembers where it was', () => {
    const out = without(rows, 'b');
    expect(out?.threads.map((x) => x.id)).toEqual(['a', 'c']);
    expect(out?.index).toBe(1);
    expect(out?.thread.id).toBe('b');
    expect(without(rows, 'z')).toBeNull();
  });

  it('puts it back where it was, or at the end if the list shrank', () => {
    const out = without(rows, 'b')!;
    expect(putBack(out.threads, out.thread, out.index).map((x) => x.id)).toEqual(['a', 'b', 'c']);
    expect(putBack([], out.thread, 5).map((x) => x.id)).toEqual(['b']);
    expect(putBack(rows, rows[0]!, 2).map((x) => x.id)).toEqual(['a', 'b', 'c']);
  });
});

describe('moved', () => {
  const ids = ['a', 'b', 'c'];
  it('moves down and up, stopping at the ends', () => {
    expect(moved(ids, 'a', 'ArrowDown')).toBe('b');
    expect(moved(ids, 'c', 'ArrowDown')).toBe('c');
    expect(moved(ids, 'b', 'ArrowUp')).toBe('a');
    expect(moved(ids, 'a', 'ArrowUp')).toBe('a');
  });

  it('goes to the ends with Home and End', () => {
    expect(moved(ids, 'b', 'Home')).toBe('a');
    expect(moved(ids, 'b', 'End')).toBe('c');
  });

  it('starts at the top when nothing is active', () => {
    expect(moved(ids, null, 'ArrowDown')).toBe('a');
    expect(moved(ids, 'gone', 'ArrowUp')).toBe('a');
  });

  it('ignores other keys and an empty list', () => {
    expect(moved(ids, 'a', 'Enter')).toBeNull();
    expect(moved([], null, 'ArrowDown')).toBeNull();
  });
});

describe('rowTime', () => {
  const now = day('2026-10-24T15:00:00');
  it('shows the time for today and the date before that', () => {
    const today = day('2026-10-24T09:05:00');
    const older = day('2026-10-20T09:05:00');
    expect(rowTime(today, now)).toBe(new Date(today).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }));
    expect(rowTime(older, now)).toBe(new Date(older).toLocaleDateString([], { month: 'short', day: 'numeric' }));
  });
});

describe('escapeAction', () => {
  const idle = { busy: false, speaking: false, listening: false, listOpen: false, workspaceOpen: false };
  it('stops her first, whatever else is open', () => {
    expect(escapeAction({ ...idle, busy: true, listOpen: true, workspaceOpen: true })).toBe('interrupt');
    expect(escapeAction({ ...idle, speaking: true })).toBe('interrupt');
    expect(escapeAction({ ...idle, listening: true, listOpen: true })).toBe('interrupt');
  });

  it('then closes the thread list, then the workspace', () => {
    expect(escapeAction({ ...idle, listOpen: true, workspaceOpen: true })).toBe('list');
    expect(escapeAction({ ...idle, workspaceOpen: true })).toBe('workspace');
  });

  it('does nothing when nothing is open or under way', () => {
    expect(escapeAction(idle)).toBeNull();
  });
});
