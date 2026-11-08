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

  it('draws an empty region as nothing at all', () => {
    const world = new MindWorld({ palette: PALETTE });
    const map = smallMind(0);
    world.setData({ ...map, nodes: map.nodes.filter((n) => n.region !== 'agents'), edges: map.edges.filter((e) => !e.target.startsWith('helper:')) });
    expect(world.labels.some((l) => l.region === 'agents' || l.region === 'memory')).toBe(false);
    expect(world.apply({ type: 'helper.started', id: 'h1' })).toBe(false);
    world.dispose();
  });
});
