import { describe, expect, it } from 'vitest';
import { SIDEBAR } from '../src/layout';
import { NUDGE, higher, nudged, revealForCard, savedRest, savedWidth } from '../src/sidebar';

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

  it('goes to the ends for Home and End', () => {
    expect(nudged(400, 'Home')).toBe(SIDEBAR.min);
    expect(nudged(400, 'End')).toBe(SIDEBAR.max);
  });

  it('ignores other keys', () => {
    expect(nudged(400, 'Enter')).toBeNull();
  });
});

describe('savedRest', () => {
  it('starts collapsed, orb first, when nothing or junk was stored', () => {
    expect(savedRest(null)).toBe('collapsed');
    expect(savedRest('tall')).toBe('collapsed');
  });

  it('keeps a stored rest', () => {
    expect(savedRest('half')).toBe('half');
    expect(savedRest('full')).toBe('full');
  });
});

describe('higher', () => {
  it('raises the sheet to a rest, never lowers it', () => {
    expect(higher('collapsed', 'half')).toBe('half');
    expect(higher('half', 'full')).toBe('full');
    expect(higher('full', 'half')).toBe('full');
    expect(higher('half', 'half')).toBe('half');
  });
});

describe('revealForCard', () => {
  it('shows a hidden desktop sidebar', () => {
    expect(revealForCard(false, true)).toEqual({ unhide: true, raise: null });
  });

  it('leaves a shown desktop sidebar as it is', () => {
    expect(revealForCard(false, false)).toEqual({ unhide: false, raise: null });
  });

  it('raises a phone sheet to half, never unhiding', () => {
    expect(revealForCard(true, false)).toEqual({ unhide: false, raise: 'half' });
  });
});
