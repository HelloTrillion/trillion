import { PerspectiveCamera, Vector3 } from 'three';
import { describe, expect, it } from 'vitest';
import { ORB_DISTANCE, ORB_EXTENT, easeRect, placeOrb } from '../src/stage';

function camera(w: number, h: number): PerspectiveCamera {
  const c = new PerspectiveCamera(45, w / h, 0.1, 100);
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
