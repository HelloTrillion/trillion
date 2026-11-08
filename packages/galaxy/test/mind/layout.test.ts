import { describe, expect, it } from 'vitest';
import type { MindNode } from '../../src/mind/contract';
import { ANCHORS, MEMBRANE_RADIUS, MEMORY_RADIUS, type Vec3, edgeEnd, layoutMemory, layoutMind } from '../../src/mind/layout';
import { smallMind } from './fixture';

const dist = (a: Vec3, b: Vec3): number => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);

describe('layout', () => {
  it('places the same mind in the same place for the same seed, and elsewhere for another', () => {
    const map = smallMind();
    const a = layoutMind(map, 3);
    const b = layoutMind(map, 3);
    const c = layoutMind(map, 4);
    expect([...a.entries()]).toEqual([...b.entries()]);
    expect(a.get('mem:m3')).not.toEqual(c.get('mem:m3'));
  });

  it('places every node, each near its own region and inside the membrane', () => {
    const map = smallMind();
    const pos = layoutMind(map);
    for (const n of map.nodes) {
      const p = pos.get(n.id);
      expect(p, n.id).toBeDefined();
      expect(dist(p!, ANCHORS.core)).toBeLessThan(MEMBRANE_RADIUS);
      // Closer to its own anchor than to any other region's.
      const own = dist(p!, ANCHORS[n.region]);
      for (const [r, anchor] of Object.entries(ANCHORS)) if (r !== n.region) expect(own, `${n.id} vs ${r}`).toBeLessThan(dist(p!, anchor));
    }
    expect(pos.get('core')).toEqual(ANCHORS.core);
  });

  it('settles the memory web and keeps it inside its ball, from 5 memories to 500', () => {
    for (const count of [5, 120, 500]) {
      const nodes: MindNode[] = Array.from({ length: count }, (_, i) => ({
        id: `mem:${i}`, type: 'memory', region: 'memory', label: '', size: 1, freshness: 1, extra: { kind: i % 3 ? 'fact' : 'project' },
      }));
      const edges = nodes.slice(1).map((n, i) => ({ source: n.id, target: `mem:${(i * 7) % (i + 1)}`, kind: 'similarity' as const, weight: 0.7 }));
      for (const es of [edges, []]) {
        const { positions, movement } = layoutMemory(nodes, es);
        expect(positions.size).toBe(count);
        for (const p of positions.values()) {
          expect(dist(p, ANCHORS.memory)).toBeLessThanOrEqual(MEMORY_RADIUS + 1e-9);
          expect(dist(p, ANCHORS.core)).toBeLessThan(MEMBRANE_RADIUS);
        }
        // It converges: the last steps move far less than the first.
        const first = Math.max(...movement.slice(0, 10));
        expect(movement[movement.length - 1]!).toBeLessThan(first * 0.05);
      }
    }
  });

  it('lands an edge on a ring, not at its centre', () => {
    const end = edgeEnd('ring:stable', ANCHORS.core, { x: 10, y: 0, z: 0 });
    expect(end).toEqual({ x: 2.3, y: 0, z: 0 });
    expect(edgeEnd('mem:a', { x: 1, y: 2, z: 3 }, ANCHORS.core)).toEqual({ x: 1, y: 2, z: 3 });
  });
});
