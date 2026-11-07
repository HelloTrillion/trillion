import { describe, expect, it } from 'vitest';
import {
  ARC_MS, arcControl, arcFade, arcGlow, arcHead, arcPoints, arcStart, arcsAllowed, ribbon,
} from '../src/arc';
import { ORB_EXTENT } from '../src/stage';

describe('arcsAllowed', () => {
  it('only with full detail and full motion', () => {
    expect(arcsAllowed(false, false)).toBe(true);
    expect(arcsAllowed(true, false)).toBe(false);
    expect(arcsAllowed(false, true)).toBe(false);
  });
});

describe('arcStart', () => {
  const stage = { x: 800, y: 400, size: 350 };
  const core = 350 / ORB_EXTENT;

  it('leaves from the core edge facing the target', () => {
    const p = arcStart(stage, { x: 100, y: 400 });
    expect(p?.x).toBeCloseTo(800 - core);
    expect(p?.y).toBeCloseTo(400);
  });

  it('works on a diagonal', () => {
    const p = arcStart(stage, { x: 800 - 300, y: 400 - 400 });
    expect(p?.x).toBeCloseTo(800 - core * 0.6);
    expect(p?.y).toBeCloseTo(400 - core * 0.8);
  });

  it('is null for a target inside the core or not a number', () => {
    expect(arcStart(stage, { x: 810, y: 400 })).toBeNull();
    expect(arcStart(stage, { x: Number.NaN, y: 0 })).toBeNull();
  });
});

describe('arcPoints', () => {
  it('runs from start to end and bows upward', () => {
    const from = { x: 700, y: 400 };
    const to = { x: 100, y: 400 };
    const pts = arcPoints(from, to, 10);
    expect(pts).toHaveLength(11);
    expect(pts[0]).toEqual(from);
    expect(pts[10]).toEqual(to);
    // Up on screen is smaller y.
    expect(pts[5]?.y).toBeLessThan(400);
  });

  it('bows upward whichever way it goes', () => {
    expect(arcControl({ x: 100, y: 400 }, { x: 700, y: 400 }).y).toBeLessThan(400);
    expect(arcControl({ x: 700, y: 400 }, { x: 100, y: 400 }).y).toBeLessThan(400);
    // A vertical arc bows sideways, consistently.
    const c = arcControl({ x: 400, y: 600 }, { x: 400, y: 100 });
    expect(c.y).toBeCloseTo(350);
    expect(c.x).not.toBeCloseTo(400);
  });
});

describe('ribbon', () => {
  it('two vertices per point, two triangles per segment, width apart', () => {
    const r = ribbon([{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 20, y: 0 }], 2);
    expect(r.positions).toHaveLength(18);
    expect(r.index).toHaveLength(12);
    expect(Array.from(r.along)).toEqual([0, 0, 0.5, 0.5, 1, 1]);
    expect(Math.abs((r.positions[1] ?? 0) - (r.positions[4] ?? 0))).toBeCloseTo(2);
  });
});

describe('timing', () => {
  it('the head travels, then the arc fades and is gone by ARC_MS', () => {
    expect(arcHead(0)).toBe(0);
    expect(arcHead(ARC_MS * 0.3)).toBeCloseTo(0.5);
    expect(arcHead(ARC_MS)).toBe(1);
    expect(arcFade(ARC_MS * 0.5)).toBe(1);
    expect(arcFade(ARC_MS * 0.8)).toBeCloseTo(0.5);
    expect(arcFade(ARC_MS)).toBe(0);
  });

  it('nothing ahead of the head, brightest at it', () => {
    expect(arcGlow(0.9, 0.5)).toBe(0);
    expect(arcGlow(0.5, 0.5)).toBe(1);
    expect(arcGlow(0.2, 0.5)).toBeLessThan(0.2);
  });
});
