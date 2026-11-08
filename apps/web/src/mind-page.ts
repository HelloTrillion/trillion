/**
 * The Living Mind page's plain parts (GAL-10): the token, the deep link, the stats line,
 * search, and the words a tooltip shows. No DOM and no three.js, so all of it is tested.
 */

import type { MindMap, MindNode, RegionId, SourceStatus } from '@hellotrillion/galaxy';

export const TOKEN_KEY = 'trillion-token';

interface Storageish {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/**
 * The token, as the main page takes it: from `#token=` in the fragment (which is never
 * sent anywhere), kept in this tab's storage, else what that storage already holds.
 * `hash` is the fragment with the token taken out, to put back in the address bar.
 */
export function takeToken(hash: string, storage: Storageish | null): { token: string | null; hash: string } {
  const match = /(?:^#|&)token=([\w-]+)/.exec(hash);
  if (match?.[1]) {
    try {
      storage?.setItem(TOKEN_KEY, match[1]);
    } catch {
      // Storage can be blocked; the token still works for this load.
    }
    const rest = hash.replace(/^#/, '').split('&').filter((p) => !p.startsWith('token=')).join('&');
    return { token: match[1], hash: rest ? `#${rest}` : '' };
  }
  let kept: string | null = null;
  try {
    kept = storage?.getItem(TOKEN_KEY) ?? null;
  } catch {
    kept = null;
  }
  return { token: kept && /^[\w-]+$/.test(kept) ? kept : null, hash };
}

/** `#node=<id>`: the node to open on. */
export function deepLinkNode(hash: string): string | null {
  const match = /(?:^#|&)node=([^&]+)/.exec(hash);
  if (!match?.[1]) return null;
  try {
    const id = decodeURIComponent(match[1]);
    return id.length > 0 && id.length <= 512 ? id : null;
  } catch {
    return null;
  }
}

export const deepLink = (id: string | null): string => (id ? `#node=${encodeURIComponent(id)}` : '');

/** What each region is called when the server didn't say. */
export const REGION_NAMES: Readonly<Record<RegionId, string>> = {
  core: 'Her',
  memory: 'Memory',
  working: 'Recent chats',
  agents: 'Helpers',
  knowledge: 'Knowledge',
  rim: 'Tools and connectors',
};

export const REGION_ORDER: readonly RegionId[] = ['core', 'memory', 'working', 'agents', 'knowledge', 'rim'];

export function regionName(map: MindMap | null, r: RegionId): string {
  return map?.regions.find((x) => x.id === r)?.label || REGION_NAMES[r];
}

/** Why a region is empty, in the words the stats line uses. */
const EMPTY: Readonly<Record<RegionId, string>> = {
  core: 'Her prompt has nothing in it yet',
  memory: 'No memories yet',
  working: 'No chats yet',
  agents: 'No helpers yet',
  knowledge: 'No persona files or skills in use',
  rim: 'No tools or connectors',
};

export function sourceNote(r: RegionId, status: SourceStatus, name: string): string | null {
  switch (status) {
    case 'ok':
      return null;
    case 'empty':
      return EMPTY[r];
    case 'no_meaning':
      return 'No memories by meaning: install the meaning extra';
    case 'error':
      return `${name} couldn't be read (see her log)`;
  }
}

export interface StatsLine {
  readonly summary: string;
  /** One sentence for each region that is empty or failed, and why. */
  readonly notes: readonly string[];
}

const plural = (n: number, one: string, many: string): string => `${n.toLocaleString('en')} ${n === 1 ? one : many}`;

export function statsLine(map: MindMap): StatsLine {
  const { memory_shown: shown, memory_total: total } = map.stats;
  const parts = [plural(map.nodes.length, 'node', 'nodes'), plural(map.edges.length, 'link', 'links')];
  parts.push(total > shown ? `${shown.toLocaleString('en')} of ${total.toLocaleString('en')} memories (the newest)` : plural(shown, 'memory', 'memories'));
  const notes: string[] = [];
  for (const r of REGION_ORDER) {
    const status = map.stats.sources[r];
    if (!status) continue;
    const note = sourceNote(r, status, regionName(map, r));
    if (note) notes.push(note);
  }
  return { summary: parts.join(' · '), notes };
}

/** Labels and ids that contain the query: a prefix beats a match inside, a short label a long one. */
export function rankMatches(nodes: readonly MindNode[], query: string, limit = 8): MindNode[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const scored: { n: MindNode; rank: number; len: number }[] = [];
  for (const n of nodes) {
    const label = n.label.toLowerCase();
    const id = n.id.toLowerCase();
    let rank: number;
    if (label.startsWith(q)) rank = 0;
    else if (id.startsWith(q) || id.slice(id.indexOf(':') + 1).startsWith(q)) rank = 1;
    else if (label.includes(q) || id.includes(q)) rank = 2;
    else continue;
    scored.push({ n, rank, len: label.length || id.length });
  }
  scored.sort((a, b) => a.rank - b.rank || a.len - b.len || a.n.id.localeCompare(b.n.id));
  return scored.slice(0, limit).map((s) => s.n);
}

const word = (v: unknown): string => (typeof v === 'string' ? v.replace(/_/g, ' ') : '');

/** The one line of detail under a tooltip's label. */
export function nodeDetail(n: MindNode): string {
  const x = n.extra;
  switch (n.type) {
    case 'memory':
      return [word(x['kind']), word(x['source'])].filter(Boolean).join(' · ');
    case 'tool':
      return [word(x['category']), x['risk'] ? `${word(x['risk'])} risk` : ''].filter(Boolean).join(' · ');
    case 'connector':
      return word(x['status']);
    case 'helper':
      return [word(x['kind']), word(x['state'])].filter(Boolean).join(' · ');
    case 'knowledge':
      return word(x['kind']);
    case 'chat':
      return word(x['kind']);
    case 'ring':
      return typeof x['chars'] === 'number' ? `${x['chars'].toLocaleString('en')} characters` : '';
    case 'core':
      return '';
  }
}

/** The page's title: her name from the payload, never a name of ours. */
export const pageTitle = (agent: string): string => (agent ? `${agent}'s mind` : 'Her mind');
