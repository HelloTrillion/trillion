import { describe, expect, it } from 'vitest';
import { clock, hourLabel, showcaseScene, stepProgress, stepStates, timeOfDay } from '../src/showcase';

describe('showcaseScene', () => {
  it('opens on a bare flag in the fragment or the query', () => {
    expect(showcaseScene('', '#showcase')).toBe('briefing');
    expect(showcaseScene('?showcase', '')).toBe('briefing');
  });

  it('names a scene, and falls back to the first for one it does not know', () => {
    expect(showcaseScene('', '#showcase=decide')).toBe('decide');
    expect(showcaseScene('?showcase=nope', '')).toBe('briefing');
  });

  it('finds the flag among others, and not inside another word', () => {
    expect(showcaseScene('', '#token=abc&showcase=compare')).toBe('compare');
    expect(showcaseScene('?showcases', '#show=google:gmail')).toBeNull();
    expect(showcaseScene('', '')).toBeNull();
  });
});

describe('stepStates', () => {
  const d = [1000, 2000, 500];

  it('runs the steps one after another', () => {
    expect(stepStates(0, d)).toEqual(['running', 'pending', 'pending']);
    expect(stepStates(1000, d)).toEqual(['done', 'running', 'pending']);
    expect(stepStates(3200, d)).toEqual(['done', 'done', 'running']);
    expect(stepStates(3500, d)).toEqual(['done', 'done', 'done']);
  });

  it('measures progress through one step', () => {
    expect(stepProgress(500, d, 0)).toBe(0.5);
    expect(stepProgress(0, d, 1)).toBe(0);
    expect(stepProgress(2000, d, 1)).toBe(0.5);
    expect(stepProgress(9000, d, 2)).toBe(1);
  });
});

describe('clock and timeOfDay', () => {
  it('formats seconds left and times of day', () => {
    expect(clock(300)).toBe('5:00');
    expect(clock(59.2)).toBe('1:00');
    expect(clock(-3)).toBe('0:00');
    expect(timeOfDay(9 * 60 + 42)).toBe('9:42');
    expect(timeOfDay(12 * 60 + 30)).toBe('12:30');
    expect(timeOfDay(15 * 60 + 5)).toBe('3:05');
  });
});

describe('hourLabel', () => {
  it('says am or pm on every hour', () => {
    expect([7, 10, 13, 16, 19].map(hourLabel)).toEqual(['7 am', '10 am', '1 pm', '4 pm', '7 pm']);
    expect(hourLabel(12)).toBe('Noon');
    expect(hourLabel(0)).toBe('12 am');
  });
});
