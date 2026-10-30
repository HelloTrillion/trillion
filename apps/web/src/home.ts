/**
 * Home (SUR-1a): the empty workspace's morning screen. It greets the user, offers to build
 * today's briefing, and shows the nine widgets a briefing can hold, each either ready or naming
 * the one service that would fill it, with a button straight into that service's sign-in.
 *
 * The core decides every tile's state (GET /api/workspace/home); this page only draws it. The
 * previews are the widget kit's samples, labelled "Sample" and hidden from screen readers, so a
 * made-up number never passes for the user's own. Everything is set with textContent.
 */
import { attachDrag, keyStep, moved } from './arrange';
import { type DrawnBlocks, drawBlocks, h, str } from './blocks';
import { mark } from './connected';
import { icon } from './icons';
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
  /** Tiles the user took off Home, in default order: "Add widgets" lists them (D14). */
  readonly removed: readonly HomeTile[];
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
  // A core from before arranging sends no `removed`: nothing is.
  const shown = new Set(tiles.map((t) => t.id));
  const removed = (Array.isArray(r['removed']) ? r['removed'] : []).map(tileOf).filter((t): t is HomeTile => t !== null && !shown.has(t.id));
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
    removed,
  };
}

// ── Arranging (D13, D14), pure ──────────────────────────────────────────

/** The nine tiles in their default order (D3). */
export const DEFAULT_ORDER: readonly string[] = ['next_up', 'free_time', 'inbox', 'tasks', 'mentions', 'due_soon', 'kpis', 'files', 'checks'];

/** What PUT /api/workspace/home/layout takes. */
export interface Layout {
  readonly order: readonly string[];
  readonly removed: readonly string[];
}

const rank = (id: string): number => {
  const i = DEFAULT_ORDER.indexOf(id);
  return i < 0 ? DEFAULT_ORDER.length : i;
};
/** Tiles in default order; one the page does not know keeps its place after the rest. */
const byDefault = (ts: readonly HomeTile[]): HomeTile[] => [...ts].sort((a, b) => rank(a.id) - rank(b.id));
const counted = (ts: readonly HomeTile[]): number => ts.filter((t) => t.state === 'ready' || t.state === 'built_in').length;

export function layoutOf(d: HomeData): Layout {
  return { order: d.tiles.map((t) => t.id), removed: d.removed.map((t) => t.id) };
}

/**
 * Home with a new layout, drawn before the core answers. The meter counts only tiles shown,
 * as the core does, and the suggestion goes with a tile that was removed.
 */
export function withLayout(d: HomeData, order: readonly string[], removed: readonly string[]): HomeData {
  const pool = new Map([...d.tiles, ...d.removed].map((t) => [t.id, t]));
  const gone = new Set(removed.filter((id) => pool.has(id)));
  const tiles: HomeTile[] = [];
  const seen = new Set<string>();
  for (const id of order) {
    const t = pool.get(id);
    if (t && !gone.has(id) && !seen.has(id)) {
      seen.add(id);
      tiles.push(t);
    }
  }
  // A tile named nowhere stays shown, after the rest.
  for (const t of byDefault([...pool.values()])) if (!gone.has(t.id) && !seen.has(t.id)) tiles.push(t);
  return {
    ...d,
    tiles,
    removed: byDefault([...pool.values()].filter((t) => gone.has(t.id))),
    ready: Math.max(0, d.ready + counted(tiles) - counted(d.tiles)),
    total: Math.max(0, d.total + tiles.length - d.tiles.length),
    next: d.next && gone.has(d.next.tile) ? null : d.next,
  };
}

/** Whether Home is as it comes: default order, nothing removed. */
export function isDefaultLayout(d: HomeData): boolean {
  return d.removed.length === 0 && byDefault(d.tiles).every((t, i) => t === d.tiles[i]);
}

/** Every tile back, in default order (Reset Home). */
export function defaultLayout(d: HomeData): Layout {
  return { order: byDefault([...d.tiles, ...d.removed]).map((t) => t.id), removed: [] };
}

/** The order with a removed tile added back: after the last shown tile that comes before it by default. */
export function addedBack(order: readonly string[], id: string): string[] {
  const rest = order.filter((x) => x !== id);
  let at = 0;
  rest.forEach((x, i) => {
    if (rank(x) < rank(id)) at = i + 1;
  });
  return [...rest.slice(0, at), id, ...rest.slice(at)];
}

/** What the live region says as the keyboard grip moves a tile. */
export const pickedLine = (title: string, at: number, of: number): string => `${title}, picked up, position ${at} of ${of}`;
export const movedLine = (at: number, of: number): string => `moved to position ${at} of ${of}`;

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
  /** How long Build stays busy at most, so a build the core drops never leaves it stuck. */
  readonly buildTimeoutMs?: number;
  /** PUT /api/workspace/home/layout, answering with Home; a rejection puts the layout back. Without it, tiles stay put. */
  readonly saveLayout?: (layout: Layout) => Promise<unknown>;
  /** How long "Inbox removed" offers Undo. */
  readonly toastMs?: number;
}

export interface Home {
  readonly el: HTMLElement;
  /** Home is shown: tiles arrive afresh and the state is read again. */
  show(): Promise<void>;
  /** Connections changed: read the state again, if Home is on screen. */
  refresh(): Promise<void>;
  /** The build Home asked for is over (its board came, or it failed): Build rests again. */
  built(): void;
  /** The layout changed somewhere (a `home` frame): read it again without animation, after any drag. */
  changed(): Promise<void>;
}

/** The longest Build stays busy. */
export const BUILD_TIMEOUT_MS = 60_000;
/** A refresh this soon after a show still reading is that same read (the startup double). */
export const SHOW_SETTLE_MS = 500;

/** The line under the greeting: her name when there is one. */
export function ledeLine(agentName: string): string {
  const who = agentName.trim();
  return `Here’s what your briefing can show. Connect more and ${who ? `${who} will` : 'she’ll'} see more.`;
}

// ── main.ts's decisions about Home, pure so they can be tested ──────────

/**
 * A board frame arrived while the board view is up: keep it up (redraw) or close the workspace.
 * Her board open, an approval pinned, the Folders panel, or Home showing all keep it up.
 */
export function frameKeeps(f: { readonly boardOpen: boolean; readonly approvals: boolean; readonly folders: boolean; readonly homeShown: boolean }): boolean {
  return f.boardOpen || f.approvals || f.folders || f.homeShown;
}

/**
 * She arranged Home (a `home` frame from her): whether the page opens the workspace to show it.
 * Only into a closed workspace, and never over a board with widgets, an approval, the Folders
 * panel or onboarding. A workspace already showing Home redraws it in place.
 */
export function herOpensHome(f: {
  readonly by: unknown;
  readonly open: boolean;
  readonly boardWidgets: boolean;
  readonly approvals: boolean;
  readonly folders: boolean;
  readonly onboarding: boolean;
}): boolean {
  return f.by === 'her' && !f.open && !f.boardWidgets && !f.approvals && !f.folders && !f.onboarding;
}

/** Something closed in the workspace: whether Home comes back (Settings opened from Home, closed by itself). */
export function returnsToHome(fromHome: boolean, closedId: string | null, replaced: boolean): boolean {
  return fromHome && closedId !== 'board' && !replaced;
}

/** What to do with the day board Home's button asked for, as a board frame arrives. */
export type DayBoardStep =
  /** Nothing asked, or not the day's conversation: forget it. */
  | 'forget'
  /** Still empty: she is building it, Home stays. */
  | 'wait'
  /** Open with widgets: the board shows itself (onBoard). */
  | 'shown'
  /** Built but put away: open it again. */
  | 'open'
  /** Settings has the stage: the sidebar's chip says it is there. */
  | 'chip';

export function dayBoardStep(
  homeDay: string | null,
  conversation: string | null,
  board: { readonly open: boolean; readonly widgets: readonly unknown[] } | null,
  showing: string | null,
): DayBoardStep {
  if (homeDay === null || conversation !== homeDay) return 'forget';
  if (!board || board.widgets.length === 0) return 'wait';
  if (showing === 'settings') return 'chip';
  return board.open ? 'shown' : 'open';
}

/** Refusals that mean a build never started. */
const BUILD_REFUSALS = new Set(['no_model', 'busy', 'routine_building']);

/**
 * Whether a message ends the build Home asked for: the day's turn finished, a refusal of it,
 * or an error during that turn. Unrelated errors (the microphone, say) leave it alone.
 */
export function endsBuild(
  msg: { readonly type: string; readonly code?: unknown },
  homeDay: string | null,
  conversation: string | null,
  busyBefore: boolean,
): boolean {
  if (homeDay === null) return false;
  const day = conversation === homeDay;
  if (msg.type === 'turn.complete') return day;
  if (msg.type !== 'error') return false;
  const code = typeof msg.code === 'string' ? msg.code : '';
  return BUILD_REFUSALS.has(code) || (day && busyBefore && code !== 'listening' && code !== 'no_listening');
}

const PLACEHOLDERS = 9;
/** How long "Inbox removed" offers Undo. */
export const TOAST_MS = 6000;
/** Left after the pointer or focus leaves a paused toast, at the least. */
const TOAST_RESUME_MS = 2000;
/** How long a tile takes to slide to its new place. */
const SLIDE_MS = 260;
const EASE = 'cubic-bezier(0.2, 0.7, 0.2, 1)';
// Ids for the grip's description and the Add panel, one set per Home.
let helpIds = 0;

/** Whether two answers draw the same tiles, in the same places: then nothing is drawn again. */
function sameTiles(a: HomeData, b: HomeData): boolean {
  return JSON.stringify([a.tiles, a.removed]) === JSON.stringify([b.tiles, b.removed]);
}

function btn(text: string, cls: string, onClick: () => void): HTMLButtonElement {
  const b = h('button', cls, text);
  b.type = 'button';
  b.addEventListener('click', onClick);
  return b;
}

export function createHome(opts: HomeOptions): Home {
  const clock = opts.now ?? ((): Date => new Date());
  const still = opts.reducedMotion ?? ((): boolean => matchMedia('(prefers-reduced-motion: reduce)').matches);

  const el = h('section', 'home');
  el.setAttribute('aria-label', 'Home');

  // Header: the date, the greeting, one line, and the button.
  const head = h('header', 'home-head');
  const date = h('p', 'home-date');
  const hello = h('h3', 'home-hello');
  const lede = h('p', 'home-lede', ledeLine(''));
  const build = btn('Build my briefing', 'home-build', () => {
    if (building) return;
    building = true;
    // A build the core drops says nothing: the button rests again after a while.
    buildTimer = setTimeout(built, opts.buildTimeoutMs ?? BUILD_TIMEOUT_MS);
    drawButton();
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
  // The foot line, and Reset Home there when nothing is removed but the order is the user's own.
  const footLine = h('div', 'home-foot-line');
  const foot = h('p', 'home-foot', 'Want something else on your board? Just ask her.');
  const footReset = btn('Reset Home', 'home-reset home-reset-foot', () => reset());
  footReset.dataset['act'] = 'reset';
  footReset.hidden = true;
  footLine.append(foot, footReset);

  // "Inbox removed" with Undo, or "Couldn't save that change". It sits at the foot of the pane.
  const toast = h('div', 'home-toast');
  toast.hidden = true;
  const toastText = h('span', 'home-toast-text');
  const undo = btn('Undo', 'home-undo', () => undoRemove());
  toast.append(toastText, undo);

  // Without a region from the page, Home speaks through its own, kept in the document: Home itself
  // is taken out while Settings is over it, and a line said then must still be heard.
  let live: HTMLElement | null = null;
  const say =
    opts.announce ??
    ((text: string): void => {
      if (!live?.isConnected) {
        live = h('p', 'visually-hidden home-live');
        live.setAttribute('role', 'status');
        live.setAttribute('aria-live', 'polite');
        document.body.append(live);
      }
      live.textContent = text;
    });

  el.append(head, meterBox, grid, footLine, toast);

  let data: HomeData | null = null;
  // Each tile's drawn sample, so a tile taken off is let go of on its own.
  const previews = new Map<string, Omit<DrawnBlocks, 'el'>>();
  // The tiles on screen, by id, so a move reuses them rather than drawing every sample again.
  let tileEls = new Map<string, HTMLElement>();
  // What the core last said, to go back to when a save fails.
  let confirmed: HomeData | null = null;
  // Arranging: a pointer drag, a keyboard pick-up, and saves still out.
  let dragging = false;
  let held: { readonly id: string; readonly title: string; readonly from: readonly string[]; at: number } | null = null;
  let saving = 0;
  // A read that came while arranging, done once it is over.
  let stale = false;
  let addOpen = false;
  let toastTimer: ReturnType<typeof setTimeout> | null = null;
  let toastLeft = 0;
  let toastSince = 0;
  let toastPaused = false;
  let lastRemoved: { readonly id: string; readonly at: number } | null = null;
  const arranging = (): boolean => opts.saveLayout !== undefined;
  const holding = (): boolean => dragging || held !== null || saving > 0;
  // Counts reads, so an older answer arriving after a newer one is dropped.
  let gen = 0;
  // A show whose read was overtaken by a refresh still has its tiles arrive.
  let arriving = false;
  // Build was pressed and its board has not come: reads keep the button busy.
  let building = false;
  let buildTimer: ReturnType<typeof setTimeout> | null = null;
  // A show still reading, and when it started, so a refresh right behind it is not a second read.
  let showing: { readonly at: number; readonly read: Promise<void> } | null = null;

  function drawButton(): void {
    build.disabled = building;
    if (building) {
      build.setAttribute('aria-busy', 'true');
      build.textContent = data?.briefing === 'open' ? 'Opening your briefing…' : 'Building your briefing…';
    } else {
      build.removeAttribute('aria-busy');
      build.textContent = data?.briefing === 'open' ? 'Open today’s briefing' : 'Build my briefing';
    }
  }

  function built(): void {
    if (buildTimer !== null) clearTimeout(buildTimer);
    buildTimer = null;
    building = false;
    drawButton();
  }

  function drawHead(): void {
    const now = clock();
    date.textContent = dateLine(now);
    hello.textContent = greeting(now.getHours(), data?.user_name ?? '');
    lede.textContent = ledeLine(data?.agent_name ?? '');
    drawButton();
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
        previews.set(t.id, drawn);
      } catch (err) {
        // One sample that cannot be drawn leaves its frame empty, never the rest of Home.
        console.warn(`The ${t.type} sample could not be drawn`, err);
      }
    }
    for (const f of frame.querySelectorAll<HTMLElement>('a, button, input, select, textarea, [tabindex]')) f.tabIndex = -1;
    box.append(frame, h('span', 'home-sample', 'Sample'));
    return box;
  }

  // One description for every grip, so each says how it works without repeating it aloud.
  const gripHelp = h('p', 'visually-hidden', 'Space or Enter picks it up, the arrow keys move it, Space or Enter drops it, Escape puts it back.');
  gripHelp.id = `home-grip-help-${++helpIds}`;
  el.append(gripHelp);

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
    if (arranging()) top.append(...arrangeControls(t, li));
    li.append(top, preview(t), tileFoot(t));
    return li;
  }

  /** The grip (drag it, or pick it up from the keyboard) and the × that takes the tile off. */
  function arrangeControls(t: HomeTile, li: HTMLElement): HTMLElement[] {
    const grip = h('button', 'home-grip');
    grip.type = 'button';
    grip.dataset['act'] = 'grip';
    grip.setAttribute('aria-label', `Move ${t.title}`);
    grip.setAttribute('aria-describedby', gripHelp.id);
    grip.append(icon('grip', 18));
    // Pointer clicks do nothing on their own; keys are handled below, so Space never clicks too.
    grip.addEventListener('click', (e) => e.preventDefault());
    grip.addEventListener('keydown', (e) => gripKey(e, t));
    grip.addEventListener('blur', () => {
      // Tabbing away with a tile held puts it down where it is. A keyboard move blurs it for a
      // moment and focuses it again, so only a blur that lasts counts.
      queueMicrotask(() => {
        if (held?.id === t.id && document.activeElement !== grip) drop();
      });
    });
    attachDrag(grip, li, {
      container: grid,
      items: () => [...grid.querySelectorAll<HTMLElement>(':scope > .home-tile')],
      flip,
      may: () => data !== null && held === null && !dragging,
      lifted: () => {
        dragging = true;
        el.classList.add('home-dragging');
      },
      ended: (from, to, cancelled) => {
        dragging = false;
        el.classList.remove('home-dragging');
        if (!cancelled && from !== to && data) commit(moved(layoutOf(data).order, from, to), layoutOf(data).removed);
        else settle();
      },
    });
    const remove = h('button', 'home-remove');
    remove.type = 'button';
    remove.dataset['act'] = 'remove';
    remove.setAttribute('aria-label', `Remove ${t.title} from Home`);
    remove.append(icon('close', 14));
    remove.addEventListener('click', () => removeTile(t.id));
    return [grip, remove];
  }

  // ── Keyboard grip ──

  function gripKey(e: KeyboardEvent, t: HomeTile): void {
    if (!data) return;
    const ids = layoutOf(data).order;
    if (e.key === ' ' || e.key === 'Enter') {
      e.preventDefault();
      e.stopPropagation();
      if (held?.id === t.id) drop();
      else if (!held && !dragging) {
        const at = ids.indexOf(t.id);
        if (at < 0) return;
        held = { id: t.id, title: t.title, from: ids, at };
        tileEls.get(t.id)?.classList.add('home-held');
        el.classList.add('home-dragging');
        say(pickedLine(t.title, at + 1, ids.length));
      }
      return;
    }
    if (held?.id !== t.id) return;
    if (e.key === 'Escape') {
      // Spent here: the workspace behind stays open.
      e.preventDefault();
      e.stopPropagation();
      const was = held;
      putDown();
      placeTiles(was.from, true);
      say(`${was.title} put back at position ${was.from.indexOf(was.id) + 1} of ${was.from.length}`);
      settle();
      return;
    }
    const step = keyStep(e.key);
    if (!step) return;
    e.preventDefault();
    e.stopPropagation();
    const order = currentOrder();
    const to = Math.max(0, Math.min(order.length - 1, held.at + step));
    if (to !== held.at) {
      held.at = to;
      placeTiles(moved(order, order.indexOf(t.id), to), true);
    }
    say(movedLine(to + 1, order.length));
  }

  /** The tile ids in the order they stand in the grid now. */
  function currentOrder(): string[] {
    return [...grid.querySelectorAll<HTMLElement>(':scope > .home-tile')].map((li) => li.dataset['id'] ?? '');
  }

  function putDown(): void {
    if (held) tileEls.get(held.id)?.classList.remove('home-held');
    el.classList.remove('home-dragging');
    held = null;
  }

  function drop(): void {
    if (!held || !data) return;
    const order = currentOrder();
    const changed = order.join() !== held.from.join();
    putDown();
    say('dropped');
    if (changed) commit(order, layoutOf(data).removed);
    else settle();
  }

  // ── Moving tiles on screen ──

  /** Runs a change to the grid and slides each tile from where it was to where it is (FLIP). */
  function flip(mutate: () => void): void {
    if (still()) {
      mutate();
      return;
    }
    const kids = [...grid.children] as HTMLElement[];
    const was = new Map(kids.map((k) => [k, k.getBoundingClientRect()]));
    mutate();
    for (const k of grid.children as HTMLCollectionOf<HTMLElement>) {
      if (typeof k.animate !== 'function') continue;
      const b = was.get(k);
      if (!b) {
        // New here (added back): it fades up into its place.
        k.animate([{ opacity: 0, transform: 'scale(0.96)' }, { opacity: 1, transform: 'none' }], { duration: SLIDE_MS, easing: EASE });
        continue;
      }
      const a = k.getBoundingClientRect();
      const dx = b.left - a.left;
      const dy = b.top - a.top;
      if (!dx && !dy) continue;
      // Its entrance animation would fight the slide.
      k.classList.remove('home-in');
      for (const old of k.getAnimations?.() ?? []) old.cancel();
      k.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }], { duration: SLIDE_MS, easing: EASE });
    }
  }

  /**
   * Puts the tiles on screen in this order, reusing what is drawn, then the Add widgets tile.
   * Focus stays where it was even though moving an element in the page takes it away.
   */
  function placeTiles(order: readonly string[], animate: boolean): void {
    const go = (): void => {
      const pool = new Map([...(data?.tiles ?? []), ...(data?.removed ?? [])].map((t) => [t.id, t]));
      const want: HTMLElement[] = [];
      for (const id of order) {
        let li = tileEls.get(id);
        const t = pool.get(id);
        if (!li && t) {
          li = tile(t, 0, false);
          tileEls.set(id, li);
        }
        if (li) want.push(li);
      }
      // Tiles taken off are let go of, samples and all.
      for (const [id, li] of [...tileEls]) {
        if (want.includes(li)) continue;
        li.remove();
        previews.get(id)?.dispose();
        previews.delete(id);
        tileEls.delete(id);
      }
      if (arranging() && data && data.removed.length > 0) want.push(addTile);
      else addTile.remove();
      const active = document.activeElement;
      want.forEach((w, i) => {
        if (grid.children[i] !== w) grid.insertBefore(w, grid.children[i] ?? null);
      });
      if (active instanceof HTMLElement && active !== document.activeElement && active.isConnected) active.focus({ preventScroll: true });
    };
    if (animate) flip(go);
    else go();
  }

  // ── Add widgets, Reset Home ──

  const addTile = h('li', 'home-add');
  const addButton = h('button', 'home-add-open');
  addButton.type = 'button';
  addButton.dataset['act'] = 'add-open';
  addButton.setAttribute('aria-expanded', 'false');
  const addPanel = h('div', 'home-add-panel');
  addPanel.id = `home-add-panel-${helpIds}`;
  addPanel.hidden = true;
  addPanel.setAttribute('role', 'group');
  addPanel.setAttribute('aria-label', 'Add widgets');
  addButton.setAttribute('aria-controls', addPanel.id);
  addButton.addEventListener('click', () => setAddOpen(true));
  addPanel.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    // Spent here: the workspace behind stays open.
    e.preventDefault();
    e.stopPropagation();
    setAddOpen(false);
    addButton.focus();
  });
  addTile.append(addButton, addPanel);

  function setAddOpen(on: boolean): void {
    addOpen = on && (data?.removed.length ?? 0) > 0;
    drawAdd();
    if (addOpen) addPanel.querySelector<HTMLElement>('.home-add-one')?.focus();
  }

  function drawAdd(): void {
    const gone = data?.removed ?? [];
    if (!gone.length) addOpen = false;
    addTile.classList.toggle('home-add-on', addOpen);
    addButton.hidden = addOpen;
    addButton.setAttribute('aria-expanded', String(addOpen));
    const plus = h('span', 'home-add-plus');
    plus.append(icon('plus', 20));
    addButton.replaceChildren(plus, h('span', 'home-add-words', `Add widgets (${gone.length})`));
    addPanel.hidden = !addOpen;
    if (!addOpen) {
      addPanel.replaceChildren();
      return;
    }
    const close = h('button', 'home-add-close');
    close.type = 'button';
    close.setAttribute('aria-label', 'Close Add widgets');
    close.append(icon('close', 14));
    close.addEventListener('click', () => {
      setAddOpen(false);
      addButton.focus();
    });
    const top = h('div', 'home-add-top');
    top.append(h('h4', 'home-add-title', 'Add widgets'), close);
    const list = h('ul', 'home-add-list');
    for (const t of gone) {
      const row = h('li', 'home-add-row');
      row.dataset['id'] = t.id;
      const words = h('div', 'home-add-row-words');
      words.append(h('span', 'home-add-row-title', t.title), h('span', 'home-add-row-what', t.what));
      const badge = h('span', 'home-badge', BADGE[t.state]);
      badge.dataset['tone'] = t.state;
      const add = btn('Add', 'home-add-one', () => addBack(t.id));
      add.setAttribute('aria-label', `Add ${t.title} to Home`);
      row.append(words, badge, add);
      list.append(row);
    }
    const resetButton = btn('Reset Home', 'home-reset', () => reset());
    resetButton.dataset['act'] = 'reset';
    addPanel.replaceChildren(top, list, resetButton);
  }

  /** The Add widgets tile while anything is removed; else Reset Home in the foot line once the order is the user's own. */
  function drawArrangeBits(): void {
    drawAdd();
    const on = arranging() && data !== null;
    if (!on || !data?.removed.length) addTile.remove();
    footReset.hidden = !on || !data || data.removed.length > 0 || isDefaultLayout(data);
  }

  function focusGrip(id: string | undefined): void {
    const target = id ? tileEls.get(id)?.querySelector<HTMLElement>('[data-act="grip"]') : null;
    (target ?? (addButton.isConnected ? addButton : null))?.focus();
  }

  function removeTile(id: string): void {
    if (!data || dragging || held) return;
    const l = layoutOf(data);
    const at = l.order.indexOf(id);
    if (at < 0) return;
    const t = data.tiles[at]!;
    const order = l.order.filter((x) => x !== id);
    commit(order, [...l.removed, id]);
    showToast(`${t.title} removed`, true);
    lastRemoved = { id, at };
    say(`${t.title} removed`);
    // Focus goes to the tile that took its place, else the one before, else Add widgets.
    focusGrip(order[at] ?? order[at - 1]);
  }

  function undoRemove(): void {
    const r = lastRemoved;
    hideToast();
    if (!r || !data || !data.removed.some((t) => t.id === r.id)) return;
    const l = layoutOf(data);
    const order = [...l.order];
    order.splice(Math.min(r.at, order.length), 0, r.id);
    commit(order, l.removed.filter((x) => x !== r.id));
    const t = data.tiles.find((x) => x.id === r.id);
    if (t) say(`${t.title} is back`);
    focusGrip(r.id);
  }

  function addBack(id: string): void {
    if (!data) return;
    const l = layoutOf(data);
    const t = data.removed.find((x) => x.id === id);
    if (!t) return;
    const rows = data.removed.map((x) => x.id);
    const nextRow = rows[rows.indexOf(id) + 1] ?? rows[rows.indexOf(id) - 1];
    commit(addedBack(l.order, id), l.removed.filter((x) => x !== id));
    say(`${t.title} added`);
    // More to add: the panel stays, focus on the next row. Nothing left: on the tile now back.
    const nextAdd = nextRow ? addPanel.querySelector<HTMLElement>(`.home-add-row[data-id="${nextRow}"] .home-add-one`) : null;
    if (nextAdd) nextAdd.focus();
    else focusGrip(id);
  }

  function reset(): void {
    if (!data || dragging || held) return;
    const l = defaultLayout(data);
    addOpen = false;
    commit(l.order, l.removed);
    say('Home is back to its default layout');
    focusGrip(l.order[0]);
  }

  // ── The toast ──

  function armToast(ms: number): void {
    if (toastTimer !== null) clearTimeout(toastTimer);
    toastTimer = null;
    toastLeft = ms;
    toastSince = Date.now();
    // While the pointer or focus is on it, it waits, so Undo is not taken away mid-reach.
    if (!toastPaused) toastTimer = setTimeout(hideToast, ms);
  }

  function showToast(text: string, canUndo: boolean): void {
    toastText.textContent = text;
    undo.hidden = !canUndo;
    lastRemoved = null;
    toast.hidden = false;
    armToast(opts.toastMs ?? TOAST_MS);
  }

  function hideToast(): void {
    if (toastTimer !== null) clearTimeout(toastTimer);
    toastTimer = null;
    toastPaused = false;
    toast.hidden = true;
    lastRemoved = null;
  }

  toast.addEventListener('pointerenter', () => pauseToast());
  toast.addEventListener('pointerleave', () => {
    if (!toast.contains(document.activeElement)) resumeToast();
  });
  toast.addEventListener('focusin', () => pauseToast());
  toast.addEventListener('focusout', (e) => {
    if (!toast.contains(e.relatedTarget as Node | null)) resumeToast();
  });

  function pauseToast(): void {
    if (toastPaused || toast.hidden) return;
    toastPaused = true;
    toastLeft -= Date.now() - toastSince;
    if (toastTimer !== null) clearTimeout(toastTimer);
    toastTimer = null;
  }

  function resumeToast(): void {
    if (!toastPaused) return;
    toastPaused = false;
    if (!toast.hidden) armToast(Math.max(toastLeft, TOAST_RESUME_MS));
  }

  // ── Saving ──

  /** Draws the new layout at once and saves it; a failed save puts the last saved one back. */
  function commit(order: readonly string[], removed: readonly string[]): void {
    const save = opts.saveLayout;
    if (!data || !save) return;
    data = withLayout(data, order, removed);
    drawMeter();
    placeTiles(layoutOf(data).order, true);
    drawArrangeBits();
    // Newer than any read still out: that answer would put the old layout back.
    const mine = ++gen;
    saving++;
    void save(layoutOf(data))
      .then((raw) => {
        const got = homeOf(raw);
        if (!got) throw new Error('No Home in the answer');
        confirmed = got;
        if (mine === gen) take(got, false);
      })
      .catch(() => {
        // Only the latest change decides: it carries the whole layout.
        if (mine !== gen || !confirmed) return;
        data = confirmed;
        drawMeter();
        placeTiles(layoutOf(data).order, true);
        drawArrangeBits();
        showToast('Couldn’t save that change', false);
        say('Couldn’t save that change');
      })
      .finally(() => {
        saving--;
        settle();
      });
  }

  /** Arranging is over: a read that waited for it happens now. */
  function settle(): void {
    if (holding() || !stale) return;
    stale = false;
    void read(false);
  }

  function disposePreviews(): void {
    for (const p of previews.values()) p.dispose();
    previews.clear();
    tileEls = new Map();
  }

  function drawGrid(animate: boolean): void {
    // Focus inside the grid comes back to the same control on the same tile.
    const a = document.activeElement;
    const was = a instanceof HTMLElement && grid.contains(a) ? { id: a.closest<HTMLElement>('.home-tile')?.dataset['id'], act: a.dataset['act'] } : null;
    disposePreviews();
    grid.removeAttribute('aria-busy');
    if (!data) return;
    const tiles = data.tiles.map((t, i) => tile(t, i, animate));
    tileEls = new Map(data.tiles.map((t, i) => [t.id, tiles[i]!]));
    drawArrangeBits();
    grid.replaceChildren(...tiles, ...(arranging() && data.removed.length ? [addTile] : []));
    if (was?.act) {
      const back = was.act === 'add-open' ? addButton : was.id ? tileEls.get(was.id)?.querySelector<HTMLElement>(`[data-act="${was.act}"]`) : null;
      back?.focus({ preventScroll: true });
    }
  }

  function drawLoading(): void {
    disposePreviews();
    drawArrangeBits();
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
    drawArrangeBits();
    grid.removeAttribute('aria-busy');
    const li = h('li', 'home-failed');
    li.append(h('p', 'home-failed-text', 'Couldn’t check what’s connected.'), btn('Try again', 'home-retry', () => void read(true, true)));
    grid.replaceChildren(li);
  }

  /** Read the state and draw it. `fresh`: tiles arrive afresh (a show, not a refresh). */
  async function read(fresh: boolean, retry = false): Promise<void> {
    arriving ||= fresh;
    // A tile in hand, or a change still saving: read once that is over.
    if (holding()) {
      stale = true;
      return;
    }
    const mine = ++gen;
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
    if (holding()) {
      stale = true;
      return;
    }
    if (!got) {
      arriving = false;
      // A refresh that failed keeps what was there; with nothing to keep, say so.
      if (!data || retry) {
        data = null;
        drawHead();
        drawMeter();
        drawFailed();
      }
      return;
    }
    confirmed = got;
    take(got, arriving || !data);
    arriving = false;
  }

  /** Draws what the core said. Tiles are drawn again only if they arrive or something on them changed. */
  function take(got: HomeData, arrive: boolean): void {
    // Said on a show too: back from Settings is when a sign-in turns a tile ready. A move alone says nothing.
    const line = changeLine(data, got);
    // The briefing moved on (built, or a new day): whatever Build was waiting for is over.
    if (building && data && data.briefing !== got.briefing) built();
    const same = !arrive && data !== null && tileEls.size > 0 && sameTiles(data, got);
    data = got;
    drawHead();
    drawMeter();
    if (same) drawArrangeBits();
    else drawGrid(arrive && !still());
    if (line) say(line);
  }

  // Before the first read, the header and quiet placeholders, so nothing jumps.
  drawHead();
  drawMeter();
  drawLoading();

  return {
    el,
    show() {
      const r = read(true);
      const mine = { at: Date.now(), read: r };
      showing = mine;
      void r.finally(() => {
        if (showing === mine) showing = null;
      });
      return r;
    },
    refresh() {
      if (!el.isConnected) return Promise.resolve();
      // At startup the connections load just behind the first show: that show's read will do.
      if (showing && Date.now() - showing.at < SHOW_SETTLE_MS) return showing.read;
      return read(false);
    },
    changed() {
      if (!el.isConnected) return Promise.resolve();
      return read(false);
    },
    built,
  };
}
