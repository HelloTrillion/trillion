import { describe, expect, it } from 'vitest';
import { age, clock, countdown, isDateOnly, parseWhen, range, relative } from '../src/when';

// Sunday 25 October 2026, 2:05 pm, in whatever zone the tests run in.
const NOW = new Date(2026, 9, 25, 14, 5);
const local = (d: number, h: number, m = 0): Date => new Date(2026, 9, d, h, m);

describe('parseWhen', () => {
  it('reads a time without an offset as local', () => {
    expect(parseWhen('2026-10-25T14:30')?.getTime()).toBe(local(25, 14, 30).getTime());
    expect(parseWhen('2026-10-25T14:30:15')?.getSeconds()).toBe(15);
  });

  it('reads Z and an offset as absolute', () => {
    expect(parseWhen('2026-10-25T14:30:00Z')?.getTime()).toBe(Date.UTC(2026, 9, 25, 14, 30));
    expect(parseWhen('2026-10-25T14:30-07:00')?.getTime()).toBe(Date.UTC(2026, 9, 25, 21, 30));
    expect(parseWhen('2026-10-25T14:30:00.250+05:30')?.getTime()).toBe(Date.UTC(2026, 9, 25, 9, 0, 0, 250));
  });

  it('reads a bare date as local midnight', () => {
    expect(parseWhen('2026-10-28')?.getTime()).toBe(new Date(2026, 9, 28).getTime());
    expect(isDateOnly('2026-10-28')).toBe(true);
    expect(isDateOnly('2026-10-28T09:00')).toBe(false);
  });

  it('refuses anything else', () => {
    for (const bad of ['', 'tomorrow', '2026-13-01', '2026-02-30', '2026-10-25T25:00', '2026-10-25 14:30', 42, null]) {
      expect(parseWhen(bad)).toBeNull();
    }
  });
});

describe('relative', () => {
  it('counts down to what is soon', () => {
    expect(relative(local(25, 14, 5), NOW)).toBe('now');
    expect(relative(local(25, 14, 30), NOW)).toBe('in 25 min');
    expect(relative(local(25, 16, 15), NOW)).toBe('in 2 h 10 min');
    expect(relative(local(25, 16, 5), NOW)).toBe('in 2 h');
  });

  it('counts up from what just was, or what has started', () => {
    expect(relative(local(25, 14, 0), NOW)).toBe('5 min ago');
    expect(relative(local(25, 14, 0), NOW, true)).toBe('started 5 min ago');
  });

  it('names the day and the time further off', () => {
    expect(relative(local(24, 15), NOW)).toBe('yesterday 3:00 pm');
    expect(relative(local(27, 15), NOW)).toBe('Tue 3:00 pm');
    expect(relative(local(26, 9), NOW)).toBe('tomorrow 9:00 am');
    expect(relative(local(12, 15), NOW)).toBe('Oct 12');
    expect(relative(new Date(2027, 0, 4, 9), NOW)).toBe('Jan 4, 2027');
  });

  it('reads strings, and a bare date as a day', () => {
    expect(relative('2026-10-25T14:30', NOW)).toBe('in 25 min');
    expect(relative('2026-10-27', NOW)).toBe('Tue');
    expect(relative('2026-10-25', NOW)).toBe('today');
    expect(relative('soon', NOW)).toBe('');
  });

  it('places an absolute time in the local day', () => {
    const at = new Date(NOW.getTime() + 25 * 60_000).toISOString();
    expect(at.endsWith('Z')).toBe(true);
    expect(relative(at, NOW)).toBe('in 25 min');
  });
});

describe('countdown', () => {
  it('is before, during and after an event', () => {
    expect(countdown('2026-10-25T14:30', '2026-10-25T15:00', NOW)).toBe('in 25 min');
    expect(countdown('2026-10-25T14:00', '2026-10-25T15:00', NOW)).toBe('started 5 min ago');
    expect(countdown('2026-10-25T13:00', '2026-10-25T13:55', NOW)).toBe('ended 10 min ago');
  });
});

describe('range', () => {
  it('shares the half of the day when it can', () => {
    expect(range(local(25, 14, 30), local(25, 15))).toBe('2:30–3:00 pm');
    expect(range(local(25, 11, 30), local(25, 12, 15))).toBe('11:30 am–12:15 pm');
  });

  it('names both days across days', () => {
    expect(range(local(27, 15), local(28, 9))).toBe('Tue 3:00 pm–Wed 9:00 am');
  });

  it('is the time alone without an end, and days for dates', () => {
    expect(range('2026-10-25T09:05')).toBe('9:05 am');
    expect(range('2026-10-28', '2026-10-30')).toBe('Oct 28–30');
    expect(range('2026-10-30', '2026-11-02')).toBe('Oct 30–Nov 2');
  });

  it('writes noon and midnight on a twelve hour clock', () => {
    expect(clock(local(25, 12))).toBe('12:00 pm');
    expect(clock(local(25, 0, 5))).toBe('12:05 am');
  });
});

describe('age', () => {
  it('says how old a card is', () => {
    expect(age(new Date(NOW.getTime() - 20_000), NOW)).toBe('just now');
    expect(age(local(25, 14, 3), NOW)).toBe('2 min ago');
    expect(age(local(25, 13, 0), NOW)).toBe('1 h ago');
    expect(age(local(24, 9), NOW)).toBe('yesterday');
    expect(age(local(21, 9), NOW)).toBe('4 days ago');
    expect(age(local(2, 9), NOW)).toBe('Oct 2');
    expect(age('2026-10-25T14:03', NOW)).toBe('2 min ago');
  });
});
