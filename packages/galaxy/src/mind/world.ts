/**
 * The Living Mind as a scene graph: everything that is drawn and how it moves, with no
 * renderer, camera or page. `createMind` puts it on a canvas; tests build it bare.
 */

import { Color, FogExp2, Group, type InstancedMesh, type Object3D, Scene, Vector3 } from 'three';
import { type MindMap, type MindNode, type ObserveEvent, REGION_IDS, type RegionId } from './contract';
import { Drift, EdgeLayer, PulsePool } from './edges';
import type { DegradeLevel } from './governor';
import { ANCHORS, type Vec3, edgeEnd, layoutMind } from './layout';
import { type Core, type GlowSphere, Population, type Shared, buildAccent, buildCore, buildMembrane, buildStarfield, disposeTree, makeGlowSphere, toVector } from './nodes';
import { type MindPalette, type PaletteKey, paletteColors } from './palette';
import { type Effect, type Tint, planEvent } from './pulses';
import { seededRandom } from '../random';

export interface WorldOptions {
  readonly palette: MindPalette;
  readonly seed?: number;
  readonly reducedMotion?: boolean;
  /** A `memory.written` arrived: read the skeleton again and call `setData`. */
  readonly onRefetch?: () => void;
}

export interface Label {
  readonly text: string;
  readonly kind: 'region' | 'helper' | 'core';
  readonly region: RegionId;
  /** The node it names, or null for a region heading. */
  readonly id: string | null;
  readonly at: Vector3;
}

type Entry =
  | { readonly kind: 'instanced'; readonly pop: Population; readonly index: number; readonly node: MindNode }
  | { readonly kind: 'object'; readonly object: Object3D; readonly node: MindNode; readonly glow?: GlowSphere };

interface Flare {
  age: number;
  readonly dur: number;
  readonly boost: number;
  readonly scale: number;
  readonly tint: Color | null;
}

const HALO_BASE = 0.4;
const HALO_ACTIVE = 0.8;
const HALO_SECONDS = 30;
const BLOOM_SECONDS = 0.8;
const MEMBRANE_BASE = 0.06;

export class MindWorld {
  readonly scene = new Scene();
  private readonly colors: Readonly<Record<PaletteKey, Color>>;
  private readonly seed: number;
  private readonly shared: Shared = { uTime: { value: 0 }, uBreath: { value: 1 } };
  private readonly onRefetch: (() => void) | undefined;
  private readonly membrane;
  private readonly stars;
  private readonly pulses = new PulsePool();
  private readonly regionGroups = {} as Record<RegionId, Group>;
  private readonly hidden = new Set<RegionId>();
  private readonly registry = new Map<string, Entry>();
  private readonly populations: Population[] = [];
  private readonly helpers = new Map<string, GlowSphere>();
  private readonly flares = new Map<string, Flare>();
  private readonly halos = new Map<string, number>();
  private readonly blooms = new Map<string, number>();
  private content = new Group();
  private map: MindMap | null = null;
  private positions = new Map<string, Vec3>();
  private core: Core | null = null;
  private edges: EdgeLayer | null = null;
  private drift: Drift | null = null;
  private labelList: Label[] = [];
  private reduced: boolean;
  private level: DegradeLevel = 0;
  private time = 0;
  private ringFlash = 0;
  private rippleAge = Number.POSITIVE_INFINITY;
  private highlighted: string | null = null;

  constructor(opts: WorldOptions) {
    this.colors = paletteColors(opts.palette);
    this.seed = opts.seed ?? 1;
    this.reduced = opts.reducedMotion ?? false;
    this.onRefetch = opts.onRefetch;
    this.scene.background = this.colors.void.clone();
    this.scene.fog = new FogExp2(this.colors.void.clone(), 0.012);
    this.stars = buildStarfield(this.seed, this.shared.uTime);
    this.membrane = buildMembrane(this.colors.core);
    this.scene.add(this.stars.points, this.membrane, this.content, this.pulses.group);
    this.applyMotion();
  }

  /** Draw a skeleton. Nodes new since the last one bloom into place. */
  setData(map: MindMap): void {
    const before = this.map ? new Set(this.map.nodes.map((n) => n.id)) : null;
    this.clearContent();
    this.map = map;
    this.positions = layoutMind(map, this.seed);
    const content = new Group();
    this.content = content;
    this.scene.add(content);
    for (const r of REGION_IDS) {
      const g = new Group();
      g.name = `region:${r}`;
      g.visible = !this.hidden.has(r);
      this.regionGroups[r] = g;
      content.add(g);
    }
    const nodes = map.nodes.filter((n) => this.positions.has(n.id));
    const present = new Set(nodes.map((n) => n.region));
    for (const r of present) for (const s of buildAccent(r, ANCHORS[r], this.colors[r])) this.regionGroups[r].add(s);

    // Her star and its rings.
    const coreNode = nodes.find((n) => n.type === 'core');
    const ringNodes = nodes.filter((n) => n.type === 'ring');
    if (coreNode || ringNodes.length) {
      const core = buildCore(this.colors.core, {
        stable: ringNodes.some((n) => n.id === 'ring:stable'),
        turn: ringNodes.some((n) => n.id === 'ring:turn'),
      });
      this.core = core;
      core.star.group.visible = coreNode !== undefined;
      core.corona.visible = core.coronaOuter.visible = coreNode !== undefined;
      this.regionGroups.core.add(core.group);
      if (coreNode) this.registry.set(coreNode.id, { kind: 'object', object: core.star.core, node: coreNode, glow: core.star });
      for (const n of ringNodes) {
        const mesh = n.id === 'ring:stable' ? core.stableRing : n.id === 'ring:turn' ? core.turnRing : null;
        if (mesh) this.registry.set(n.id, { kind: 'object', object: mesh, node: n });
      }
    }

    // Helpers: glow spheres of their own, with their names beside them.
    const labels: Label[] = [];
    for (const n of nodes.filter((x) => x.type === 'helper')) {
      const glow = makeGlowSphere(this.colors.agents, 0.55, HALO_BASE);
      glow.group.position.copy(toVector(this.at(n.id)));
      glow.core.userData['nodeId'] = n.id;
      this.regionGroups[n.region].add(glow.group);
      this.helpers.set(n.id, glow);
      this.registry.set(n.id, { kind: 'object', object: glow.group, node: n, glow });
      labels.push({ text: n.label, kind: 'helper', region: n.region, id: n.id, at: toVector(this.at(n.id)).add(new Vector3(0, -1.2, 0)) });
    }

    // Everything else: one instanced population per region.
    for (const r of REGION_IDS) {
      const small = nodes.filter((n) => n.region === r && n.type !== 'core' && n.type !== 'ring' && n.type !== 'helper');
      if (!small.length) continue;
      const pop = new Population(small, (id) => this.at(id), this.colors[r], this.shared);
      this.populations.push(pop);
      this.regionGroups[r].add(pop.mesh, pop.halo);
      small.forEach((node, index) => this.registry.set(node.id, { kind: 'instanced', pop, index, node }));
    }

    this.edges = new EdgeLayer(
      map.edges,
      (id, other) => (this.positions.has(id) && this.positions.has(other) ? toVector(edgeEnd(id, this.at(id), this.at(other))) : null),
      (id) => this.registry.get(id)?.node.region,
      this.colors,
      this.shared.uTime,
    );
    content.add(this.edges.group);

    if (present.has('working') && present.has('memory')) {
      this.drift = new Drift(toVector(ANCHORS.working), toVector(ANCHORS.memory), this.colors.working, seededRandom(this.seed + 7));
      this.regionGroups.memory.add(this.drift.points);
    }

    // Region headings (her own star is named by her name) and helper names.
    const regionLabel = new Map(map.regions.map((r) => [r.id, r.label]));
    for (const r of present) {
      if (r === 'core') {
        if (coreNode && map.agent) labels.push({ text: map.agent, kind: 'core', region: r, id: coreNode.id, at: new Vector3(0, -2.2, 0) });
        continue;
      }
      labels.push({ text: regionLabel.get(r) ?? r, kind: 'region', region: r, id: null, at: toVector(ANCHORS[r]).add(new Vector3(0, r === 'agents' ? 11 : 6, 0)) });
    }
    this.labelList = labels;

    if (before) for (const n of nodes) if (!before.has(n.id) && n.region !== 'core') this.blooms.set(n.id, 0);
    this.applyMotion();
    this.applyVisibility();
  }

  get data(): MindMap | null {
    return this.map;
  }

  get labels(): readonly Label[] {
    return this.labelList;
  }

  has(id: string): boolean {
    return this.registry.has(id);
  }

  node(id: string): MindNode | undefined {
    return this.registry.get(id)?.node;
  }

  /** Where a node is now, or null when it isn't drawn. */
  positionOf(id: string): Vector3 | null {
    const p = this.positions.get(id);
    return p && this.registry.has(id) ? toVector(p) : null;
  }

  /** A region's group, where its labels belong so they hide with it. Rebuilt by `setData`. */
  regionGroup(r: RegionId): Group | undefined {
    return this.regionGroups[r];
  }

  regionVisible(r: RegionId): boolean {
    return !this.hidden.has(r);
  }

  setRegionVisible(r: RegionId, visible: boolean): void {
    if (visible) this.hidden.delete(r);
    else this.hidden.add(r);
    this.applyVisibility();
  }

  /** What hover and click may hit: only what is shown. */
  pickTargets(): Object3D[] {
    const out: Object3D[] = [];
    for (const r of REGION_IDS) {
      if (this.hidden.has(r) || !this.regionGroups[r]) continue;
      this.regionGroups[r].traverseVisible((o) => {
        if ((o as InstancedMesh).isInstancedMesh && o.userData['ids']) out.push(o);
        else if (typeof o.userData['nodeId'] === 'string') out.push(o);
      });
    }
    return out;
  }

  /** The node a ray hit, from the object and (for a population) its instance. */
  idFromHit(object: Object3D, instanceId: number | undefined): string | null {
    const ids = object.userData['ids'] as readonly string[] | undefined;
    const id = ids && instanceId !== undefined ? ids[instanceId] : (object.userData['nodeId'] as string | undefined);
    return id && this.registry.has(id) ? id : null;
  }

  /** Light one node's edges (null clears). False when it has none showing. */
  highlight(id: string | null): boolean {
    this.highlighted = id;
    return this.edges?.highlight(id, (r) => !this.hidden.has(r)) ?? false;
  }

  setReducedMotion(on: boolean): void {
    this.reduced = on;
    this.applyMotion();
  }

  /** The governor's verdict: 2 turns off particles and travelling pulses. */
  setDegrade(level: DegradeLevel): void {
    this.level = level;
    this.applyMotion();
  }

  /** Carry out an observe event. False when it was dropped (it named nothing on the page). */
  apply(event: ObserveEvent): boolean {
    const effects = this.plan(event);
    for (const e of effects) this.run(e);
    return effects.length > 0;
  }

  /** The effects an event would have, given what is drawn and shown now. */
  plan(event: ObserveEvent): Effect[] {
    return planEvent(event, {
      has: (id) => {
        const n = this.registry.get(id)?.node;
        return n !== undefined && !this.hidden.has(n.region);
      },
      hasRegion: (r) => !this.hidden.has(r) && this.map?.nodes.some((n) => n.region === r) === true,
      reducedMotion: this.reduced || this.level >= 2,
    });
  }

  get pulsesInFlight(): number {
    return this.pulses.inFlight;
  }

  private tint(t: Tint): Color {
    return this.colors[t];
  }

  private endpoint(e: string): Vector3 | null {
    if (e.startsWith('region:')) {
      const r = e.slice(7) as RegionId;
      return r in ANCHORS ? toVector(ANCHORS[r]) : null;
    }
    return this.positionOf(e);
  }

  private flare(id: string, boost: number, scale: number, dur: number, tint: Color | null): void {
    if (!this.registry.has(id)) return;
    this.flares.set(id, { age: 0, dur, boost, scale: this.reduced ? 1 : scale, tint });
  }

  private run(e: Effect): void {
    switch (e.kind) {
      case 'travel': {
        const a = this.endpoint(e.from);
        const b = this.endpoint(e.to);
        if (!a || !b) return;
        const color = this.tint(e.tint);
        if (e.flareFrom) this.flare(e.from, 1.2, 2.2, 1.0, color);
        this.pulses.fire(this.edges?.curves ?? new Map(), e.from, e.to, a, b, color, () => this.flare(e.to, 0.8, 1.8, 0.6, color));
        return;
      }
      case 'brighten':
        this.flare(e.id, 1.2, 1, 0.9, this.tint(e.tint));
        return;
      case 'halo':
        this.halos.set(e.id, e.on ? HALO_SECONDS : Math.min(this.halos.get(e.id) ?? 0, 3));
        return;
      case 'ring':
        this.ringFlash = 1;
        return;
      case 'ripple':
        this.rippleAge = 0;
        return;
      case 'refetch':
        this.onRefetch?.();
        return;
    }
  }

  /** Advance one frame. `dt` in seconds. */
  update(dt: number): void {
    const still = this.reduced;
    this.time += dt;
    this.shared.uTime.value = this.time;
    const t = this.time;
    const core = this.core;
    if (core) {
      if (!still) {
        core.stablePivot.rotation.y += 0.09 * dt;
        core.turnPivot.rotation.y += 0.24 * dt;
        core.corona.material.rotation += dt * 0.05;
        core.coronaOuter.material.rotation -= dt * 0.03;
      }
      this.ringFlash = Math.max(0, this.ringFlash - dt / 1.2);
      const ringBase = still ? 0.25 : 0.25 + 0.08 * Math.sin(t * 1.6);
      if (core.turnMaterial) core.turnMaterial.opacity = ringBase + (0.9 - ringBase) * this.ringFlash;
      // Her star breathes, and swells with each turn as the ring flashes.
      core.nucleus.scale.setScalar(1 + (still ? 0 : 0.025 * Math.sin(t * 0.9) + 0.16 * this.ringFlash));
      core.corona.material.opacity = 0.5 + 0.35 * this.ringFlash;
    }

    const mem = this.membrane.material as unknown as { uniforms: { uOpacity: { value: number } } };
    if (this.rippleAge < 2) {
      this.rippleAge += dt;
      mem.uniforms.uOpacity.value = MEMBRANE_BASE + 0.16 * Math.sin(Math.PI * Math.min(this.rippleAge / 2, 1));
    } else mem.uniforms.uOpacity.value = MEMBRANE_BASE;

    if (this.drift && this.level < 2 && !still) this.drift.update(dt);
    this.pulses.update(dt);

    for (const [id, f] of this.flares) {
      f.age += dt;
      const k = f.age >= f.dur ? 0 : 1 - f.age / f.dur;
      this.setLook(id, 1 + (f.scale - 1) * k, f.boost * k, f.tint);
      if (k === 0) this.flares.delete(id);
    }
    for (const [id, age] of this.blooms) {
      const next = age + dt;
      const k = Math.min(1, next / BLOOM_SECONDS);
      // A new memory grows from nothing; under reduced motion it fades up instead.
      if (still) this.setLook(id, 1, (1 - k) * 1.2, null);
      else this.setLook(id, k, 0, null);
      if (k >= 1) this.blooms.delete(id);
      else this.blooms.set(id, next);
    }
    for (const [id, left] of this.halos) {
      const next = left - dt;
      const glow = this.helpers.get(id);
      if (glow) glow.haloMaterial.uniforms['uOpacity']!.value = next > 0 ? HALO_ACTIVE : HALO_BASE;
      if (next > 0) this.halos.set(id, next);
      else this.halos.delete(id);
    }
  }

  private setLook(id: string, scale: number, boost: number, tint: Color | null): void {
    const e = this.registry.get(id);
    if (!e) return;
    if (e.kind === 'instanced') {
      e.pop.setScale(e.index, scale);
      e.pop.setBoost(e.index, boost, tint);
      return;
    }
    const target = e.glow ? e.glow.group : e.object;
    if (e.node.type !== 'core') target.scale.setScalar(scale);
    if (e.glow) e.glow.coreMaterial.uniforms['uBoost']!.value = boost;
  }

  private at(id: string): Vec3 {
    return this.positions.get(id) ?? ANCHORS.core;
  }

  private applyMotion(): void {
    const moving = !this.reduced && this.level < 2;
    this.shared.uBreath.value = this.reduced ? 0 : 1;
    this.edges?.setMotion(moving);
    if (this.drift) this.drift.points.visible = moving;
    this.pulses.enabled = moving;
    if (!moving) this.pulses.clear();
  }

  private applyVisibility(): void {
    for (const r of REGION_IDS) if (this.regionGroups[r]) this.regionGroups[r].visible = !this.hidden.has(r);
    this.edges?.setRegionsVisible((r) => !this.hidden.has(r));
    // Pulses fly at the scene root; one over a hidden region would cross empty space.
    this.pulses.clear();
    if (this.highlighted) this.highlight(this.highlighted);
  }

  private clearContent(): void {
    this.pulses.clear();
    this.edges?.dispose();
    this.drift?.dispose();
    for (const p of this.populations) p.dispose();
    this.scene.remove(this.content);
    disposeTree(this.content);
    this.populations.length = 0;
    this.registry.clear();
    this.helpers.clear();
    this.flares.clear();
    this.blooms.clear();
    this.edges = null;
    this.drift = null;
    this.core = null;
    this.labelList = [];
  }

  dispose(): void {
    this.clearContent();
    this.pulses.dispose();
    disposeTree(this.stars.points);
    disposeTree(this.membrane);
  }
}
