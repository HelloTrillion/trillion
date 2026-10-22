import { describe, expect, it } from 'vitest';
import { BargeDetector, FRAME_S, Preroll, frameLevel } from '../src/barge';

/** Feed frames; the frame index at which it triggered, or -1. */
function run(d: BargeDetector, frames: readonly (readonly [mic: number, her: number])[]): number {
  for (let i = 0; i < frames.length; i++) {
    const f = frames[i];
    if (f && d.push(f[0], f[1])) return i;
  }
  return -1;
}

const seconds = (s: number): number => Math.round(s / FRAME_S);
// Her voice: a level that rises and falls like speech.
const her = (i: number): number => 0.12 + 0.1 * Math.sin(i / 2);

describe('barge-in: her own voice is not the user', () => {
  it('ignores her voice leaking back, however long she talks', () => {
    const leak = Array.from({ length: seconds(10) }, (_, i) => [0.3 * her(i) + 0.003, her(i)] as const);
    expect(run(new BargeDetector(), leak)).toBe(-1);
  });

  it('ignores a strong leak heard from the very start', () => {
    // Loud speakers: half of her comes back, and the first frame already clears the minimum.
    const leak = Array.from({ length: seconds(6) }, (_, i) => [0.5 * her(i), her(i)] as const);
    expect(run(new BargeDetector(), leak)).toBe(-1);
  });

  it('ignores her loudest syllables arriving a moment late', () => {
    const d = new BargeDetector();
    const frames = Array.from({ length: seconds(8) }, (_, i) => {
      const now = i % 7 === 0 ? 0.45 : 0.08; // a sharp peak every so often
      const before = (i - 1) % 7 === 0 ? 0.45 : 0.08;
      return [0.3 * before, now] as const; // the echo lags her by one frame
    });
    expect(run(d, frames)).toBe(-1);
  });

  it('ignores a cough or a click', () => {
    const d = new BargeDetector();
    run(d, Array.from({ length: seconds(1) }, (_, i) => [0.3 * her(i), her(i)] as const));
    expect(run(d, [[0.4, 0.1], [0.4, 0.1], [0.02, 0.1], [0.02, 0.1]])).toBe(-1);
  });
});

describe('barge-in: the user talking over her', () => {
  it('is noticed within about a quarter of a second', () => {
    const d = new BargeDetector();
    run(d, Array.from({ length: seconds(2) }, (_, i) => [0.3 * her(i), her(i)] as const));
    const over = Array.from({ length: seconds(1) }, (_, i) => [0.3 * her(i) + 0.15, her(i)] as const);
    const at = run(d, over);
    expect(at).toBeGreaterThanOrEqual(3);
    expect(at * FRAME_S).toBeLessThanOrEqual(0.3);
  });

  it('is noticed in a pause between her sentences too', () => {
    const d = new BargeDetector();
    run(d, Array.from({ length: seconds(2) }, (_, i) => [0.3 * her(i), her(i)] as const));
    const at = run(d, Array.from({ length: 8 }, () => [0.1, 0] as const));
    expect(at).toBeGreaterThanOrEqual(3);
    expect(at * FRAME_S).toBeLessThanOrEqual(0.3);
  });

  it('waits out the first half second, while it learns', () => {
    const d = new BargeDetector();
    const loud = Array.from({ length: seconds(1.5) }, () => [0.2, 0.05] as const);
    expect(run(d, loud)).toBeGreaterThan(seconds(0.45));
  });

  it('starts over for each reply', () => {
    const d = new BargeDetector();
    run(d, Array.from({ length: seconds(2) }, () => [0.4, 0.8] as const)); // a loud leak learned
    d.reset();
    run(d, Array.from({ length: seconds(1) }, () => [0.003, 0.1] as const)); // headphones now
    expect(run(d, Array.from({ length: 6 }, () => [0.1, 0.1] as const))).toBe(3);
  });

  it('is noticed even when they started talking as she did', () => {
    // Warming up on the user's own voice must not make them impossible to hear.
    const d = new BargeDetector();
    expect(run(d, Array.from({ length: seconds(2) }, () => [0.2, 0.05] as const))).toBeGreaterThan(0);
  });
});

describe('the frames before it noticed', () => {
  it('keeps the last few, so the first word is not lost', () => {
    const p = new Preroll(3);
    const frames = [1, 2, 3, 4, 5].map((n) => new Int16Array([n]).buffer as ArrayBuffer);
    for (const f of frames) p.push(f);
    expect(p.drain().map((f) => new Int16Array(f)[0])).toEqual([3, 4, 5]);
    expect(p.drain()).toEqual([]);
  });

  it('measures a frame', () => {
    expect(frameLevel(new Int16Array(960).buffer)).toBe(0);
    expect(frameLevel(new Int16Array(960).fill(16384).buffer)).toBeCloseTo(0.5, 3);
    expect(frameLevel(new ArrayBuffer(0))).toBe(0);
  });
});
