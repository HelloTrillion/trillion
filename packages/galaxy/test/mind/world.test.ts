import { describe, expect, it } from 'vitest';
import { InstancedMesh, type Object3D } from 'three';
import { MindWorld } from '../../src/mind/world';
import { PALETTE, smallMind } from './fixture';

const count = (root: Object3D, test: (o: Object3D) => boolean): number => {
  let n = 0;
  root.traverse((o) => void (test(o) && n++));
  return n;
};

describe('the scene, built without a screen', () => {
  it('draws every node, one instanced mesh per region, and disposes cleanly', () => {
    const world = new MindWorld({ palette: PALETTE, seed: 2 });
    const map = smallMind(20);
    world.setData(map);
    for (const n of map.nodes) expect(world.has(n.id), n.id).toBe(true);
    // Memory, working, knowledge and rim beads (helpers and her star are meshes of their own).
    expect(count(world.scene, (o) => (o as InstancedMesh).isInstancedMesh === true && Array.isArray(o.userData['ids']))).toBe(4);
    expect(world.labels.map((l) => l.text)).toEqual(expect.arrayContaining(['Nova', 'helper:h1', 'Memory']));
    world.update(0.016);
    world.dispose();
  });

  it('fires real pulses, drops ones naming absent nodes, and brightens under reduced motion', () => {
    let refetched = 0;
    const world = new MindWorld({ palette: PALETTE, onRefetch: () => refetched++ });
    world.setData(smallMind());
    expect(world.apply({ type: 'tool.start', name: 'web_search' })).toBe(true);
    expect(world.pulsesInFlight).toBe(1);
    expect(world.apply({ type: 'tool.start', name: 'absent' })).toBe(false);
    expect(world.pulsesInFlight).toBe(1);
    for (let i = 0; i < 100; i++) world.update(0.02);
    expect(world.pulsesInFlight).toBe(0);
    expect(world.apply({ type: 'memory.written', id: 'mem_new' })).toBe(true);
    expect(refetched).toBe(1);

    world.setReducedMotion(true);
    world.update(0.016);
    expect(world.apply({ type: 'memory.recalled', ids: ['m1'] })).toBe(true);
    expect(world.pulsesInFlight).toBe(0);
    world.dispose();
  });

  it('hides a region from picking and drops events into it', () => {
    const world = new MindWorld({ palette: PALETTE });
    world.setData(smallMind());
    const before = world.pickTargets().length;
    world.setRegionVisible('rim', false);
    expect(world.pickTargets().length).toBeLessThan(before);
    expect(world.apply({ type: 'tool.start', name: 'web_search' })).toBe(false);
    world.setRegionVisible('rim', true);
    expect(world.apply({ type: 'tool.start', name: 'web_search' })).toBe(true);
    world.dispose();
  });

  it('puts each region label just above its own cluster', () => {
    const world = new MindWorld({ palette: PALETTE });
    world.setData(smallMind(30));
    for (const label of world.labels.filter((l) => l.kind === 'region')) {
      const box = world.regionBox(label.region)!;
      expect(label.at.y).toBeGreaterThan(box.max.y);
      expect(label.at.y).toBeLessThan(box.max.y + 2);
      expect(label.at.x).toBeGreaterThanOrEqual(box.min.x);
      expect(label.at.x).toBeLessThanOrEqual(box.max.x);
    }
    world.dispose();
  });

  it('frames a bigger node from further back, and the whole mind in one sphere', () => {
    const world = new MindWorld({ palette: PALETTE });
    const map = smallMind(10);
    world.setData({ ...map, nodes: map.nodes.map((n) => (n.id === 'helper:h1' ? { ...n, size: 3 } : n)) });
    expect(world.nodeRadius('helper:h1')).toBeGreaterThan(world.nodeRadius('mem:m1'));
    expect(world.focusRadius('helper:h1')).toBeGreaterThanOrEqual(world.nodeRadius('helper:h1') * 4);
    expect(world.focusRadius('mem:m1')).toBeGreaterThanOrEqual(2.5);
    const all = world.bounds()!;
    for (const n of map.nodes) expect(all.containsPoint(world.positionOf(n.id)!), n.id).toBe(true);
    world.setRegionVisible('rim', false);
    expect(world.bounds(true)!.radius).toBeLessThanOrEqual(all.radius);
    world.dispose();
  });

  it('draws an empty region as nothing at all', () => {
    const world = new MindWorld({ palette: PALETTE });
    const map = smallMind(0);
    world.setData({ ...map, nodes: map.nodes.filter((n) => n.region !== 'agents'), edges: map.edges.filter((e) => !e.target.startsWith('helper:')) });
    expect(world.labels.some((l) => l.region === 'agents' || l.region === 'memory')).toBe(false);
    expect(world.apply({ type: 'helper.started', id: 'h1' })).toBe(false);
    world.dispose();
  });
});
