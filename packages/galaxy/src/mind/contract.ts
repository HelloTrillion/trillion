/**
 * GAL-10, the Living Mind: the shape of `GET /api/mind` and of the events on
 * `/ws/observe` (docs/specs/2026-11-08-living-mind.md, Contract), and the parsers
 * that turn whatever arrived into it.
 *
 * The map draws nothing that isn't true, so the parsers are strict about meaning and
 * lenient about absence: a node of a type this page doesn't know is left out, an edge
 * whose ends are not both on the page is left out, a region the server didn't send is
 * simply not there.
 */

export const REGION_IDS = ['core', 'memory', 'working', 'agents', 'knowledge', 'rim'] as const;
export type RegionId = (typeof REGION_IDS)[number];

export const NODE_TYPES = ['core', 'ring', 'memory', 'chat', 'helper', 'knowledge', 'tool', 'connector'] as const;
export type NodeType = (typeof NODE_TYPES)[number];

export const EDGE_KINDS = ['similarity', 'recall', 'handoff', 'uses', 'feeds', 'serves'] as const;
export type EdgeKind = (typeof EDGE_KINDS)[number];

export const SOURCE_STATUSES = ['ok', 'empty', 'error', 'no_meaning'] as const;
export type SourceStatus = (typeof SOURCE_STATUSES)[number];

export interface MindRegion {
  readonly id: RegionId;
  readonly label: string;
  readonly count: number;
}

export interface MindNode {
  readonly id: string;
  readonly type: NodeType;
  readonly region: RegionId;
  readonly label: string;
  /** 0.5 to 3. */
  readonly size: number;
  /** 0 to 1: 1 is new (or undated), fading with a 30-day half-life. */
  readonly freshness: number;
  readonly extra: Readonly<Record<string, unknown>>;
}

export interface MindEdge {
  readonly source: string;
  readonly target: string;
  readonly kind: EdgeKind;
  /** 0 to 1. */
  readonly weight: number;
}

export interface MindStats {
  readonly memory_total: number;
  readonly memory_shown: number;
  readonly sources: Readonly<Partial<Record<RegionId, SourceStatus>>>;
}

export interface MindMap {
  readonly agent: string;
  readonly regions: readonly MindRegion[];
  readonly nodes: readonly MindNode[];
  readonly edges: readonly MindEdge[];
  readonly stats: MindStats;
}

/** Labels are short in the skeleton (80 characters for a memory); anything longer is cut. */
const MAX_LABEL = 200;

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v);

const oneOf = <T extends string>(list: readonly T[], v: unknown): v is T =>
  typeof v === 'string' && (list as readonly string[]).includes(v);

const num = (v: unknown, fallback: number, lo: number, hi: number): number =>
  typeof v === 'number' && Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : fallback;

const text = (v: unknown, fallback = ''): string => (typeof v === 'string' ? v.slice(0, MAX_LABEL) : fallback);

/** The skeleton, or null when what arrived is not one at all. */
export function parseMindMap(raw: unknown): MindMap | null {
  if (!isObject(raw) || !Array.isArray(raw['nodes'])) return null;

  const nodes: MindNode[] = [];
  const seen = new Set<string>();
  for (const n of raw['nodes']) {
    if (!isObject(n)) continue;
    const id = n['id'];
    if (typeof id !== 'string' || !id || seen.has(id)) continue;
    if (!oneOf(NODE_TYPES, n['type']) || !oneOf(REGION_IDS, n['region'])) continue;
    seen.add(id);
    nodes.push({
      id,
      type: n['type'],
      region: n['region'],
      label: text(n['label'], id),
      size: num(n['size'], 1, 0.5, 3),
      freshness: num(n['freshness'], 1, 0, 1),
      extra: isObject(n['extra']) ? n['extra'] : {},
    });
  }

  const edges: MindEdge[] = [];
  for (const e of Array.isArray(raw['edges']) ? raw['edges'] : []) {
    if (!isObject(e) || !oneOf(EDGE_KINDS, e['kind'])) continue;
    const source = e['source'];
    const target = e['target'];
    if (typeof source !== 'string' || typeof target !== 'string' || source === target) continue;
    if (!seen.has(source) || !seen.has(target)) continue;
    edges.push({ source, target, kind: e['kind'], weight: num(e['weight'], 1, 0, 1) });
  }

  const regions: MindRegion[] = [];
  for (const r of Array.isArray(raw['regions']) ? raw['regions'] : []) {
    if (!isObject(r) || !oneOf(REGION_IDS, r['id'])) continue;
    if (regions.some((x) => x.id === r['id'])) continue;
    regions.push({ id: r['id'], label: text(r['label'], r['id']), count: num(r['count'], 0, 0, Number.MAX_SAFE_INTEGER) });
  }

  const statsRaw = isObject(raw['stats']) ? raw['stats'] : {};
  const sourcesRaw = isObject(statsRaw['sources']) ? statsRaw['sources'] : {};
  const sources: Partial<Record<RegionId, SourceStatus>> = {};
  for (const id of REGION_IDS) {
    const s = sourcesRaw[id];
    if (oneOf(SOURCE_STATUSES, s)) sources[id] = s;
  }
  const memories = nodes.filter((n) => n.type === 'memory').length;
  const shown = num(statsRaw['memory_shown'], memories, 0, Number.MAX_SAFE_INTEGER);

  return {
    agent: text(raw['agent']),
    regions,
    nodes,
    edges,
    stats: {
      memory_total: num(statsRaw['memory_total'], shown, 0, Number.MAX_SAFE_INTEGER),
      memory_shown: shown,
      sources,
    },
  };
}

/** What `/ws/observe` sends after `observe.ready`: ids and names only. */
export type ObserveEvent =
  | { readonly type: 'turn'; readonly conversation_id: string | null }
  | { readonly type: 'tool.start'; readonly name: string }
  | { readonly type: 'tool.end'; readonly name: string; readonly ok: boolean }
  | { readonly type: 'memory.written'; readonly id: string }
  | { readonly type: 'memory.recalled'; readonly ids: readonly string[] }
  | { readonly type: 'helper.started'; readonly id: string }
  | { readonly type: 'helper.done'; readonly id: string; readonly ok: boolean }
  | { readonly type: 'alert'; readonly critical: boolean };

export type ObserveType = ObserveEvent['type'];

const str = (v: unknown): string | null => (typeof v === 'string' && v.length > 0 ? v : null);

/** One event, or null for anything else (`observe.ready`, an unknown type, a malformed one). */
export function parseObserveEvent(raw: unknown): ObserveEvent | null {
  if (!isObject(raw)) return null;
  switch (raw['type']) {
    case 'turn':
      return { type: 'turn', conversation_id: str(raw['conversation_id']) };
    case 'tool.start':
    case 'tool.end': {
      const name = str(raw['name']);
      if (!name) return null;
      return raw['type'] === 'tool.start' ? { type: 'tool.start', name } : { type: 'tool.end', name, ok: raw['ok'] !== false };
    }
    case 'memory.written': {
      const id = str(raw['id']);
      return id ? { type: 'memory.written', id } : null;
    }
    case 'memory.recalled': {
      const ids = Array.isArray(raw['ids']) ? raw['ids'].filter((x): x is string => typeof x === 'string' && x.length > 0) : [];
      return ids.length ? { type: 'memory.recalled', ids } : null;
    }
    case 'helper.started':
    case 'helper.done': {
      const id = str(raw['id']);
      if (!id) return null;
      return raw['type'] === 'helper.started' ? { type: 'helper.started', id } : { type: 'helper.done', id, ok: raw['ok'] !== false };
    }
    case 'alert':
      return { type: 'alert', critical: raw['critical'] === true };
    default:
      return null;
  }
}

/**
 * The node id an event's id names. Events carry the source's own id (`mem_0123…`, a
 * tool's name); the map namespaces them (`mem:mem_0123…`). An id that already carries
 * the prefix is taken as it is.
 */
export function nodeId(prefix: 'mem' | 'chat' | 'helper' | 'tool', id: string): string {
  return id.startsWith(`${prefix}:`) ? id : `${prefix}:${id}`;
}
