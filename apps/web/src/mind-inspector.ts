/**
 * The Living Mind's side panel: one node's detail, loaded when it is clicked
 * (`GET /api/mind/node/{id}`). A memory lists the memories nearest it by meaning;
 * clicking one hops along the web. Every string from the server goes in as text,
 * never as markup: memory bodies are the user's own words.
 */

import type { MindNode } from '@hellotrillion/galaxy';
import { memoryWords } from './memories';

export type Detail = Readonly<Record<string, unknown>>;

export interface InspectorOptions {
  /** The detail for a node, or null when there is none (a 404). Throws when it couldn't be read (a 503). */
  readonly load: (id: string) => Promise<Detail | null>;
  /** The node as the skeleton has it, when it is drawn. */
  readonly node: (id: string) => MindNode | undefined;
  /** Go to another node: a similar memory, a connector's tool. */
  readonly hop: (id: string) => void;
  readonly close: () => void;
  /** A region's name, for the panel's heading. */
  readonly regionName?: (region: MindNode['region']) => string;
}

export interface Inspector {
  open(id: string): Promise<void>;
  hide(): void;
  readonly openId: string | null;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const when = (v: unknown): string | null => {
  const ms = num(v);
  return ms && ms > 0 ? new Date(ms).toLocaleString() : null;
};
const words = (v: unknown): string | null => str(v)?.replace(/_/g, ' ') ?? null;

/** A row of "name  value" pairs, leaving out the ones with no value. */
function facts(rows: readonly (readonly [string, string | null])[]): HTMLElement {
  const dl = el('dl', 'mind-facts');
  for (const [k, v] of rows) {
    if (!v) continue;
    dl.append(el('dt', '', k), el('dd', '', v));
  }
  return dl;
}

function list(title: string, items: readonly { label: string; id: string | null }[], hop: (id: string) => void): HTMLElement | null {
  if (!items.length) return null;
  const box = el('div', 'mind-list');
  box.append(el('h3', '', title));
  for (const item of items) {
    if (item.id) {
      const b = el('button', 'mind-hop', item.label);
      b.type = 'button';
      const id = item.id;
      b.addEventListener('click', () => hop(id));
      box.append(b);
    } else box.append(el('div', 'mind-item', item.label));
  }
  return box;
}

const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);

/** The panel's contents for one node's detail. */
export function renderDetail(node: MindNode, d: Detail, hop: (id: string) => void, has: (id: string) => boolean): HTMLElement[] {
  const out: HTMLElement[] = [];
  const badge = (text: string | null): void => {
    if (text) out.push(el('span', `mind-badge mind-badge-${node.region}`, text));
  };
  switch (node.type) {
    case 'memory': {
      // The Memories screen's own words for its types and sources.
      badge(memoryWords('type', d['kind']) ?? words(d['kind']) ?? 'Memory');
      out.push(el('p', 'mind-body', str(d['text']) ?? node.label));
      out.push(
        facts([
          ['Written', when(d['created_ms'])],
          ['True as of', when(d['as_of_ms'])],
          ['From', memoryWords('source', d['source']) ?? words(d['source'])],
          ['Version', num(d['version'])?.toString() ?? null],
        ]),
      );
      const similar = (Array.isArray(d['similar']) ? d['similar'] : []).flatMap((s) => {
        const o = s as Record<string, unknown>;
        const raw = str(o['id']);
        if (!raw) return [];
        const id = raw.startsWith('mem:') ? raw : `mem:${raw}`;
        const score = num(o['score']);
        const label = `${str(o['label']) ?? id}${score !== null ? `  ·  ${score.toFixed(2)}` : ''}`;
        return [{ label, id: has(id) ? id : null }];
      });
      const box = list('Similar memories', similar, hop);
      if (box) out.push(box);
      break;
    }
    case 'chat':
      badge(words(d['kind']) ?? 'chat');
      out.push(el('h2', '', str(d['title']) ?? node.label));
      out.push(facts([['Last active', when(d['last_ms'])], ['Messages', num(d['messages'])?.toLocaleString() ?? null]]));
      break;
    case 'helper': {
      badge(words(d['state']));
      out.push(el('h2', '', str(d['name']) ?? node.label));
      if (str(d['job'])) out.push(el('p', 'mind-body', str(d['job']) ?? ''));
      out.push(facts([['Doing', str(d['doing'])], ['Last run', when(d['last_run'])], ['Next', when(d['next_ms'])]]));
      const conns = strings(d['connections']).map((c) => ({ label: c, id: has(`conn:${c}`) ? `conn:${c}` : null }));
      const box = list('Connections', conns, hop);
      if (box) out.push(box);
      break;
    }
    case 'knowledge': {
      badge(words(d['kind']) ?? words(node.extra['kind']));
      out.push(el('h2', '', str(d['path']) ?? node.label));
      out.push(facts([['Changed', when(d['modified_ms'])], ['Characters', num(d['chars'])?.toLocaleString() ?? null]]));
      const preview = str(d['text']);
      if (preview) out.push(el('pre', 'mind-preview', preview));
      break;
    }
    case 'tool':
      badge(words(d['category']));
      out.push(el('h2', '', str(d['label']) ?? node.label));
      if (str(d['description'])) out.push(el('p', 'mind-body', str(d['description']) ?? ''));
      out.push(
        facts([
          ['Risk', words(d['risk'])],
          ['Can be undone', typeof d['recoverable'] === 'boolean' ? (d['recoverable'] ? 'Yes' : 'No') : null],
        ]),
      );
      break;
    case 'connector': {
      badge(words(d['status']));
      out.push(el('h2', '', str(d['label']) ?? node.label));
      const tools = strings(d['tools']).map((t) => ({ label: t, id: has(`tool:${t}`) ? `tool:${t}` : null }));
      const box = list('Tools', tools, hop);
      if (box) out.push(box);
      break;
    }
    case 'core':
    case 'ring': {
      out.push(el('h2', '', str(d['agent']) ?? str(d['name']) ?? node.label));
      const rows: [string, string | null][] = [];
      for (const [k, v] of Object.entries(d)) {
        if (k === 'agent' || k === 'name' || k === 'id' || k === 'type') continue;
        if (typeof v === 'number') rows.push([words(k) ?? k, v.toLocaleString()]);
        else if (typeof v === 'string') rows.push([words(k) ?? k, v]);
        else if (Array.isArray(v)) rows.push([words(k) ?? k, strings(v).join(', ')]);
        else if (v && typeof v === 'object') {
          for (const [k2, v2] of Object.entries(v)) if (typeof v2 === 'number' || typeof v2 === 'string') rows.push([words(k2) ?? k2, String(v2)]);
        }
      }
      out.push(facts(rows));
      break;
    }
  }
  return out;
}

export function createInspector(root: HTMLElement, opts: InspectorOptions): Inspector {
  const head = el('div', 'mind-inspector-head');
  const region = el('span', 'mind-inspector-region');
  const closeBtn = el('button', 'mind-inspector-close', '×');
  closeBtn.type = 'button';
  closeBtn.setAttribute('aria-label', 'Close');
  closeBtn.addEventListener('click', () => opts.close());
  head.append(region, closeBtn);
  const title = el('h1', 'mind-inspector-title');
  const body = el('div', 'mind-inspector-body');
  root.replaceChildren(head, title, body);

  let openId: string | null = null;
  const inspector: Inspector = {
    get openId() {
      return openId;
    },
    async open(id) {
      openId = id;
      const node = opts.node(id);
      root.hidden = false;
      root.classList.add('open');
      region.textContent = node ? (opts.regionName?.(node.region) ?? node.region) : '';
      title.textContent = node?.label ?? id;
      body.replaceChildren(el('p', 'mind-muted', 'Loading…'));
      let detail: Detail | null = null;
      let failed = false;
      try {
        detail = await opts.load(id);
      } catch {
        failed = true;
      }
      // Hopped elsewhere while it loaded: this answer is for a panel no longer shown.
      if (openId !== id) return;
      if (failed) {
        body.replaceChildren(el('p', 'mind-muted', "Couldn't load this one right now."));
        return;
      }
      if (!node || !detail) {
        body.replaceChildren(el('p', 'mind-muted', 'No detail for this one.'));
        return;
      }
      body.replaceChildren(...renderDetail(node, detail, opts.hop, (x) => opts.node(x) !== undefined));
    },
    hide() {
      openId = null;
      root.classList.remove('open');
      root.hidden = true;
    },
  };
  inspector.hide();
  return inspector;
}
