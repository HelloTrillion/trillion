import { describe, expect, it } from 'vitest';
import { Mesh } from 'three';
import { SILENT } from '../src/audio/levels';
import { Orb } from '../src/orb/orb';
import { ORB_STATES } from '../src/states';

const frame = (dt: number) => ({
  params: ORB_STATES.processing, levels: SILENT, time: 0, dt, flare: 0, motion: 1,
});

describe('ring lights', () => {
  it('orbit on their rings while processing, so the rings show work', () => {
    const orb = new Orb(1);
    const lights = () =>
      orb.group.children.flatMap((c) => c.children).filter((c): c is Mesh => c instanceof Mesh);
    orb.update(frame(0));
    const before = lights().map((l) => l.position.clone());
    orb.update(frame(0.5));
    const after = lights();

    expect(after).toHaveLength(3);
    after.forEach((l, i) => {
      const ring = l.parent as Mesh;
      const radius = (ring.geometry as unknown as { parameters: { radius: number } }).parameters.radius;
      expect(l.position.length()).toBeCloseTo(radius, 6);
      expect(l.position.distanceTo(before[i]!)).toBeGreaterThan(0.1);
      expect(l.visible && ring.visible).toBe(true);
    });
  });
});
