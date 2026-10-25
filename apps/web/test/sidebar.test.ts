import { describe, expect, it } from 'vitest';
import { SIDEBAR } from '../src/layout';
import { NUDGE, nudged, savedWidth } from '../src/sidebar';

describe('savedWidth', () => {
  it('starts at the initial width when nothing was stored', () => {
    expect(savedWidth(null)).toBe(SIDEBAR.initial);
  });

  it('starts at the initial width when what was stored is not a number', () => {
    expect(savedWidth('wide')).toBe(SIDEBAR.initial);
  });

  it('keeps a stored width, clamped to the allowed range', () => {
    expect(savedWidth('420')).toBe(420);
    expect(savedWidth('9999')).toBe(SIDEBAR.max);
    expect(savedWidth('10')).toBe(SIDEBAR.min);
  });
});

describe('nudged', () => {
  it('moves the edge by one step for the arrow keys', () => {
    expect(nudged(400, 'ArrowRight')).toBe(400 + NUDGE);
    expect(nudged(400, 'ArrowLeft')).toBe(400 - NUDGE);
  });

  it('stops at the ends', () => {
    expect(nudged(SIDEBAR.max, 'ArrowRight')).toBe(SIDEBAR.max);
    expect(nudged(SIDEBAR.min, 'ArrowLeft')).toBe(SIDEBAR.min);
  });

  it('ignores other keys', () => {
    expect(nudged(400, 'Enter')).toBeNull();
  });
});
