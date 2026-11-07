/**
 * Energy arcs (GAL-8): when a tool runs, a faint pulse of light travels from the
 * orb's edge to where the host says the tool's chip is. Everything here is plain
 * maths in CSS pixels from the canvas's top-left, so it is tested without WebGL.
 */
import { ORB_EXTENT, type StageRect } from './stage';

export interface Point {
  readonly x: number;
  readonly y: number;
}

/** How long one arc lives, head to fade, in ms. */
export const ARC_MS = 900;
/** At most this many at once; a burst of tools drops the oldest. */
export const MAX_ARCS = 3;
/** The ribbon's width in CSS pixels: a thread, not a beam. */
export const ARC_WIDTH = 2;
/** Points along one arc. */
export const ARC_SEGMENTS = 48;
/** How far the arc bows from the straight line, as a share of its length. */
const BEND = 0.18;
/** The orb's core (radius 1) as a share of the outer ring's diameter. */
const CORE = 1 / ORB_EXTENT;

/** Arcs are motion for its own sake: none in perf mode or with reduced motion (GAL-6). */
export const arcsAllowed = (perfActive: boolean, reducedMotion: boolean): boolean => !perfActive && !reducedMotion;

/**
 * Where an arc leaves the orb: the core's edge, on the side facing `to`.
 * Null when `to` is inside the core or anything is not a finite number.
 */
export function arcStart(stage: StageRect, to: Point): Point | null {
  const dx = to.x - stage.x;
  const dy = to.y - stage.y;
  const d = Math.hypot(dx, dy);
  const r = Math.max(0, stage.size) * CORE;
  if (!Number.isFinite(d) || d <= r) return null;
  return { x: stage.x + (dx / d) * r, y: stage.y + (dy / d) * r };
}

/** The quadratic's control point: the midpoint pushed sideways, always bowing upward on screen. */
export function arcControl(from: Point, to: Point): Point {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  // The left-hand normal, flipped if it points down (y grows downward).
  let nx = -dy;
  let ny = dx;
  if (ny > 0 || (ny === 0 && nx < 0)) {
    nx = -nx;
    ny = -ny;
  }
  const len = Math.hypot(nx, ny) || 1;
  const bow = Math.hypot(dx, dy) * BEND;
  return { x: (from.x + to.x) / 2 + (nx / len) * bow, y: (from.y + to.y) / 2 + (ny / len) * bow };
}

/** `n + 1` points along the arc, `from` first and `to` last. */
export function arcPoints(from: Point, to: Point, n = ARC_SEGMENTS): Point[] {
  const c = arcControl(from, to);
  const out: Point[] = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n;
    const a = (1 - t) * (1 - t);
    const b = 2 * (1 - t) * t;
    const e = t * t;
    out.push({ x: a * from.x + b * c.x + e * to.x, y: a * from.y + b * c.y + e * to.y });
  }
  return out;
}

/**
 * A ribbon `width` wide along `points`: two vertices per point (x, y, 0 each), the
 * share of the way along each is (0..1), and the triangles' indices.
 */
export function ribbon(points: readonly Point[], width = ARC_WIDTH): {
  positions: Float32Array; along: Float32Array; index: number[];
} {
  const n = points.length;
  const positions = new Float32Array(n * 6);
  const along = new Float32Array(n * 2);
  const index: number[] = [];
  const half = width / 2;
  for (let i = 0; i < n; i++) {
    const p = points[i] as Point;
    const prev = points[Math.max(0, i - 1)] as Point;
    const next = points[Math.min(n - 1, i + 1)] as Point;
    const tx = next.x - prev.x;
    const ty = next.y - prev.y;
    const len = Math.hypot(tx, ty) || 1;
    const nx = (-ty / len) * half;
    const ny = (tx / len) * half;
    positions.set([p.x + nx, p.y + ny, 0, p.x - nx, p.y - ny, 0], i * 6);
    const t = n > 1 ? i / (n - 1) : 0;
    along[i * 2] = t;
    along[i * 2 + 1] = t;
    if (i < n - 1) {
      const a = i * 2;
      index.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
    }
  }
  return { positions, along, index };
}

/** Where the bright head is, 0..1 along the arc, `age` ms after it began: it arrives at 60% of its life. */
export const arcHead = (age: number): number => Math.min(1, Math.max(0, age / (ARC_MS * 0.6)));

/** The whole arc's strength: full while the head travels, then fading to nothing at ARC_MS. */
export function arcFade(age: number): number {
  if (age <= ARC_MS * 0.6) return 1;
  return Math.max(0, 1 - (age - ARC_MS * 0.6) / (ARC_MS * 0.4));
}

/**
 * How bright a point `along` the arc is with the head at `head`: a soft head, a faint
 * trail fading behind it, nothing ahead of it. Mirrors the fragment shader.
 */
export function arcGlow(along: number, head: number): number {
  if (along > head + 0.04) return 0;
  const d = (along - head) / 0.06;
  const behind = Math.max(0, head - along);
  return Math.min(1, Math.exp(-d * d) + 0.35 * Math.exp(-behind / 0.25));
}
