import { describe, expect, it } from 'vitest';
import { PerfGovernor, motionScale, qualityFor } from '../src/perf';

describe('PerfGovernor', () => {
  it('turns on after 3s below 45fps, not before', () => {
    const g = new PerfGovernor();
    expect(g.sample(30, 0)).toBe(false);
    expect(g.sample(30, 2900)).toBe(false);
    expect(g.sample(30, 3000)).toBe(true);
  });

  it('forgives a brief dip', () => {
    const g = new PerfGovernor();
    g.sample(30, 0);
    g.sample(60, 1000);
    expect(g.sample(30, 3500)).toBe(false);
  });

  it('latches once on, so quality does not flap', () => {
    const g = new PerfGovernor();
    g.sample(30, 0);
    g.sample(30, 3000);
    expect(g.sample(120, 60_000)).toBe(true);
  });

  it('lets a manual choice override auto in both directions', () => {
    const g = new PerfGovernor();
    g.setMode('on');
    expect(g.sample(120, 0)).toBe(true);
    g.setMode('off');
    g.sample(10, 0);
    expect(g.sample(10, 10_000)).toBe(false);
    g.setMode('auto');
    expect(g.sample(10, 20_000)).toBe(true);
  });
});

describe('qualityFor', () => {
  it('caps pixel ratio at 2 and drops to 1 in perf mode', () => {
    expect(qualityFor(false, 3).pixelRatio).toBe(2);
    expect(qualityFor(true, 3).pixelRatio).toBe(1);
  });

  it('thins detail, dust and nodes in perf mode', () => {
    const full = qualityFor(false, 1);
    const lite = qualityFor(true, 1);
    expect(full.orbDetail).toBe(24);
    expect(lite.orbDetail).toBeLessThan(full.orbDetail);
    expect(lite.dustCount).toBeLessThan(full.dustCount);
    expect(lite.nodeCount).toBeLessThan(full.nodeCount);
  });
});

describe('motionScale', () => {
  it('slows everything under prefers-reduced-motion', () => {
    expect(motionScale(false)).toBe(1);
    expect(motionScale(true)).toBeLessThan(0.5);
  });
});
