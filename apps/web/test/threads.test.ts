import { describe, expect, it } from 'vitest';
import { groupByDay, untitled, type Thread } from '../src/threads';

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
