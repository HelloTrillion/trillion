/**
 * GAL-10, the Living Mind page (mind.html): fetches the skeleton, draws it with the
 * Galaxy's `createMind`, and keeps it live from `/ws/observe`. This file owns the HUD,
 * search, legend, tooltip and inspector; three.js stays in the Galaxy package.
 * docs/specs/2026-11-08-living-mind.md
 */

import '@hellotrillion/galaxy/tokens.css';
import './mind.css';
import { type Mind, type MindMap, type ObserveEvent, type RegionId, createMind, parseMindMap } from '@hellotrillion/galaxy';
import { type Detail, createInspector } from './mind-inspector';
import { type Live, type LiveState, connectObserve } from './mind-live';
import { REGION_ORDER, deepLink, deepLinkNode, nodeDetail, pageTitle, rankMatches, regionName, statsLine, takeToken } from './mind-page';

// In development the page comes from Vite and the core runs beside it, as for the main page.
const CORE = import.meta.env.DEV ? '127.0.0.1:7777' : location.host;
const API = import.meta.env.DEV ? `http://${CORE}` : '';
const WS_URL = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${CORE}/ws/observe`;

const $ = <T extends HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing #${id}`);
  return el as T;
};

const canvas = $<HTMLCanvasElement>('mind-canvas');
const titleEl = $('mind-title');
const statsEl = $('mind-stats');
const notesEl = $('mind-notes');
const liveEl = $('mind-live');
const legendEl = $('mind-legend');
const searchEl = $<HTMLInputElement>('mind-search');
const resultsEl = $('mind-results');
const tooltipEl = $('mind-tooltip');
const unreachableEl = $('mind-unreachable');
const whyEl = $('mind-unreachable-why');

function storage(): Storage | null {
  try {
    return sessionStorage;
  } catch {
    return null;
  }
}

const taken = takeToken(location.hash, storage());
if (taken.hash !== location.hash) history.replaceState(null, '', `${location.pathname}${location.search}${taken.hash}`);
const token = taken.token;

interface Source {
  skeleton(): Promise<MindMap>;
  detail(id: string): Promise<Detail | null>;
  live(onEvent: (e: ObserveEvent) => void, onState: (s: LiveState) => void): Live | null;
}

class Unreachable extends Error {}

/** The server, with the bearer token like every /api/ route. */
function serverSource(tok: string): Source {
  const get = (path: string): Promise<Response> => fetch(`${API}${path}`, { headers: { authorization: `Bearer ${tok}` } });
  return {
    async skeleton() {
      let res: Response;
      try {
        res = await get('/api/mind');
      } catch {
        throw new Unreachable("She isn't answering. Is Trillion still running on this computer?");
      }
      if (res.status === 401 || res.status === 403) throw new Unreachable('This page has no valid sign-in. Open it again from Settings → Her mind.');
      if (!res.ok) throw new Unreachable(`She answered, but not with her mind (status ${res.status}).`);
      const map = parseMindMap(await res.json().catch(() => null));
      if (!map) throw new Unreachable('She answered with something that is not a map of her mind.');
      return map;
    },
    async detail(id) {
      const res = await get(`/api/mind/node/${encodeURIComponent(id)}`);
      if (!res.ok) return null;
      const body: unknown = await res.json().catch(() => null);
      return body && typeof body === 'object' && !Array.isArray(body) ? (body as Detail) : null;
    },
    live: (onEvent, onState) => connectObserve({ url: WS_URL, token: tok, onEvent, onState }),
  };
}

/** Development only: made-up data, never in a production build (`import.meta.env.DEV` is false there). */
async function fixtureSource(events: boolean): Promise<Source> {
  const f = await import('./mind-fixture');
  const map = parseMindMap(f.fixtureSkeleton());
  if (!map) throw new Unreachable('The fixture is not a map.');
  return {
    skeleton: async () => map,
    detail: async (id) => f.fixtureDetail(id, map),
    live(onEvent, onState) {
      onState('live');
      const stop = events ? f.fixtureEvents(map, onEvent) : () => undefined;
      (window as unknown as { mindFixture: unknown }).mindFixture = { fire: onEvent };
      return { visibility: () => undefined, close: stop };
    },
  };
}

function showUnreachable(why: string, retry: (() => void) | null): void {
  whyEl.textContent = why;
  unreachableEl.hidden = false;
  const btn = $<HTMLButtonElement>('mind-retry');
  btn.hidden = retry === null;
  btn.onclick = retry;
}

const LIVE_WORDS: Readonly<Record<LiveState, string>> = {
  connecting: 'Connecting to her live activity…',
  live: 'Live',
  retrying: 'Live activity lost; trying again',
  refused: 'Live activity refused: open the page again from Settings',
  closed: '',
};

async function start(source: Source): Promise<void> {
  unreachableEl.hidden = true;
  let map: MindMap;
  try {
    map = await source.skeleton();
  } catch (e) {
    showUnreachable(e instanceof Unreachable ? e.message : "She isn't answering.", () => void start(source));
    return;
  }

  let refetching: ReturnType<typeof setTimeout> | null = null;
  let mind: Mind;
  try {
    mind = createMind(canvas, {
      labels: $('mind-labels'),
      lowPower: matchMedia('(pointer: coarse)').matches && Math.min(innerWidth, innerHeight) < 600,
      onHover: (id, at) => hover(id, at),
      onSelect: (id) => select(id),
      onOverview: () => overview(),
      onRefetch: () => {
        // A burst of writes reads the skeleton once.
        if (refetching) clearTimeout(refetching);
        refetching = setTimeout(() => {
          refetching = null;
          source.skeleton().then(draw, () => undefined);
        }, 400);
      },
    });
  } catch {
    showUnreachable("This browser can't draw her mind: WebGL is turned off or not available.", null);
    return;
  }

  const inspector = createInspector($('mind-inspector'), {
    load: (id) => source.detail(id),
    node: (id) => mind.node(id),
    hop: (id) => select(id),
    close: () => overview(),
    regionName: (r) => regionName(map, r),
  });

  function draw(next: MindMap): void {
    map = next;
    mind.setData(next);
    titleEl.textContent = pageTitle(next.agent);
    document.title = pageTitle(next.agent);
    const stats = statsLine(next);
    statsEl.textContent = stats.summary;
    notesEl.replaceChildren(...stats.notes.map((n) => Object.assign(document.createElement('li'), { textContent: n })));
    drawLegend();
    if (inspector.openId && !mind.has(inspector.openId)) overview();
  }

  function drawLegend(): void {
    legendEl.replaceChildren(
      ...REGION_ORDER.filter((r) => map.nodes.some((n) => n.region === r)).map((r: RegionId) => {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = `mind-chip mind-chip-${r}`;
        chip.setAttribute('aria-pressed', String(mind.regionVisible(r)));
        const dot = document.createElement('span');
        dot.className = 'mind-chip-dot';
        chip.append(dot, document.createTextNode(regionName(map, r)));
        chip.addEventListener('click', () => {
          const on = !mind.regionVisible(r);
          mind.setRegionVisible(r, on);
          chip.setAttribute('aria-pressed', String(on));
        });
        return chip;
      }),
    );
  }

  function hover(id: string | null, at: { x: number; y: number } | null): void {
    const n = id ? mind.node(id) : undefined;
    if (!n || !at) {
      tooltipEl.hidden = true;
      return;
    }
    const [region, label, detail] = tooltipEl.children;
    if (region) region.textContent = regionName(map, n.region);
    if (label) label.textContent = n.type === 'core' && map.agent ? map.agent : n.label;
    if (detail) detail.textContent = nodeDetail(n);
    tooltipEl.hidden = false;
    tooltipEl.style.left = `${Math.min(at.x + 14, innerWidth - 300)}px`;
    tooltipEl.style.top = `${at.y + 14}px`;
  }

  function select(id: string): void {
    if (!mind.focus(id)) return;
    history.replaceState(null, '', `${location.pathname}${location.search}${deepLink(id)}`);
    void inspector.open(id);
  }

  function overview(): void {
    inspector.hide();
    mind.focus(null);
    history.replaceState(null, '', `${location.pathname}${location.search}`);
  }

  // Search flies to the best match on Enter; the list shows the next few.
  let matches: ReturnType<typeof rankMatches> = [];
  const showResults = (): void => {
    matches = rankMatches(map.nodes.filter((n) => mind.regionVisible(n.region)), searchEl.value);
    resultsEl.replaceChildren(
      ...matches.map((n) => {
        const li = document.createElement('li');
        li.setAttribute('role', 'option');
        li.textContent = n.type === 'core' && map.agent ? map.agent : n.label;
        li.dataset['region'] = n.region;
        li.addEventListener('mousedown', (ev) => {
          ev.preventDefault();
          select(n.id);
          searchEl.blur();
        });
        return li;
      }),
    );
    resultsEl.hidden = matches.length === 0;
  };
  searchEl.addEventListener('input', showResults);
  searchEl.addEventListener('blur', () => {
    resultsEl.hidden = true;
  });
  searchEl.addEventListener('keydown', (ev) => {
    // Typing never drives the scene.
    ev.stopPropagation();
    if (ev.key === 'Enter') {
      showResults();
      const best = matches[0];
      if (best) {
        select(best.id);
        searchEl.blur();
      }
    } else if (ev.key === 'Escape') searchEl.blur();
  });

  addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') overview();
    else if (ev.key === '/' && document.activeElement !== searchEl) {
      ev.preventDefault();
      searchEl.focus();
    }
  });

  draw(map);
  const first = deepLinkNode(location.hash);
  if (first) {
    if (mind.has(first)) select(first);
    else history.replaceState(null, '', `${location.pathname}${location.search}`);
  }

  const live = source.live(
    (event) => {
      // Absent nodes are dropped inside: nothing is drawn for them.
      mind.apply(event);
    },
    (state) => {
      liveEl.textContent = LIVE_WORDS[state];
      liveEl.dataset['state'] = state;
    },
  );
  document.addEventListener('visibilitychange', () => live?.visibility(document.hidden));
  addEventListener('pagehide', () => {
    live?.close();
    mind.dispose();
  });
}

const params = new URLSearchParams(location.search);
if (import.meta.env.DEV && params.has('fixture')) {
  void fixtureSource(params.has('events')).then(start);
} else if (!token) {
  showUnreachable('This page has no sign-in. Open it from Settings → Her mind in Trillion.', null);
} else {
  void start(serverSource(token));
}
