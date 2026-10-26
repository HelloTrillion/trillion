import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  DAY_OPEN_WAIT_MS,
  appended,
  createDayOpener,
  escapeAction,
  cursorOf,
  groupByDay,
  listPath,
  localDay,
  moved,
  needsBuild,
  putBack,
  retitle,
  rowTime,
  shortDate,
  threadsOf,
  todayIn,
  untitled,
  whenIdle,
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

describe('threadsOf, day conversations', () => {
  it('keeps the date of a day conversation, and none for a chat', () => {
    const body = {
      conversations: [
        { id: 'd', title: 'Your day · Mon 26 Oct', last_message_at: 5, kind: 'day', day: '2026-10-26' },
        { id: 'c', title: 'Trip', last_message_at: 4, kind: 'chat', day: null },
        { id: 'x', title: 'Odd', last_message_at: 3, kind: 'chat', day: '2026-10-26' },
      ],
    };
    expect(threadsOf(body).threads).toEqual([
      { id: 'd', title: 'Your day · Mon 26 Oct', lastMessageAt: 5, day: '2026-10-26' },
      { id: 'c', title: 'Trip', lastMessageAt: 4 },
      { id: 'x', title: 'Odd', lastMessageAt: 3 },
    ]);
  });
});

describe('your day, the pure parts', () => {
  const now = day('2026-10-26T09:05:00');
  it('dates today locally, as the core does', () => {
    expect(localDay(now)).toBe('2026-10-26');
    expect(localDay(day('2026-03-04T23:59:00'))).toBe('2026-03-04');
  });

  it('shows today short', () => {
    const d = new Date(now);
    expect(shortDate(now)).toBe(
      `${d.toLocaleDateString([], { weekday: 'short' })} 26 ${d.toLocaleDateString([], { month: 'short' })}`,
    );
  });

  it("finds today's day conversation among the rows", () => {
    const rows: Thread[] = [
      { id: 'old', title: null, lastMessageAt: 1, day: '2026-10-25' },
      { id: 'chat', title: null, lastMessageAt: 2 },
      { id: 'today', title: null, lastMessageAt: 3, day: '2026-10-26' },
    ];
    expect(todayIn(rows, now)).toBe('today');
    expect(todayIn(rows.slice(0, 2), now)).toBeNull();
  });

  it('builds only for no board or an empty one', () => {
    expect(needsBuild(null)).toBe(true);
    expect(needsBuild({ widgets: [] })).toBe(true);
    expect(needsBuild({ widgets: [{ id: 'w' }] })).toBe(false);
  });
});

describe('createDayOpener', () => {
  function setup(body: unknown = { id: 'd1', created: true }, current: string | null = 'c1') {
    const s = { current, switched: [] as string[], builds: 0, fetches: 0 };
    const opener = createDayOpener({
      fetchDay: () => {
        s.fetches++;
        return body instanceof Error ? Promise.reject(body) : Promise.resolve(body);
      },
      current: () => s.current,
      switchTo: (id) => {
        s.switched.push(id);
        s.current = id;
      },
      build: () => s.builds++,
    });
    return { s, opener };
  }

  it('creates, switches, and builds once the new session shows an empty board', async () => {
    const { s, opener } = setup();
    expect(await opener.open()).toBe(true);
    expect(s.switched).toEqual(['d1']);
    expect(opener.id).toBe('d1');
    expect(s.builds).toBe(0);
    opener.session();
    opener.board(null);
    expect(s.builds).toBe(1);
  });

  it('opens a day that has a board without building', async () => {
    const { s, opener } = setup({ id: 'd1', created: false });
    await opener.open();
    opener.session();
    opener.board({ widgets: [{ id: 'w' }] });
    expect(s.switched).toEqual(['d1']);
    expect(s.builds).toBe(0);
  });

  it('builds a day opened earlier with nothing on its board', async () => {
    const { s, opener } = setup({ id: 'd1', created: false });
    await opener.open();
    opener.session();
    opener.board({ widgets: [] });
    expect(s.builds).toBe(1);
  });

  it('builds once per open: a later board or a reconnect builds nothing', async () => {
    const { s, opener } = setup();
    await opener.open();
    opener.session();
    opener.board(null);
    opener.board(null);
    opener.session();
    opener.board(null);
    expect(s.builds).toBe(1);
  });

  it('opened again while in it: no switch, and no rebuild of a built board', async () => {
    const { s, opener } = setup({ id: 'd1', created: false }, 'd1');
    opener.session();
    opener.board({ widgets: [{ id: 'w' }] });
    await opener.open();
    expect(s.switched).toEqual([]);
    expect(s.builds).toBe(0);
  });

  it('opened again while in it with an empty board: builds now', async () => {
    const { s, opener } = setup({ id: 'd1', created: false }, 'd1');
    opener.session();
    opener.board(null);
    await opener.open();
    expect(s.builds).toBe(1);
  });

  it('builds nothing when the session that came is not the day', async () => {
    const { s, opener } = setup();
    await opener.open();
    s.current = 'elsewhere';
    opener.session();
    opener.board(null);
    s.current = 'd1';
    opener.board(null);
    expect(s.builds).toBe(0);
  });

  it('does nothing when the core does not answer, or answers without an id', async () => {
    for (const body of [new Error('offline'), {}, null, { id: '' }]) {
      const { s, opener } = setup(body);
      expect(await opener.open()).toBe(false);
      opener.board(null);
      expect(s.switched).toEqual([]);
      expect(s.builds).toBe(0);
    }
  });
});

describe('whenIdle', () => {
  afterEach(() => vi.useRealTimers());

  it('runs at once when she is already idle', () => {
    let ran = 0;
    whenIdle(() => true, () => ran++);
    expect(ran).toBe(1);
  });

  it('waits for her reply to end, then runs once', () => {
    vi.useFakeTimers();
    let busy = true;
    let ran = 0;
    whenIdle(() => !busy, () => ran++);
    vi.advanceTimersByTime(3000);
    expect(ran).toBe(0);
    busy = false;
    vi.advanceTimersByTime(250);
    expect(ran).toBe(1);
    vi.advanceTimersByTime(5000);
    expect(ran).toBe(1);
  });

  it('runs anyway at the cap', () => {
    vi.useFakeTimers();
    let ran = 0;
    whenIdle(() => false, () => ran++);
    vi.advanceTimersByTime(DAY_OPEN_WAIT_MS - 500);
    expect(ran).toBe(0);
    vi.advanceTimersByTime(500);
    expect(ran).toBe(1);
  });

  it('can be called off', () => {
    vi.useFakeTimers();
    let ran = 0;
    const cancel = whenIdle(() => false, () => ran++);
    cancel();
    vi.advanceTimersByTime(DAY_OPEN_WAIT_MS * 2);
    expect(ran).toBe(0);
  });
});
