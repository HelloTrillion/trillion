/**
 * Where everything in the Living Mind goes. Pure maths on plain numbers: no three.js,
 * no scene. Every position follows from the node ids and a seed, so a reload draws
 * the same mind in the same place.
 *
 * The mind is a solar system with regions: her star at the origin, each region at a
 * fixed anchor around it, all inside a membrane of radius 26.
 */

import type { MindEdge, MindMap, MindNode, RegionId } from './contract';

export interface Vec3 {
  readonly x: number;
  readonly y: number;
  readonly z: number;
}

export const MEMBRANE_RADIUS = 26;
/** Nothing is placed closer to the membrane than this. */
const MEMBRANE_MARGIN = 1.5;

export const ANCHORS: Readonly<Record<RegionId, Vec3>> = {
  core: { x: 0, y: 0, z: 0 },
  memory: { x: -14, y: 5.5, z: -2 },
  working: { x: -6, y: -3.5, z: 4 },
  agents: { x: 13.5, y: 2.5, z: 0 },
  knowledge: { x: 0, y: -10.5, z: -1 },
  // The capability ball, lower front right.
  rim: { x: 13, y: -8.5, z: 8 },
};

/** The prompt rings around her star: the stable block, and the per-turn block outside it. */
export const RING_RADII: Readonly<Record<string, number>> = { 'ring:stable': 2.3, 'ring:turn': 3.1 };

/** How far the memory web may spread from its anchor. */
export const MEMORY_RADIUS = 8;

const add = (a: Vec3, b: Vec3, k = 1): Vec3 => ({ x: a.x + b.x * k, y: a.y + b.y * k, z: a.z + b.z * k });
const len = (v: Vec3): number => Math.hypot(v.x, v.y, v.z);

/** FNV-1a over the characters: a stable 32-bit number per id. */
export function hashString(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/** A small seeded stream of numbers in [0, 1) for one id (mulberry32). */
export function idRandom(id: string, seed = 1): () => number {
  let a = (hashString(id) ^ Math.imul(seed, 0x9e3779b1)) >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** A node's breathing phase, 0 to 2π: from its id, so nodes never breathe in unison. */
export function idPhase(id: string): number {
  return (hashString(id) / 4294967296) * Math.PI * 2;
}

/** Pull a point back inside a ball. */
function clampTo(p: Vec3, centre: Vec3, radius: number): Vec3 {
  const d = { x: p.x - centre.x, y: p.y - centre.y, z: p.z - centre.z };
  const l = len(d);
  if (l <= radius) return p;
  return add(centre, d, radius / l);
}

const insideMembrane = (p: Vec3): Vec3 => clampTo(p, ANCHORS.core, MEMBRANE_RADIUS - MEMBRANE_MARGIN);

export interface MemoryLayout {
  readonly positions: Map<string, Vec3>;
  /** The total movement in each iteration: falls as the web settles. */
  readonly movement: readonly number[];
}

const ITERATIONS = 150;
const DAMPING = 0.82;
const GRAVITY = 0.05;
const SPRING = 0.045;

/**
 * The memory web: similarity edges are springs, every pair of memories pushes apart,
 * and a gentle pull holds them to the anchor. Run once at load and kept, never per
 * frame. Without similarity edges (meaning not installed) memories of the same kind
 * gather instead, so the web still shows something true about them.
 */
export function layoutMemory(nodes: readonly MindNode[], edges: readonly MindEdge[], seed = 1): MemoryLayout {
  const n = nodes.length;
  const anchor = ANCHORS.memory;
  const index = new Map(nodes.map((node, i) => [node.id, i]));
  const px = new Float64Array(n);
  const py = new Float64Array(n);
  const pz = new Float64Array(n);
  const vx = new Float64Array(n);
  const vy = new Float64Array(n);
  const vz = new Float64Array(n);
  // A wider start for a bigger web, so the first pushes are not explosive.
  const spread = Math.min(2 * MEMORY_RADIUS * 0.8, 6 + Math.cbrt(n) * 1.2);
  nodes.forEach((node, i) => {
    const r = idRandom(node.id, seed);
    px[i] = anchor.x + (r() - 0.5) * spread;
    py[i] = anchor.y + (r() - 0.5) * spread;
    pz[i] = anchor.z + (r() - 0.5) * spread;
  });

  const springs = edges
    .filter((e) => e.kind === 'similarity')
    .map((e) => [index.get(e.source), index.get(e.target), e.weight] as const)
    .filter((s): s is readonly [number, number, number] => s[0] !== undefined && s[1] !== undefined);

  // No meaning: gather by kind, each kind around its own point near the anchor.
  const kindCentre = new Float64Array(n * 3);
  const byKind = springs.length === 0;
  if (byKind) {
    const kinds = [...new Set(nodes.map((node) => String(node.extra['kind'] ?? '')))].sort();
    nodes.forEach((node, i) => {
      const k = kinds.indexOf(String(node.extra['kind'] ?? ''));
      const a = (k / Math.max(1, kinds.length)) * Math.PI * 2;
      const r = kinds.length > 1 ? 3.2 : 0;
      kindCentre[i * 3] = anchor.x + Math.cos(a) * r;
      kindCentre[i * 3 + 1] = anchor.y + Math.sin(a * 2) * 0.8;
      kindCentre[i * 3 + 2] = anchor.z + Math.sin(a) * r;
    });
  }

  // Push weakens as the web grows, so 500 memories fit the same space as 50.
  // (A cloud of n settles where n·push/R² meets the pull GRAVITY·R.)
  const repulse = Math.min(0.8, 14 / Math.max(1, n));
  const movement: number[] = [];
  for (let it = 0; it < ITERATIONS; it++) {
    const fx = new Float64Array(n);
    const fy = new Float64Array(n);
    const fz = new Float64Array(n);
    for (let a = 0; a < n; a++) {
      for (let b = a + 1; b < n; b++) {
        let dx = px[a]! - px[b]!;
        let dy = py[a]! - py[b]!;
        let dz = pz[a]! - pz[b]!;
        let d = Math.hypot(dx, dy, dz);
        if (d < 1e-6) {
          // Two in one place: part them along a direction from their ids.
          dx = ((a * 7919 + b) % 13) / 13 - 0.5;
          dy = ((a * 104729 + b) % 17) / 17 - 0.5;
          dz = 0.1;
          d = Math.hypot(dx, dy, dz);
        }
        const dist = Math.max(d, 0.25);
        const f = repulse / (dist * dist) / d;
        fx[a] = fx[a]! + dx * f;
        fy[a] = fy[a]! + dy * f;
        fz[a] = fz[a]! + dz * f;
        fx[b] = fx[b]! - (dx * f);
        fy[b] = fy[b]! - (dy * f);
        fz[b] = fz[b]! - (dz * f);
      }
    }
    for (let i = 0; i < n; i++) {
      const cx = byKind ? kindCentre[i * 3]! : anchor.x;
      const cy = byKind ? kindCentre[i * 3 + 1]! : anchor.y;
      const cz = byKind ? kindCentre[i * 3 + 2]! : anchor.z;
      vx[i] = (vx[i]! + fx[i]! + (cx - px[i]!) * GRAVITY) * DAMPING;
      vy[i] = (vy[i]! + fy[i]! + (cy - py[i]!) * GRAVITY) * DAMPING;
      vz[i] = (vz[i]! + fz[i]! + (cz - pz[i]!) * GRAVITY) * DAMPING;
    }
    for (const [a, b, w] of springs) {
      const k = SPRING * w;
      const dx = (px[b]! - px[a]!) * k;
      const dy = (py[b]! - py[a]!) * k;
      const dz = (pz[b]! - pz[a]!) * k;
      vx[a] = vx[a]! + dx;
      vy[a] = vy[a]! + dy;
      vz[a] = vz[a]! + dz;
      vx[b] = vx[b]! - (dx);
      vy[b] = vy[b]! - (dy);
      vz[b] = vz[b]! - (dz);
    }
    let moved = 0;
    for (let i = 0; i < n; i++) {
      // The web stays a region: inside its ball, and so inside the membrane. The wall
      // takes the speed it stopped, so nothing keeps pressing against it.
      const c = insideMembrane(clampTo({ x: px[i]! + vx[i]!, y: py[i]! + vy[i]!, z: pz[i]! + vz[i]! }, anchor, MEMORY_RADIUS));
      vx[i] = c.x - px[i]!;
      vy[i] = c.y - py[i]!;
      vz[i] = c.z - pz[i]!;
      px[i] = c.x;
      py[i] = c.y;
      pz[i] = c.z;
      moved += Math.hypot(vx[i]!, vy[i]!, vz[i]!);
    }
    movement.push(moved);
  }

  const positions = new Map<string, Vec3>();
  nodes.forEach((node, i) => positions.set(node.id, { x: px[i]!, y: py[i]!, z: pz[i]! }));
  return { positions, movement };
}

const GOLDEN = Math.PI * (3 - Math.sqrt(5));

/** An even spread of n directions on the unit sphere. */
export function sphereDir(i: number, n: number, phase = 0): Vec3 {
  const y = 1 - (2 * (i + 0.5)) / Math.max(n, 1);
  const r = Math.sqrt(Math.max(0, 1 - y * y));
  const a = i * GOLDEN + phase;
  return { x: Math.cos(a) * r, y, z: Math.sin(a) * r };
}

function jitter(id: string, seed: number, scale: number): Vec3 {
  const r = idRandom(id, seed);
  return { x: (r() - 0.5) * scale, y: (r() - 0.5) * scale, z: (r() - 0.5) * scale };
}

/** Every node's place. Nodes whose region has no layout of its own sit at their anchor. */
export function layoutMind(map: Pick<MindMap, 'nodes' | 'edges'>, seed = 1): Map<string, Vec3> {
  const pos = new Map<string, Vec3>();
  const by = (region: RegionId): MindNode[] => map.nodes.filter((n) => n.region === region);

  // Core: her star and its rings, at the origin.
  for (const n of by('core')) pos.set(n.id, ANCHORS.core);

  // Memory: the force-directed web.
  const memories = by('memory');
  for (const [id, p] of layoutMemory(memories, map.edges, seed).positions) pos.set(id, p);

  // Working: the recent chats in a small ring with a gentle wave.
  const chats = by('working');
  chats.forEach((n, i) => {
    const a = (i / Math.max(chats.length, 1)) * Math.PI * 2;
    pos.set(n.id, add(ANCHORS.working, { x: Math.cos(a) * 2.4, y: Math.sin(a * 2) * 1.1, z: Math.sin(a) * 2.4 }));
  });

  // Agents: her helpers along a vertical arc, a column of presences to one side.
  const helpers = by('agents');
  helpers.forEach((n, i) => {
    const t = helpers.length > 1 ? i / (helpers.length - 1) : 0.5;
    const span = Math.min(1.7, 0.35 * Math.max(1, helpers.length - 1));
    const a = -span / 2 + t * span;
    pos.set(n.id, add(ANCHORS.agents, { x: Math.cos(a) * 3.2 - 1.5, y: Math.sin(a) * 9.5, z: Math.sin(a * 2) * 2 }));
  });

  // Knowledge: a flat lattice, roughly square.
  const files = by('knowledge');
  const cols = Math.ceil(Math.sqrt(files.length));
  files.forEach((n, i) => {
    const x = (i % cols) - (cols - 1) / 2;
    const z = Math.floor(i / cols) - (Math.ceil(files.length / cols) - 1) / 2;
    pos.set(n.id, add(ANCHORS.knowledge, { x: x * 2.2, y: 0, z: z * 2.2 }));
  });

  // Rim: the capability ball. Tool categories on an inner sphere, each category's
  // tools bunched round it like grapes; connectors on a slightly larger shell, half a
  // turn round so they sit between the categories rather than behind them.
  const rim = by('rim');
  const tools = rim.filter((n) => n.type === 'tool');
  const categories = [...new Set(tools.map((n) => String(n.extra['category'] ?? '')))].sort();
  const catPos = new Map(categories.map((c, i) => [c, add(ANCHORS.rim, sphereDir(i, categories.length), categories.length > 1 ? 4.5 : 0)]));
  for (const n of tools) {
    const centre = catPos.get(String(n.extra['category'] ?? '')) ?? ANCHORS.rim;
    pos.set(n.id, add(centre, jitter(n.id, seed, 1.6)));
  }
  const others = rim.filter((n) => n.type !== 'tool');
  others.forEach((n, i) => pos.set(n.id, add(ANCHORS.rim, sphereDir(i, others.length, Math.PI), 6.5)));

  for (const [id, p] of pos) pos.set(id, insideMembrane(p));
  return pos;
}

/**
 * Where an edge meets a node. A ring is not a point: an edge to it lands on the ring,
 * on the side facing where the edge comes from.
 */
export function edgeEnd(id: string, at: Vec3, from: Vec3): Vec3 {
  const r = RING_RADII[id];
  if (r === undefined) return at;
  const d = { x: from.x - at.x, y: from.y - at.y, z: from.z - at.z };
  const l = len(d);
  return l < 1e-6 ? at : add(at, d, r / l);
}
