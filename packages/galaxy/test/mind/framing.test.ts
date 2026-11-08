import { describe, expect, it } from 'vitest';
import { fitDistance, freeCentreShift } from '../../src/mind/framing';

describe('framing round the HUD', () => {
  it('stands far enough back for a sphere to fit, further when the HUD takes room', () => {
    const open = fitDistance(10, 55, 1024, 768);
    // At that distance the sphere's half-angle fits the vertical half-angle (the short side).
    expect(Math.asin(10 / open) * 2 * (180 / Math.PI)).toBeLessThan(55);
    expect(fitDistance(10, 55, 1024, 768, { top: 60, right: 0, bottom: 60, left: 0 })).toBeGreaterThan(open);
    // A narrow screen is limited by its width instead.
    expect(fitDistance(10, 55, 400, 800)).toBeGreaterThan(fitDistance(10, 55, 800, 800));
    expect(fitDistance(20, 55, 1024, 768)).toBeCloseTo(open * 2, 6);
    expect(fitDistance(0, 55, 1024, 768)).toBe(0);
  });

  it('puts the middle of the picture in the middle of the space beside a panel', () => {
    expect(freeCentreShift(1000, 800)).toEqual({ x: 0, y: 0 });
    expect(freeCentreShift(1000, 800, { top: 0, right: 380, bottom: 0, left: 0 })).toEqual({ x: -190, y: 0 });
    expect(freeCentreShift(1000, 800, { top: 60, right: 0, bottom: 100, left: 0 })).toEqual({ x: 0, y: -20 });
  });
});
