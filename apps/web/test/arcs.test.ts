import { describe, expect, it } from 'vitest';
import { type ArcScene, arcTarget, orbSeen } from '../src/arcs';

const viewport = { w: 1400, h: 900 };
const scene = (over: Partial<ArcScene> = {}): ArcScene => ({
  // A chip in the sidebar's transcript, the orb in the stage to its right.
  chip: { left: 40, top: 300, right: 200, bottom: 324 },
  clip: { left: 24, top: 80, right: 372, bottom: 800 },
  viewport,
  canvas: { left: 0, top: 0, right: 1400, bottom: 900 },
  orb: { x: 880, y: 450 },
  ...over,
});

describe('arcTarget', () => {
  it('lands on the point of the chip nearest the orb', () => {
    expect(arcTarget(scene())).toEqual({ x: 200, y: 324 });
  });

  it('lands inside the chip when the orb is level with it', () => {
    expect(arcTarget(scene({ orb: { x: 880, y: 310 } }))).toEqual({ x: 200, y: 310 });
  });

  it('is measured from the canvas', () => {
    expect(arcTarget(scene({ canvas: { left: 10, top: 20, right: 1410, bottom: 920 } }))).toEqual({ x: 190, y: 304 });
  });

  it('is null when the orb cannot be seen', () => {
    expect(arcTarget(scene({ orb: null }))).toBeNull();
  });

  it('is null with no chip, or an empty one (a hidden sidebar)', () => {
    expect(arcTarget(scene({ chip: null }))).toBeNull();
    expect(arcTarget(scene({ chip: { left: 0, top: 0, right: 0, bottom: 0 } }))).toBeNull();
  });

  it('is null when the chip is scrolled out of the transcript', () => {
    expect(arcTarget(scene({ chip: { left: 40, top: 820, right: 200, bottom: 844 } }))).toBeNull();
    expect(arcTarget(scene({ chip: { left: 40, top: 40, right: 200, bottom: 64 } }))).toBeNull();
  });

  it('is null when the chip is outside the window (a phone sheet pulled down)', () => {
    const clip = { left: 0, top: 700, right: 390, bottom: 1600 };
    expect(arcTarget(scene({ viewport: { w: 390, h: 844 }, clip, chip: { left: 16, top: 900, right: 180, bottom: 924 } }))).toBeNull();
  });

  it('still fires when the chip is half cut off but its middle shows', () => {
    expect(arcTarget(scene({ chip: { left: 40, top: 790, right: 200, bottom: 806 } }))).toEqual({ x: 200, y: 790 });
  });
});

describe('orbSeen', () => {
  it('is the centre while the page shows and it is in the window', () => {
    expect(orbSeen({ x: 880, y: 450 }, viewport, false)).toEqual({ x: 880, y: 450 });
  });

  it('is null on a hidden page, off the window, or unplaced', () => {
    expect(orbSeen({ x: 880, y: 450 }, viewport, true)).toBeNull();
    expect(orbSeen({ x: 1500, y: 450 }, viewport, false)).toBeNull();
    expect(orbSeen(null, viewport, false)).toBeNull();
  });
});
