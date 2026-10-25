import { PerspectiveCamera, Vector3 } from 'three';
import { describe, expect, it } from 'vitest';
import {
  ORB_DISTANCE, ORB_EXTENT, ORB_FOV, defaultRect, easeRect, placeOrb, stepStage,
} from '../src/stage';

function camera(w: number, h: number): PerspectiveCamera {
  const c = new PerspectiveCamera(ORB_FOV, w / h, 0.1, 100);
  c.position.set(0, 0, ORB_DISTANCE);
  c.updateMatrixWorld();
  return c;
}

const px = (c: PerspectiveCamera, p: Vector3, w: number, h: number): [number, number] => {
  const v = p.clone().project(c);
  return [((v.x + 1) / 2) * w, ((1 - v.y) / 2) * h];
};

describe('placeOrb', () => {
  it('puts the orb centre on the requested pixel', () => {
    const c = camera(1200, 800);
    placeOrb(c, { x: 780, y: 400, size: 600 }, 1200, 800);
    const [x, y] = px(c, new Vector3(0, 0, 0), 1200, 800);
    expect(x).toBeCloseTo(780, 0);
    expect(y).toBeCloseTo(400, 0);
  });

  it('places off-centre on both axes', () => {
    const c = camera(1200, 800);
    placeOrb(c, { x: 1104, y: 704, size: 96 }, 1200, 800);
    const [x, y] = px(c, new Vector3(0, 0, 0), 1200, 800);
    expect(x).toBeCloseTo(1104, 0);
    expect(y).toBeCloseTo(704, 0);
  });

  it('makes the outer ring the requested diameter', () => {
    const c = camera(1200, 800);
    placeOrb(c, { x: 1104, y: 704, size: 96 }, 1200, 800);
    const [left] = px(c, new Vector3(-ORB_EXTENT / 2, 0, 0), 1200, 800);
    const [right] = px(c, new Vector3(ORB_EXTENT / 2, 0, 0), 1200, 800);
    expect(right - left).toBeCloseTo(96, 0);
    const [, top] = px(c, new Vector3(0, ORB_EXTENT / 2, 0), 1200, 800);
    const [, bottom] = px(c, new Vector3(0, -ORB_EXTENT / 2, 0), 1200, 800);
    expect(bottom - top).toBeCloseTo(96, 0);
  });
});

describe('easeRect', () => {
  it('jumps when told to, and approaches otherwise', () => {
    const from = { x: 0, y: 0, size: 100 };
    const to = { x: 100, y: 50, size: 200 };
    expect(easeRect(from, to, 0.016, true)).toEqual(to);
    const mid = easeRect(from, to, 0.016, false);
    expect(mid.x).toBeGreaterThan(0);
    expect(mid.x).toBeLessThan(100);
    let r = from;
    for (let i = 0; i < 60; i++) r = easeRect(r, to, 1 / 60, false); // ~1s, well past 300 ms
    expect(r.x).toBeCloseTo(100, 0);
    expect(r.size).toBeCloseTo(200, 0);
  });

  it('is visually there after about 300 ms', () => {
    const to = { x: 100, y: 0, size: 100 };
    let r = { x: 0, y: 0, size: 100 };
    for (let i = 0; i < 18; i++) r = easeRect(r, to, 1 / 60, false);
    expect(r.x).toBeGreaterThan(97);
  });
});

describe('defaultRect', () => {
  // Today's default: centred, zoom min(1, w/h), no lens shift.
  const diameter = (w: number, h: number): number => {
    const c = camera(w, h);
    c.zoom = Math.min(1, w / h);
    c.updateProjectionMatrix();
    const [left] = px(c, new Vector3(-ORB_EXTENT / 2, 0, 0), w, h);
    const [right] = px(c, new Vector3(ORB_EXTENT / 2, 0, 0), w, h);
    return right - left;
  };

  it('matches the projected default orb, landscape and portrait', () => {
    for (const [w, h] of [[1200, 800], [390, 844]] as const) {
      const r = defaultRect(w, h);
      expect(r.x).toBe(w / 2);
      expect(r.y).toBe(h / 2);
      expect(r.size).toBeCloseTo(diameter(w, h), 1);
    }
  });

  it('places exactly where the default does', () => {
    const plain = camera(390, 844);
    plain.zoom = 390 / 844;
    plain.updateProjectionMatrix();
    const placed = camera(390, 844);
    placeOrb(placed, defaultRect(390, 844), 390, 844);
    const p = new Vector3(1.75, 0.4, 0.3);
    const [ax, ay] = px(plain, p, 390, 844);
    const [bx, by] = px(placed, p, 390, 844);
    expect(bx).toBeCloseTo(ax, 3);
    expect(by).toBeCloseTo(ay, 3);
  });
});

describe('stepStage', () => {
  const to = { x: 100, y: 50, size: 200 };

  it('jumps when told to', () => {
    expect(stepStage({ x: 0, y: 0, size: 10 }, to, 1 / 60, true)).toBe(to);
  });

  it('snaps to the target once within 0.1 px', () => {
    expect(stepStage({ x: 99.95, y: 50.05, size: 199.95 }, to, 1 / 60, false)).toBe(to);
    const far = stepStage({ x: 99, y: 50, size: 200 }, to, 1 / 60, false);
    expect(far).not.toBe(to);
    expect(far.x).toBeGreaterThan(99);
  });

  it('returns the same object once settled', () => {
    let r = { x: 0, y: 0, size: 10 };
    for (let i = 0; i < 120; i++) r = stepStage(r, to, 1 / 60, false);
    expect(r).toBe(to);
    expect(stepStage(r, to, 1 / 60, false)).toBe(r);
  });
});
