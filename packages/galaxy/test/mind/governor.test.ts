import { describe, expect, it } from 'vitest';
import { IDLE_ROTATE_MS, MindGovernor, shouldAutoRotate } from '../../src/mind/governor';

const run = (g: MindGovernor, fps: number, seconds: number, start = 0): number => {
  let t = start;
  const dt = 1 / fps;
  for (let i = 0; i < seconds * fps; i++) g.sample(dt, (t += dt));
  return t;
};

describe('the governor', () => {
  it('leaves a fast mind alone and ignores one slow frame', () => {
    const g = new MindGovernor();
    let t = run(g, 60, 5);
    g.sample(0.5, (t += 0.5));
    run(g, 60, 5, t);
    expect(g.level).toBe(0);
  });

  it('turns bloom off after three slow seconds, then the particles, and never back', () => {
    const g = new MindGovernor();
    // Three seconds to fill its window of 60 frames, then three seconds low.
    let t = run(g, 20, 5);
    expect(g.level).toBe(0);
    t = run(g, 20, 1.5, t);
    expect(g.level).toBe(1);
    t = run(g, 20, 3.5, t);
    expect(g.level).toBe(2);
    run(g, 60, 10, t);
    expect(g.level).toBe(2);
  });
});

describe('idle turning', () => {
  it('starts after 30 seconds without a touch, never under reduced motion', () => {
    expect(shouldAutoRotate(IDLE_ROTATE_MS - 1, false)).toBe(false);
    expect(shouldAutoRotate(IDLE_ROTATE_MS, false)).toBe(true);
    expect(shouldAutoRotate(10 * IDLE_ROTATE_MS, true)).toBe(false);
  });
});
