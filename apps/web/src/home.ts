/**
 * Home (SUR-1a): the empty workspace's morning screen. It greets the user, offers to build
 * today's briefing, and shows the nine widgets a briefing can hold, each either ready or naming
 * the one service that would fill it, with a button straight into that service's sign-in.
 *
 * The core decides every tile's state (GET /api/workspace/home); this page only draws it. The
 * previews are the widget kit's samples, labelled "Sample" and hidden from screen readers, so a
 * made-up number never passes for the user's own. Everything is set with textContent.
 */
import { type DrawnBlocks, drawBlocks, h, str } from './blocks';
import { mark } from './connected';
import SAMPLES from './kit/samples.json';

// ── What the route sends ────────────────────────────────────────────────

export type TileState = 'ready' | 'asks_first' | 'connect' | 'built_in';
export type ServiceState = 'ready' | 'asks_first' | 'connect';

export interface HomeService {
  readonly id: string;
  readonly title: string;
  readonly color: string;
  readonly glyph: string;
  readonly how: string;
  readonly category: string;
  readonly state: ServiceState;
  readonly settings_key: string;
}

export interface HomeTile {
  readonly id: string;
  readonly title: string;
  readonly type: string;
  readonly what: string;
  readonly state: TileState;
  readonly services: readonly HomeService[];
  readonly more: number;
}

export interface HomeNext {
  readonly tile: string;
  readonly service: string;
  readonly text: string;
}

export interface HomeData {
  readonly user_name: string;
  readonly agent_name: string;
  readonly briefing: 'build' | 'open';
  readonly ready: number;
  readonly total: number;
  readonly next: HomeNext | null;
  readonly tiles: readonly HomeTile[];
}

const rec = (v: unknown): Record<string, unknown> | null =>
  typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? Math.max(0, Math.floor(v)) : 0);
const TILE_STATES: readonly TileState[] = ['ready', 'asks_first', 'connect', 'built_in'];

function serviceOf(raw: unknown): HomeService | null {
  const r = rec(raw);
  const id = str(r?.['id']);
  if (!r || !id) return null;
  const state = r['state'] === 'ready' || r['state'] === 'asks_first' ? r['state'] : 'connect';
  return {
    id,
    title: str(r['title']) || id,
    color: str(r['color']),
    glyph: str(r['glyph']),
    how: str(r['how']),
    category: str(r['category']),
    state,
    settings_key: str(r['settings_key']),
  };
}

function tileOf(raw: unknown): HomeTile | null {
  const r = rec(raw);
  const id = str(r?.['id']);
  if (!r || !id) return null;
  const state = TILE_STATES.includes(r['state'] as TileState) ? (r['state'] as TileState) : 'connect';
  const services = Array.isArray(r['services']) ? r['services'].map(serviceOf).filter((s): s is HomeService => s !== null) : [];
  return { id, title: str(r['title']) || id, type: str(r['type']) || id, what: str(r['what']), state, services, more: num(r['more']) };
}

/** The route's answer, checked: anything malformed is left out rather than drawn wrong. Null for no answer at all. */
export function homeOf(raw: unknown): HomeData | null {
  const r = rec(raw);
  if (!r || !Array.isArray(r['tiles'])) return null;
  const tiles = r['tiles'].map(tileOf).filter((t): t is HomeTile => t !== null);
  const n = rec(r['next']);
  const next = n && str(n['service']) ? { tile: str(n['tile']), service: str(n['service']), text: str(n['text']) } : null;
  return {
    user_name: str(r['user_name']).trim(),
    agent_name: str(r['agent_name']).trim(),
    briefing: r['briefing'] === 'open' ? 'open' : 'build',
    ready: num(r['ready']),
    total: num(r['total']) || tiles.length,
    next,
    tiles,
  };
}

// ── Words, tested on their own ──────────────────────────────────────────

/** "Good morning, Sam": afternoon from 12:00, evening from 17:00, local time. No name, no comma. */
export function greeting(hour: number, name: string): string {
  const part = hour >= 17 ? 'evening' : hour >= 12 ? 'afternoon' : 'morning';
  const who = name.trim();
  return who ? `Good ${part}, ${who}` : `Good ${part}`;
}

/** "Friday, October 30", in the user's own language. */
export function dateLine(d: Date): string {
  return d.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' });
}

/** "Gmail", "Gmail and Slack", "Linear, Todoist and Notion". */
export function joinNames(names: readonly string[], last = 'and'): string {
  if (names.length <= 1) return names[0] ?? '';
  return `${names.slice(0, -1).join(', ')} ${last} ${names[names.length - 1]}`;
}

const readyFrom = (t: HomeTile): HomeService[] => t.services.filter((s) => s.state === 'ready');
const asking = (t: HomeTile): HomeService | undefined => t.services.find((s) => s.state === 'asks_first');

/** What a screen reader hears for a tile: "Inbox: ready, from Gmail", "Mentions: not connected. Connect Slack". */
export function tileLabel(t: HomeTile): string {
  switch (t.state) {
    case 'ready': {
      const from = joinNames(readyFrom(t).map((s) => s.title));
      return from ? `${t.title}: ready, from ${from}` : `${t.title}: ready`;
    }
    case 'built_in':
      return `${t.title}: built in`;
    case 'asks_first': {
      const s = asking(t);
      return s ? `${t.title}: needs your OK. ${s.title} asks you each time` : `${t.title}: needs your OK`;
    }
    default: {
      const names = t.services.slice(0, 3).map((s) => s.title);
      const more = Math.max(t.more, t.services.length - names.length);
      if (more > 0) names.push(`${more} more`);
      const which = joinNames(names, 'or');
      return which ? `${t.title}: not connected. Connect ${which}` : `${t.title}: not connected`;
    }
  }
}

export const BADGE: Record<TileState, string> = {
  ready: 'Ready',
  built_in: 'Built in',
  asks_first: 'Needs your OK',
  connect: 'Not connected',
};

/** After a refresh, what changed, in a line for the live region ("Mentions is ready"); empty for nothing. */
export function changeLine(before: HomeData | null, after: HomeData): string {
  if (!before) return '';
  const was = new Map(before.tiles.map((t) => [t.id, t.state]));
  const lines: string[] = [];
  for (const t of after.tiles) {
    const old = was.get(t.id);
    if (old === undefined || old === t.state) continue;
    if (t.state === 'ready' || t.state === 'built_in') lines.push(`${t.title} is ready`);
    else if (t.state === 'asks_first') lines.push(`${t.title} needs your OK`);
    else lines.push(`${t.title} is not connected`);
  }
  return lines.length ? `${lines.join('. ')}.` : '';
}

/** A service's colour for the tile's border, as a CSS colour; empty for anything that is not one. */
export function brandColor(color: string): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(color.trim());
  return m ? `#${m[1]}` : '';
}

// ── Once a day (D2) ─────────────────────────────────────────────────────

/** The local date as YYYY-MM-DD: the day the user is living in, not UTC's. */
export function localDate(d: Date): string {
  const p = (n: number): string => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** Whether this page load opens the workspace to Home: the first load of a date, unless turned off or something else has the stage. */
export function shouldOpenHome(lastDate: string | null, today: string, enabled: boolean, onboarding: boolean, boardOpen: boolean): boolean {
  return enabled && !onboarding && !boardOpen && lastDate !== today;
}

// ── The samples ─────────────────────────────────────────────────────────

interface Sample {
  readonly type: string;
  readonly title?: unknown;
  readonly source?: unknown;
  readonly blocks?: unknown;
}

const BY_TYPE = new Map<string, Sample>();
for (const w of (SAMPLES as unknown as { widgets: readonly Sample[] }).widgets) if (!BY_TYPE.has(w.type)) BY_TYPE.set(w.type, w);

/** The kit's sample widget of a type, if it has one. */
export function sampleFor(type: string): Sample | undefined {
  return BY_TYPE.get(type);
}

/** The moment a sample was made at, so its "in 25 min" reads as it was written; else now. */
function sampleNow(s: Sample, now: Date): Date {
  const at = new Date(str(rec(s.source)?.['at']));
  return Number.isNaN(at.getTime()) ? now : at;
}

// ── Drawing ─────────────────────────────────────────────────────────────

export interface HomeOptions {
  /** GET /api/workspace/home, parsed; a rejection draws the failure state. */
  readonly fetchHome: () => Promise<unknown>;
  /** Build my briefing, or open it (D9). */
  readonly onBuild: () => void;
  /** One service's sign-in, by catalogue id (D6). */
  readonly onConnect: (catalogId: string) => void;
  /** "+N more": Connected tools, showing that kind of app. */
  readonly onBrowse: (category: string) => void;
  /** "Let her read it without asking": that connection's card (D7). */
  readonly onReveal: (settingsKey: string) => void;
  readonly now?: () => Date;
  readonly reducedMotion?: () => boolean;
  /** Says a line politely; by default Home's own live region. */
  readonly announce?: (text: string) => void;
}

export interface Home {
  readonly el: HTMLElement;
  /** Home is shown: tiles arrive afresh and the state is read again. */
  show(): Promise<void>;
  /** Connections changed: read the state again, if Home is on screen. */
  refresh(): Promise<void>;
}

const PLACEHOLDERS = 9;

function btn(text: string, cls: string, onClick: () => void): HTMLButtonElement {
  const b = h('button', cls, text);
  b.type = 'button';
  b.addEventListener('click', onClick);
  return b;
}

export function createHome(opts: HomeOptions): Home {
  const clock = opts.now ?? ((): Date => new Date());
  const still = opts.reducedMotion ?? ((): boolean => matchMedia('(prefers-reduced-motion: reduce)').matches);

  const el = h('div', 'home');
  el.setAttribute('aria-label', 'Home');

  // Header: the date, the greeting, one line, and the button.
  const head = h('header', 'home-head');
  const date = h('p', 'home-date');
  const hello = h('h3', 'home-hello');
  const lede = h('p', 'home-lede', 'Here’s what your briefing can show. Connect more and she’ll see more.');
  const build = btn('Build my briefing', 'home-build', () => {
    if (build.disabled) return;
    build.disabled = true;
    build.setAttribute('aria-busy', 'true');
    build.textContent = data?.briefing === 'open' ? 'Opening your briefing…' : 'Building your briefing…';
    opts.onBuild();
  });
  head.append(date, hello, lede, build);

  // The meter: how many are ready, and the one next step (D8).
  const meterBox = h('section', 'home-meter');
  meterBox.setAttribute('aria-label', 'What is ready');
  const meter = h('meter', 'home-meter-bar');
  meter.min = 0;
  const count = h('span', 'home-meter-count');
  const meterTop = h('div', 'home-meter-top');
  meterTop.append(count, meter);
  const next = h('div', 'home-next');
  meterBox.append(meterTop, next);

  const grid = h('ul', 'home-grid');
  grid.setAttribute('aria-label', 'What your briefing can show');
  const foot = h('p', 'home-foot', 'Want something else on your board? Just ask her.');

  // Without a region from the page, Home speaks through its own.
  const live = h('p', 'visually-hidden');
  live.setAttribute('role', 'status');
  live.setAttribute('aria-live', 'polite');
  const say = opts.announce ?? ((text: string): void => void (live.textContent = text));

  el.append(head, meterBox, grid, foot, live);

  let data: HomeData | null = null;
  let previews: Omit<DrawnBlocks, 'el'>[] = [];
  // Counts reads, so an older answer arriving after a newer one is dropped.
  let gen = 0;
  // A show whose read was overtaken by a refresh still has its tiles arrive.
  let arriving = false;

  function drawHead(): void {
    const now = clock();
    date.textContent = dateLine(now);
    hello.textContent = greeting(now.getHours(), data?.user_name ?? '');
    build.disabled = false;
    build.removeAttribute('aria-busy');
    build.textContent = data?.briefing === 'open' ? 'Open today’s briefing' : 'Build my briefing';
  }

  function drawMeter(): void {
    if (!data) {
      meterBox.hidden = true;
      return;
    }
    meterBox.hidden = false;
    meter.max = Math.max(1, data.total);
    meter.value = Math.min(data.ready, data.total);
    const words = `${data.ready} of ${data.total} ready`;
    meter.textContent = words;
    meter.setAttribute('aria-label', words);
    count.textContent = words;
    const n = data.next;
    if (!n) {
      // No suggestion can also mean only asks-first tiles are left: then the meter says enough.
      next.hidden = data.ready < data.total;
      next.replaceChildren(...(next.hidden ? [] : [h('p', 'home-next-text home-next-done', 'Everything’s connected')]));
      return;
    }
    next.hidden = false;
    const svc = data.tiles.flatMap((t) => t.services).find((s) => s.id === n.service);
    const go = h('button', 'home-connect home-connect-next');
    go.type = 'button';
    if (svc) go.append(mark(svc.color, svc.glyph, 'xs', svc.id));
    go.append(svc ? `Connect ${svc.title}` : 'Connect');
    go.addEventListener('click', () => opts.onConnect(n.service));
    next.replaceChildren(h('p', 'home-next-text', n.text), go);
  }

  function connectButton(s: HomeService, cls: string): HTMLButtonElement {
    const b = h('button', `home-connect ${cls}`);
    b.type = 'button';
    b.append(mark(s.color, s.glyph, 'xs', s.id));
    b.addEventListener('click', () => opts.onConnect(s.id));
    return b;
  }

  function tileFoot(t: HomeTile): HTMLElement {
    const f = h('div', 'home-tile-foot');
    if (t.state === 'ready') {
      const from = readyFrom(t);
      const marks = h('span', 'home-marks');
      for (const s of from) marks.append(mark(s.color, s.glyph, 'xs', s.id));
      f.append(marks, h('span', 'home-from', `From ${joinNames(from.map((s) => s.title))}`));
    } else if (t.state === 'built_in') {
      f.append(h('span', 'home-from', 'Built in, nothing to connect'));
    } else if (t.state === 'asks_first') {
      const s = asking(t);
      f.append(h('p', 'home-why', `${s?.title ?? 'This service'} asks you each time, so the briefing leaves it out`));
      if (s?.settings_key) f.append(btn('Let her read it without asking', 'home-reveal', () => opts.onReveal(s.settings_key)));
    } else {
      // The route sends every service; three are shown and the rest are "+N more".
      const shown = t.services.slice(0, 3);
      const more = Math.max(t.more, t.services.length - shown.length);
      if (shown.length === 1 && more === 0) {
        const s = shown[0]!;
        const b = connectButton(s, 'home-connect-one');
        b.append(`Connect ${s.title}`);
        f.append(b);
      } else {
        const row = h('div', 'home-choices');
        for (const s of shown) {
          const b = connectButton(s, 'home-connect-pick');
          b.append(s.title);
          b.setAttribute('aria-label', `Connect ${s.title}`);
          row.append(b);
        }
        if (more > 0) {
          // The kind of the first one not shown, so the list opens where the rest are.
          const category = t.services[3]?.category ?? shown[0]?.category ?? '';
          const rest = btn(`+${more} more`, 'home-more', () => opts.onBrowse(category));
          rest.setAttribute('aria-label', `${more} more ways to fill ${t.title}`);
          row.append(rest);
        }
        f.append(row);
      }
    }
    return f;
  }

  function preview(t: HomeTile): HTMLElement {
    const box = h('div', 'home-preview');
    // A picture of what it could look like: nothing in it is read out or reachable by keyboard.
    box.setAttribute('aria-hidden', 'true');
    box.inert = true;
    const frame = h('div', 'home-preview-frame');
    const s = sampleFor(t.type);
    if (s) {
      try {
        const drawn = drawBlocks(Array.isArray(s.blocks) ? (s.blocks as unknown[]) : [], {
          now: sampleNow(s, clock()),
          chartHeight: 120,
          title: str(s.title),
        });
        frame.append(drawn.el);
        previews.push(drawn);
      } catch (err) {
        // One sample that cannot be drawn leaves its frame empty, never the rest of Home.
        console.warn(`The ${t.type} sample could not be drawn`, err);
      }
    }
    for (const f of frame.querySelectorAll<HTMLElement>('a, button, input, select, textarea, [tabindex]')) f.tabIndex = -1;
    box.append(frame, h('span', 'home-sample', 'Sample'));
    return box;
  }

  function tile(t: HomeTile, i: number, animate: boolean): HTMLElement {
    const li = h('li', 'home-tile');
    li.dataset['state'] = t.state;
    li.dataset['id'] = t.id;
    li.setAttribute('aria-label', tileLabel(t));
    // A ready tile's border picks up its first service's own colour.
    const brand = brandColor(readyFrom(t)[0]?.color ?? '');
    if (brand) li.style.setProperty('--brand', brand);
    if (animate) {
      li.classList.add('home-in');
      li.style.setProperty('--i', String(i));
    }
    const top = h('header', 'home-tile-head');
    const words = h('div', 'home-tile-words');
    words.append(h('h4', 'home-tile-title', t.title), h('p', 'home-tile-what', t.what));
    const badge = h('span', 'home-badge', BADGE[t.state]);
    badge.dataset['tone'] = t.state;
    top.append(words, badge);
    li.append(top, preview(t), tileFoot(t));
    return li;
  }

  function disposePreviews(): void {
    for (const p of previews.splice(0)) p.dispose();
  }

  function drawGrid(animate: boolean): void {
    disposePreviews();
    grid.removeAttribute('aria-busy');
    if (!data) return;
    grid.replaceChildren(...data.tiles.map((t, i) => tile(t, i, animate)));
  }

  function drawLoading(): void {
    disposePreviews();
    grid.setAttribute('aria-busy', 'true');
    const quiet: HTMLElement[] = [];
    for (let i = 0; i < PLACEHOLDERS; i++) {
      const li = h('li', 'home-tile home-tile-wait');
      li.setAttribute('aria-hidden', 'true');
      quiet.push(li);
    }
    grid.replaceChildren(...quiet);
  }

  function drawFailed(): void {
    disposePreviews();
    grid.removeAttribute('aria-busy');
    const li = h('li', 'home-failed');
    li.append(h('p', 'home-failed-text', 'Couldn’t check what’s connected.'), btn('Try again', 'home-retry', () => void read(true, true)));
    grid.replaceChildren(li);
  }

  /** Read the state and draw it. `fresh`: tiles arrive afresh (a show, not a refresh). */
  async function read(fresh: boolean, retry = false): Promise<void> {
    const mine = ++gen;
    arriving ||= fresh;
    if (!data || retry) {
      drawLoading();
      meterBox.hidden = true;
    }
    let got: HomeData | null = null;
    try {
      got = homeOf(await opts.fetchHome());
    } catch {
      got = null;
    }
    if (mine !== gen) return;
    const arrive = arriving || !data;
    arriving = false;
    if (!got) {
      // A refresh that failed keeps what was there; with nothing to keep, say so.
      if (!data || retry) {
        data = null;
        drawHead();
        drawMeter();
        drawFailed();
      }
      return;
    }
    const line = arrive ? '' : changeLine(data, got);
    data = got;
    drawHead();
    drawMeter();
    drawGrid(arrive && !still());
    if (line) say(line);
  }

  // Before the first read, the header and quiet placeholders, so nothing jumps.
  drawHead();
  drawMeter();
  drawLoading();

  return {
    el,
    show: () => read(true),
    refresh: () => (el.isConnected ? read(false) : Promise.resolve()),
  };
}
