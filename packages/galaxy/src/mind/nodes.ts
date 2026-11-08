/**
 * The Living Mind's bodies: her star with its two prompt rings, the instanced beads of
 * each region, the helpers' glow spheres, the membrane, the stars, and a faint wash of
 * colour behind each region.
 */

import {
  AdditiveBlending,
  BackSide,
  BufferAttribute,
  BufferGeometry,
  Color,
  DoubleSide,
  Group,
  InstancedBufferAttribute,
  InstancedMesh,
  Matrix4,
  Mesh,
  MeshBasicMaterial,
  type Object3D,
  PlaneGeometry,
  Points,
  ShaderMaterial,
  SphereGeometry,
  Sprite,
  SpriteMaterial,
  TorusGeometry,
  Vector3,
} from 'three';
import type { MindNode, RegionId } from './contract';
import { MEMBRANE_RADIUS, RING_RADII, type Vec3, idPhase } from './layout';
import { radialTexture, toward } from './palette';
import {
  HALO_FRAGMENT,
  INST_HALO_FRAGMENT,
  INST_HALO_VERTEX,
  INST_NODE_FRAGMENT,
  INST_NODE_VERTEX,
  MEMBRANE_FRAGMENT,
  NODE_FRAGMENT,
  NODE_VERTEX,
  STAR_FRAGMENT,
  STAR_VERTEX,
} from './shaders';
import { seededRandom } from '../random';

/** Uniforms every animated material shares, written once a frame. */
export interface Shared {
  readonly uTime: { value: number };
  /** 1 breathes, 0 holds still (reduced motion). */
  readonly uBreath: { value: number };
}

export const toVector = (v: Vec3): Vector3 => new Vector3(v.x, v.y, v.z);

/** Small nodes are this big per unit of `size`. */
export const NODE_SCALE = 0.22;

export interface GlowSphere {
  readonly group: Group;
  readonly core: Mesh;
  readonly coreMaterial: ShaderMaterial;
  readonly halo: Mesh;
  readonly haloMaterial: ShaderMaterial;
}

export function makeGlowSphere(color: Color, radius: number, haloOpacity = 0.35, haloScale = 2.4): GlowSphere {
  const coreMaterial = new ShaderMaterial({
    uniforms: { uColor: { value: color.clone() }, uOpacity: { value: 1 }, uBoost: { value: 0 } },
    vertexShader: NODE_VERTEX,
    fragmentShader: NODE_FRAGMENT,
    transparent: true,
    depthWrite: false,
  });
  const haloMaterial = new ShaderMaterial({
    uniforms: { uColor: { value: color.clone() }, uOpacity: { value: haloOpacity } },
    vertexShader: NODE_VERTEX,
    fragmentShader: HALO_FRAGMENT,
    transparent: true,
    depthWrite: false,
    side: BackSide,
    blending: AdditiveBlending,
  });
  const core = new Mesh(new SphereGeometry(radius, 32, 32), coreMaterial);
  const halo = new Mesh(new SphereGeometry(radius * haloScale, 24, 24), haloMaterial);
  const group = new Group();
  group.add(core, halo);
  return { group, core, coreMaterial, halo, haloMaterial };
}

/** One region's small nodes: one draw call for the beads and one for their auras. */
export class Population {
  readonly mesh: InstancedMesh;
  readonly halo: InstancedMesh;
  readonly ids: readonly string[];
  private readonly positions: Vector3[] = [];
  private readonly scales: number[] = [];
  private readonly base: Color[] = [];
  private readonly boost: InstancedBufferAttribute;
  private readonly m = new Matrix4();

  constructor(nodes: readonly MindNode[], at: (id: string) => Vec3, color: Color, shared: Shared) {
    const count = nodes.length;
    const geo = new SphereGeometry(1, 24, 16);
    const phase = new InstancedBufferAttribute(new Float32Array(count), 1);
    const fresh = new InstancedBufferAttribute(new Float32Array(count), 1);
    this.boost = new InstancedBufferAttribute(new Float32Array(count), 1);
    geo.setAttribute('aPhase', phase);
    geo.setAttribute('aFreshness', fresh);
    geo.setAttribute('aBoost', this.boost);
    const mat = new ShaderMaterial({
      uniforms: { uTime: shared.uTime, uBreath: shared.uBreath, uOpacity: { value: 0.95 } },
      vertexShader: INST_NODE_VERTEX,
      fragmentShader: INST_NODE_FRAGMENT,
      transparent: true,
      depthWrite: false,
    });
    this.mesh = new InstancedMesh(geo, mat, count);
    const ids: string[] = [];
    nodes.forEach((n, i) => {
      const p = toVector(at(n.id));
      // Connectors stand out from the tools they serve.
      const s = NODE_SCALE * n.size * (n.type === 'connector' ? 1.6 : 1);
      this.positions.push(p);
      this.scales.push(s);
      this.m.makeScale(s, s, s).setPosition(p);
      this.mesh.setMatrixAt(i, this.m);
      const c = color.clone();
      this.base.push(c);
      this.mesh.setColorAt(i, c);
      phase.setX(i, idPhase(n.id));
      fresh.setX(i, n.freshness);
      ids.push(n.id);
    });
    this.ids = ids;
    this.mesh.instanceMatrix.needsUpdate = true;
    if (this.mesh.instanceColor) this.mesh.instanceColor.needsUpdate = true;
    this.mesh.computeBoundingSphere();
    this.mesh.userData['ids'] = ids;

    const haloGeo = new PlaneGeometry(1, 1);
    haloGeo.setAttribute('aPhase', phase);
    haloGeo.setAttribute('aFreshness', fresh);
    haloGeo.setAttribute('aBoost', this.boost);
    const haloMat = new ShaderMaterial({
      uniforms: { uTime: shared.uTime, uBreath: shared.uBreath, uOpacity: { value: 0.38 } },
      vertexShader: INST_HALO_VERTEX,
      fragmentShader: INST_HALO_FRAGMENT,
      transparent: true,
      depthWrite: false,
      blending: AdditiveBlending,
      side: DoubleSide,
    });
    this.halo = new InstancedMesh(haloGeo, haloMat, count);
    this.halo.instanceMatrix = this.mesh.instanceMatrix;
    this.halo.instanceColor = this.mesh.instanceColor;
    // The quads are grown in the shader, so their bounds on the CPU are wrong.
    this.halo.frustumCulled = false;
    this.halo.renderOrder = -0.5;
    this.halo.raycast = () => undefined;
  }

  position(i: number): Vector3 | undefined {
    return this.positions[i];
  }

  /** Scale a node by `mult` (1 is its own size), e.g. a bloom from nothing. */
  setScale(i: number, mult: number): void {
    const p = this.positions[i];
    const s = this.scales[i];
    if (!p || s === undefined) return;
    const k = Math.max(1e-4, s * mult);
    this.m.makeScale(k, k, k).setPosition(p);
    this.mesh.setMatrixAt(i, this.m);
    this.mesh.instanceMatrix.needsUpdate = true;
  }

  /** Brighten a node, tinted toward `tint` as it does; 0 puts it back. */
  setBoost(i: number, amount: number, tint: Color | null): void {
    const base = this.base[i];
    if (!base) return;
    this.boost.setX(i, amount);
    this.boost.needsUpdate = true;
    const c = tint && amount > 0 ? base.clone().lerp(tint, Math.min(1, amount)) : base;
    this.mesh.setColorAt(i, c);
    if (this.mesh.instanceColor) this.mesh.instanceColor.needsUpdate = true;
  }

  dispose(): void {
    this.mesh.geometry.dispose();
    (this.mesh.material as ShaderMaterial).dispose();
    this.halo.geometry.dispose();
    (this.halo.material as ShaderMaterial).dispose();
    this.mesh.dispose();
  }
}

export interface Core {
  readonly group: Group;
  readonly nucleus: Group;
  readonly star: GlowSphere;
  readonly corona: Sprite;
  readonly coronaOuter: Sprite;
  readonly stablePivot: Group;
  readonly turnPivot: Group;
  readonly stableRing: Mesh | null;
  readonly turnRing: Mesh | null;
  readonly turnMaterial: MeshBasicMaterial | null;
}

/**
 * Her: a layered sun. A white-hot centre, the glow shell, two counter-turning coronas,
 * and the prompt rings she has (the stable block inside, the per-turn block tilted
 * outside it). A torus's axis is z, so the rings turn about y, where it shows.
 */
export function buildCore(color: Color, rings: { stable: boolean; turn: boolean }): Core {
  const group = new Group();
  const star = makeGlowSphere(color, 1.4, 0.3, 1.6);
  star.core.userData['nodeId'] = 'core';
  const nucleus = new Group();
  nucleus.add(star.group);
  const inner = new Mesh(
    new SphereGeometry(0.72, 32, 32),
    new MeshBasicMaterial({ color: toward(color, 0.88), transparent: true, opacity: 0.85, blending: AdditiveBlending, depthWrite: false }),
  );
  inner.raycast = () => undefined;
  nucleus.add(inner);
  const coronaMaterial = (opacity: number, c: Color): SpriteMaterial =>
    new SpriteMaterial({ map: radialTexture(), color: c, transparent: true, opacity, blending: AdditiveBlending, depthWrite: false });
  const corona = new Sprite(coronaMaterial(0.5, color));
  corona.scale.setScalar(8.5);
  corona.renderOrder = -1;
  const coronaOuter = new Sprite(coronaMaterial(0.14, toward(color, 0.4)));
  coronaOuter.scale.setScalar(17);
  coronaOuter.renderOrder = -2;
  for (const s of [corona, coronaOuter]) s.raycast = () => undefined;
  nucleus.add(corona, coronaOuter);
  group.add(nucleus);

  const ringMaterial = (c: Color, opacity: number): MeshBasicMaterial =>
    new MeshBasicMaterial({ color: c, transparent: true, opacity, blending: AdditiveBlending, depthWrite: false });
  const stablePivot = new Group();
  const turnPivot = new Group();
  let stableRing: Mesh | null = null;
  let turnRing: Mesh | null = null;
  let turnMaterial: MeshBasicMaterial | null = null;
  if (rings.stable) {
    stableRing = new Mesh(new TorusGeometry(RING_RADII['ring:stable'], 0.02, 12, 128), ringMaterial(toward(color, 0.55), 0.55));
    stableRing.userData['nodeId'] = 'ring:stable';
    stablePivot.add(stableRing);
  }
  if (rings.turn) {
    turnMaterial = ringMaterial(toward(color, 0.4), 0.25);
    turnRing = new Mesh(new TorusGeometry(RING_RADII['ring:turn'], 0.015, 12, 128), turnMaterial);
    turnRing.rotation.x = 0.5;
    turnRing.userData['nodeId'] = 'ring:turn';
    turnPivot.add(turnRing);
  }
  group.add(stablePivot, turnPivot);
  return { group, nucleus, star, corona, coronaOuter, stablePivot, turnPivot, stableRing, turnRing, turnMaterial };
}

export function buildMembrane(color: Color): Mesh {
  const m = new Mesh(
    new SphereGeometry(MEMBRANE_RADIUS, 48, 48),
    new ShaderMaterial({
      uniforms: { uColor: { value: color.clone() }, uOpacity: { value: 0.06 } },
      vertexShader: NODE_VERTEX,
      fragmentShader: MEMBRANE_FRAGMENT,
      transparent: true,
      depthWrite: false,
      side: BackSide,
      blending: AdditiveBlending,
    }),
  );
  m.raycast = () => undefined;
  return m;
}

/** 600 stars on a shell from 80 to 160 out, each with its own twinkle. */
export function buildStarfield(seed: number, uTime: { value: number }): { points: Points; twinkle: { value: number } } {
  const count = 600;
  const rand = seededRandom(seed);
  const positions = new Float32Array(count * 3);
  const phases = new Float32Array(count);
  const sizes = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    const theta = 2 * Math.PI * rand();
    const phi = Math.acos(2 * rand() - 1);
    const r = 80 + rand() * 80;
    positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
    positions[i * 3 + 2] = r * Math.cos(phi);
    phases[i] = rand() * Math.PI * 2;
    sizes[i] = 0.06 + rand() * 0.1;
  }
  const geo = new BufferGeometry();
  geo.setAttribute('position', new BufferAttribute(positions, 3));
  geo.setAttribute('aPhase', new BufferAttribute(phases, 1));
  geo.setAttribute('aSize', new BufferAttribute(sizes, 1));
  const twinkle = { value: 1 };
  const points = new Points(
    geo,
    new ShaderMaterial({
      uniforms: { uTime, uTwinkle: twinkle },
      vertexShader: STAR_VERTEX,
      fragmentShader: STAR_FRAGMENT,
      transparent: true,
      depthWrite: false,
      blending: AdditiveBlending,
    }),
  );
  points.raycast = () => undefined;
  return { points, twinkle };
}

const ACCENT_SCALE: Readonly<Partial<Record<RegionId, number>>> = { memory: 15, working: 9, agents: 15, knowledge: 10, rim: 14 };

/** A local glow and a wider, fainter wash behind a region: the depth cue between regions. */
export function buildAccent(region: RegionId, at: Vec3, color: Color): Object3D[] {
  const scale = ACCENT_SCALE[region];
  if (!scale) return [];
  return ([
    [1, 0.1, -1],
    [2.1, 0.05, -2],
  ] as const).map(([mult, opacity, order]) => {
    const s = new Sprite(new SpriteMaterial({ map: radialTexture(), color, transparent: true, opacity, depthWrite: false, blending: AdditiveBlending }));
    s.position.copy(toVector(at));
    s.scale.setScalar(scale * mult);
    s.renderOrder = order;
    s.raycast = () => undefined;
    return s;
  });
}

/** Release every geometry and material under a node (the shared texture stays). */
export function disposeTree(root: Object3D): void {
  root.traverse((o) => {
    const any = o as Object3D & { geometry?: BufferGeometry; material?: { dispose(): void } | { dispose(): void }[] };
    any.geometry?.dispose();
    const mats = Array.isArray(any.material) ? any.material : any.material ? [any.material] : [];
    for (const m of mats) m.dispose();
  });
}
