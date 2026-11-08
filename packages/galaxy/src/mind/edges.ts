/**
 * The Living Mind's connections: curved, additive threads merged into a few draw calls,
 * a hover overlay that lights one node's links, a pool of pulses that travel them, and
 * the faint drift of particles from the chats into the memory web.
 */

import {
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  type Color,
  type Curve,
  Float32BufferAttribute,
  Group,
  LineBasicMaterial,
  LineCurve3,
  LineSegments,
  type Material,
  Points,
  PointsMaterial,
  QuadraticBezierCurve3,
  ShaderMaterial,
  Sprite,
  SpriteMaterial,
  Vector3,
} from 'three';
import type { EdgeKind, MindEdge, RegionId } from './contract';
import { hashString } from './layout';
import { radialTexture } from './palette';
import { FLOW_EDGE_FRAGMENT, FLOW_EDGE_VERTEX, SIM_EDGE_FRAGMENT, SIM_EDGE_VERTEX } from './shaders';

export interface EdgeStyle {
  readonly region: RegionId;
  readonly opacity: number;
  /** Comets along the edge: positive runs source to target, negative the other way. */
  readonly flow?: number;
  readonly shimmer?: boolean;
}

export const EDGE_STYLE: Readonly<Record<EdgeKind, EdgeStyle>> = {
  similarity: { region: 'memory', opacity: 0.16, shimmer: true },
  // Recall flows home, from the memories into her.
  recall: { region: 'memory', opacity: 0.2, flow: -0.3 },
  handoff: { region: 'core', opacity: 0.22, flow: 0.42 },
  uses: { region: 'agents', opacity: 0.18 },
  // What she reads every turn streams into the stable ring.
  feeds: { region: 'knowledge', opacity: 0.14, flow: 0.18 },
  serves: { region: 'rim', opacity: 0.14 },
};

const SAMPLES = 24;

const phaseOf = (key: string): number => (hashString(key) / 4294967296) * Math.PI * 2;

/**
 * A curve, not a line: the midpoint pushed out 12% of the chord across it, so edges
 * arc round clusters instead of slicing through them.
 */
export function edgeCurve(a: Vector3, b: Vector3): QuadraticBezierCurve3 {
  const mid = a.clone().add(b).multiplyScalar(0.5);
  const chord = b.clone().sub(a);
  const outward = mid.lengthSq() > 1e-4 ? mid.clone().normalize() : new Vector3(0, 1, 0);
  const perp = new Vector3().crossVectors(chord, outward);
  if (perp.lengthSq() < 1e-6) perp.set(0, 1, 0);
  else perp.normalize();
  return new QuadraticBezierCurve3(a.clone(), mid.add(perp.multiplyScalar(chord.length() * 0.12)), b.clone());
}

export interface EdgeEntry {
  readonly curve: QuadraticBezierCurve3;
  readonly kind: EdgeKind;
  readonly regions: readonly [RegionId, RegionId];
}

/** A shader material whose `opacity` reaches its uniform, so dimming works the same on all. */
function opacityShim(mat: ShaderMaterial): ShaderMaterial {
  Object.defineProperty(mat, 'opacity', {
    get: () => mat.uniforms['uOpacity']?.value as number,
    set: (v: number) => {
      const u = mat.uniforms['uOpacity'];
      if (u) u.value = v;
    },
  });
  return mat;
}

export class EdgeLayer {
  readonly group = new Group();
  /** `source|target` → its curve. */
  readonly curves = new Map<string, EdgeEntry>();
  private readonly lines: LineSegments[] = [];
  private readonly flowUniforms: { value: number }[] = [];
  private readonly shimmerUniforms: { value: number }[] = [];
  private overlays: LineSegments[] = [];
  private readonly colors: Readonly<Record<RegionId, Color>>;

  constructor(
    edges: readonly MindEdge[],
    end: (id: string, other: string) => Vector3 | null,
    regionOf: (id: string) => RegionId | undefined,
    colors: Readonly<Record<RegionId, Color>>,
    uTime: { value: number },
  ) {
    this.colors = colors;
    // Merged by kind and by the regions at each end, so hiding a region hides exactly
    // the edges that touch it, in a handful of draw calls.
    const buckets = new Map<string, { kind: EdgeKind; regions: [RegionId, RegionId]; pts: Vector3[]; phase: number[]; t: number[]; w: number[] }>();
    for (const e of edges) {
      const a = end(e.source, e.target);
      const b = end(e.target, e.source);
      const ra = regionOf(e.source);
      const rb = regionOf(e.target);
      if (!a || !b || !ra || !rb) continue;
      const key = `${e.source}|${e.target}`;
      const curve = edgeCurve(a, b);
      const regions: [RegionId, RegionId] = ra <= rb ? [ra, rb] : [rb, ra];
      this.curves.set(key, { curve, kind: e.kind, regions });
      const bucketKey = `${e.kind}|${regions[0]}|${regions[1]}`;
      let bucket = buckets.get(bucketKey);
      if (!bucket) {
        bucket = { kind: e.kind, regions, pts: [], phase: [], t: [], w: [] };
        buckets.set(bucketKey, bucket);
      }
      const pts = curve.getPoints(SAMPLES);
      const phase = phaseOf(key);
      for (let i = 0; i < SAMPLES; i++) {
        bucket.pts.push(pts[i]!, pts[i + 1]!);
        bucket.phase.push(phase, phase);
        bucket.t.push(i / SAMPLES, (i + 1) / SAMPLES);
        bucket.w.push(e.weight, e.weight);
      }
    }
    for (const b of buckets.values()) {
      const style = EDGE_STYLE[b.kind];
      const color = colors[style.region];
      const geo = new BufferGeometry().setFromPoints(b.pts);
      let mat: Material;
      if (style.flow !== undefined) {
        geo.setAttribute('aT', new Float32BufferAttribute(b.t, 1));
        geo.setAttribute('aPhase', new Float32BufferAttribute(b.phase, 1));
        geo.setAttribute('aW', new Float32BufferAttribute(b.w, 1));
        const flow = { value: 1 };
        this.flowUniforms.push(flow);
        mat = opacityShim(
          new ShaderMaterial({
            uniforms: { uColor: { value: color.clone() }, uOpacity: { value: style.opacity }, uTime, uSpeed: { value: style.flow }, uFlow: flow },
            vertexShader: FLOW_EDGE_VERTEX,
            fragmentShader: FLOW_EDGE_FRAGMENT,
            transparent: true,
            depthWrite: false,
            blending: AdditiveBlending,
          }),
        );
      } else if (style.shimmer) {
        geo.setAttribute('aPhase', new Float32BufferAttribute(b.phase, 1));
        const shimmer = { value: 1 };
        this.shimmerUniforms.push(shimmer);
        mat = opacityShim(
          new ShaderMaterial({
            uniforms: { uColor: { value: color.clone() }, uOpacity: { value: style.opacity }, uTime, uShimmer: shimmer },
            vertexShader: SIM_EDGE_VERTEX,
            fragmentShader: SIM_EDGE_FRAGMENT,
            transparent: true,
            depthWrite: false,
            blending: AdditiveBlending,
          }),
        );
      } else {
        mat = new LineBasicMaterial({ color, transparent: true, opacity: style.opacity, blending: AdditiveBlending, depthWrite: false });
      }
      const lines = new LineSegments(geo, mat);
      lines.userData['kind'] = b.kind;
      lines.userData['regions'] = b.regions;
      lines.raycast = () => undefined;
      this.lines.push(lines);
      this.group.add(lines);
    }
  }

  /** Draw calls in use, for tests and the curious. */
  get drawCalls(): number {
    return this.lines.length;
  }

  /** An edge is shown only while the regions at both its ends are. */
  setRegionsVisible(visible: (r: RegionId) => boolean): void {
    for (const l of this.lines) {
      const [a, b] = l.userData['regions'] as [RegionId, RegionId];
      l.visible = visible(a) && visible(b);
    }
  }

  /** Comets and glitter on, or held still (reduced motion, the governor). */
  setMotion(on: boolean): void {
    for (const u of this.flowUniforms) u.value = on ? 1 : 0;
    for (const u of this.shimmerUniforms) u.value = on ? 1 : 0;
  }

  /**
   * Light one node's edges. Merged lines can't brighten one edge, so every line dims
   * and that node's edges are drawn again, bright, on top. False when it has none.
   */
  highlight(id: string | null, visible: (r: RegionId) => boolean): boolean {
    this.clearOverlays();
    if (!id) return false;
    const byKind = new Map<EdgeKind, Vector3[]>();
    for (const [key, entry] of this.curves) {
      const sep = key.indexOf('|');
      if (key.slice(0, sep) !== id && key.slice(sep + 1) !== id) continue;
      if (!visible(entry.regions[0]) || !visible(entry.regions[1])) continue;
      const pts = entry.curve.getPoints(SAMPLES);
      const arr = byKind.get(entry.kind) ?? [];
      for (let i = 0; i < SAMPLES; i++) arr.push(pts[i]!, pts[i + 1]!);
      byKind.set(entry.kind, arr);
    }
    if (!byKind.size) return false;
    for (const l of this.lines) (l.material as Material).opacity = 0.04;
    for (const [kind, pts] of byKind) {
      const overlay = new LineSegments(
        new BufferGeometry().setFromPoints(pts),
        new LineBasicMaterial({ color: this.colors[EDGE_STYLE[kind].region], transparent: true, opacity: 0.85, blending: AdditiveBlending, depthWrite: false }),
      );
      overlay.raycast = () => undefined;
      this.overlays.push(overlay);
      this.group.add(overlay);
    }
    return true;
  }

  private clearOverlays(): void {
    for (const o of this.overlays) {
      this.group.remove(o);
      o.geometry.dispose();
      (o.material as Material).dispose();
    }
    this.overlays = [];
    for (const l of this.lines) (l.material as Material).opacity = EDGE_STYLE[l.userData['kind'] as EdgeKind].opacity;
  }

  dispose(): void {
    this.clearOverlays();
    for (const l of this.lines) {
      l.geometry.dispose();
      (l.material as Material).dispose();
    }
  }
}

interface Flight {
  readonly sprite: Sprite;
  readonly curve: Curve<Vector3>;
  readonly reverse: boolean;
  readonly dur: number;
  t: number;
  readonly onArrive: (() => void) | undefined;
}

/**
 * Pulses: 64 sprites made once and reused. A pulse follows the edge between its ends
 * (either way round), or a straight line between them when there is none. Its ends
 * are always real places; the caller never asks for a pulse to nowhere.
 */
export class PulsePool {
  readonly group = new Group();
  private readonly free: Sprite[] = [];
  private readonly active: Flight[] = [];
  enabled = true;

  constructor(size = 64) {
    for (let i = 0; i < size; i++) {
      const s = new Sprite(new SpriteMaterial({ map: radialTexture(), transparent: true, depthWrite: false, blending: AdditiveBlending }));
      s.visible = false;
      s.raycast = () => undefined;
      this.free.push(s);
      this.group.add(s);
    }
  }

  get inFlight(): number {
    return this.active.length;
  }

  fire(curves: ReadonlyMap<string, EdgeEntry>, from: string, to: string, a: Vector3, b: Vector3, color: Color, onArrive?: () => void): boolean {
    if (!this.enabled) return false;
    const sprite = this.free.pop();
    if (!sprite) return false;
    let entry = curves.get(`${from}|${to}`);
    let reverse = false;
    if (!entry) {
      entry = curves.get(`${to}|${from}`);
      reverse = entry !== undefined;
    }
    const curve: Curve<Vector3> = entry?.curve ?? new LineCurve3(a.clone(), b.clone());
    (sprite.material as SpriteMaterial).color.copy(color);
    sprite.visible = true;
    this.active.push({ sprite, curve, reverse, dur: 1.4, t: 0, onArrive });
    return true;
  }

  update(dt: number): void {
    for (let i = this.active.length - 1; i >= 0; i--) {
      const p = this.active[i]!;
      p.t += dt / p.dur;
      if (p.t >= 1) {
        p.sprite.visible = false;
        this.free.push(p.sprite);
        this.active.splice(i, 1);
        p.onArrive?.();
        continue;
      }
      p.curve.getPoint(p.reverse ? 1 - p.t : p.t, p.sprite.position);
      // It swells in mid-flight.
      p.sprite.scale.setScalar(0.35 + 0.35 * Math.sin(Math.PI * p.t));
    }
  }

  clear(): void {
    for (const p of this.active) {
      p.sprite.visible = false;
      this.free.push(p.sprite);
    }
    this.active.length = 0;
  }

  dispose(): void {
    this.clear();
    for (const s of this.free) (s.material as SpriteMaterial).dispose();
  }
}

/** About 40 faint particles drifting from the chats into the memory web on bowed paths. */
export class Drift {
  readonly points: Points;
  private readonly from: Vector3;
  private readonly to: Vector3;
  private readonly bow: Vector3;
  private readonly parts: { t: number; speed: number; offset: Vector3; arc: number }[] = [];
  private readonly pos: Float32Array;
  private readonly rand: () => number;
  private readonly v = new Vector3();

  constructor(from: Vector3, to: Vector3, color: Color, rand: () => number, count = 40) {
    this.from = from;
    this.to = to;
    this.rand = rand;
    const axis = to.clone().sub(from);
    this.bow = new Vector3().crossVectors(axis, new Vector3(0, 1, 0));
    if (this.bow.lengthSq() < 1e-6) this.bow.set(1, 0, 0);
    else this.bow.normalize();
    for (let i = 0; i < count; i++) this.parts.push({ t: rand(), speed: 0.05 + rand() * 0.06, offset: this.lane(), arc: 1 + rand() * 2.2 });
    this.pos = new Float32Array(count * 3);
    const geo = new BufferGeometry();
    geo.setAttribute('position', new BufferAttribute(this.pos, 3));
    this.points = new Points(
      geo,
      new PointsMaterial({ color, size: 0.14, transparent: true, opacity: 0.55, map: radialTexture(), blending: AdditiveBlending, depthWrite: false }),
    );
    this.points.frustumCulled = false;
    this.points.raycast = () => undefined;
    this.update(0);
  }

  private lane(): Vector3 {
    return new Vector3((this.rand() - 0.5) * 2.6, (this.rand() - 0.5) * 2.6, (this.rand() - 0.5) * 2.6);
  }

  update(dt: number): void {
    this.parts.forEach((p, i) => {
      p.t += dt * p.speed;
      if (p.t >= 1) {
        p.t = 0;
        p.offset = this.lane();
        p.arc = 1 + this.rand() * 2.2;
      }
      this.v.lerpVectors(this.from, this.to, p.t).add(p.offset).addScaledVector(this.bow, Math.sin(Math.PI * p.t) * p.arc);
      this.pos[i * 3] = this.v.x;
      this.pos[i * 3 + 1] = this.v.y;
      this.pos[i * 3 + 2] = this.v.z;
    });
    (this.points.geometry.getAttribute('position') as BufferAttribute).needsUpdate = true;
  }

  dispose(): void {
    this.points.geometry.dispose();
    (this.points.material as PointsMaterial).dispose();
  }
}
