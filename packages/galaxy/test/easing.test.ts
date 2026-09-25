import { describe, expect, it } from 'vitest';
import { approach, approachFactor, stepParams } from '../src/easing';
import { ORB_STATES } from '../src/states';

describe('approachFactor', () => {
  it('is 0 for no time and never exceeds 1', () => {
    expect(approachFactor(0.07, 0)).toBe(0);
    expect(approachFactor(0.07, 1000)).toBeLessThanOrEqual(1);
  });

  it('is frame-rate independent', () => {
    let a = 0;
    for (let i = 0; i < 60; i++) a = approach(a, 1, 0.07, 1 / 60);
    let b = 0;
    for (let i = 0; i < 30; i++) b = approach(b, 1, 0.07, 1 / 30);
    expect(Math.abs(a - b)).toBeLessThan(1e-9);
  });
});

describe('stepParams', () => {
  it('converges on the target state', () => {
    let p = ORB_STATES.idle;
    for (let i = 0; i < 600; i++) p = stepParams(p, ORB_STATES.listening, 1 / 60);
    expect(p.opacity).toBeCloseTo(ORB_STATES.listening.opacity, 5);
    expect(p.color[0]).toBeCloseTo(1.0, 5);
  });

  it('moves every numeric field part-way in one frame', () => {
    const p = stepParams(ORB_STATES.idle, ORB_STATES.error, 1 / 60);
    expect(p.opacity).toBeGreaterThan(ORB_STATES.idle.opacity);
    expect(p.opacity).toBeLessThan(ORB_STATES.error.opacity);
  });
});
