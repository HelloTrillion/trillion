/**
 * Settings → Connected tools (CON-5): connect her to the services you use.
 *
 * Three ways in, all on one screen: a gallery of featured services (each its own
 * official MCP server), sign in or paste a key, done; "Add your own" for any other
 * server, by its address, its command, or the settings from its instructions; and
 * the servers already connected, each with what it may do on its own.
 *
 * Every tool asks first until you say otherwise. A key goes to the keychain, never
 * to a file and never back to this page. Nothing here can be done by her: only
 * this page, with its session token, can connect a server or loosen what it may do.
 */

import { inApp, signInTab } from './desktop';
import { icon } from './icons';
import type { Host } from './panel';
import type { Api } from './settings';

/** Where a sign-in page opened: a tab here, the user's browser in the desktop app (D7). */
const opened = (): string => (inApp() ? 'in your browser' : 'in the tab that opened');

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(text: string, cls = '', onClick?: () => void): HTMLButtonElement {
  const b = el('button', cls, text);
  b.type = 'button';
  if (onClick) b.addEventListener('click', onClick);
  return b;
}

function link(text: string, href: string): HTMLAnchorElement {
  const a = el('a', 'connected-link', text);
  a.href = href;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';
  return a;
}

// ── What the server sends ────────────────────────────────────────────────

export interface ToolRow {
  readonly name: string;
  readonly description: string;
  readonly asks_first: boolean;
  readonly says_read_only: boolean;
}

export type Status = 'starting' | 'signing_in' | 'connected' | 'failed' | 'disabled' | 'stopped';

export interface ServerRow {
  readonly name: string;
  readonly title: string;
  readonly catalog: string;
  readonly transport: 'stdio' | 'http';
  readonly runs: string;
  readonly status: Status;
  readonly problem: string;
  readonly sign_in_url: string;
  readonly signs_in: boolean;
  readonly signed_in: boolean;
  readonly missing: Missing | null;
  readonly keys: readonly { readonly name: string; readonly saved: boolean }[];
  readonly keys_in_file: readonly string[];
  readonly disabled: boolean;
  readonly tools: readonly ToolRow[];
  readonly skipped: Readonly<Record<string, string>>;
}

export interface Missing {
  readonly program: string;
  readonly name: string;
  readonly link: string;
}

export interface FieldSpec {
  readonly id: string;
  readonly label: string;
  readonly secret: boolean;
  readonly placeholder: string;
  readonly link: string;
  readonly link_label: string;
}

export interface CatalogEntry {
  readonly id: string;
  readonly title: string;
  readonly blurb: string;
  readonly category: string;
  readonly color: string;
  readonly glyph: string;
  readonly how: 'sign_in' | 'key' | 'google' | 'slack' | 'telegram';
  readonly connects: string;
  readonly local: boolean;
  readonly fields: readonly FieldSpec[];
  readonly homepage: string;
  readonly note: string;
  readonly service?: string;
}

export interface GoogleTool {
  readonly name: string;
  readonly title: string;
  readonly description: string;
  readonly asks_first: boolean;
  readonly freeable: boolean;
}

export interface Person {
  readonly id: string;
  readonly name: string;
  readonly times?: number;
}

export interface ChatState {
  readonly configured: boolean;
  readonly on: boolean;
  readonly status: 'off' | 'connecting' | 'connected' | 'failed';
  readonly problem: string;
  readonly handle: string;
  readonly allowed: readonly Person[];
  readonly knocks: readonly Person[];
}

export interface SlackState {
  readonly slack: {
    readonly connected: boolean;
    readonly workspace: string;
    readonly user: string;
    readonly create_app_url: string;
    readonly tools: readonly GoogleTool[];
  };
  readonly channels: { readonly slack: ChatState; readonly telegram: ChatState };
}

/** A chat connection's state, in a few words. */
export function chatText(c: Pick<ChatState, 'configured' | 'on' | 'status' | 'problem' | 'handle'>): string {
  if (!c.configured) return 'Not set up';
  if (!c.on) return 'Turned off';
  if (c.status === 'connected') return c.handle ? `Listening as @${c.handle}` : 'Listening';
  if (c.status === 'failed') return c.problem ? `Not connected: ${c.problem}` : 'Not connected';
  return 'Connecting…';
}

export interface GoogleState {
  readonly ready: boolean;
  readonly picker: boolean;
  readonly files: Readonly<Record<string, readonly { readonly id: string; readonly name: string }[]>>;
  readonly source: string;
  readonly can_forget_client: boolean;
  readonly apis: Readonly<Record<string, { readonly name: string; readonly id: string }>>;
  readonly accounts: readonly { readonly email: string; readonly services: readonly string[]; readonly signed_in: boolean }[];
  readonly services: Readonly<Record<string, { readonly title: string; readonly accounts: readonly string[]; readonly tools: readonly GoogleTool[] }>>;
  readonly attempts: Readonly<Record<string, { readonly status: 'signing_in' | 'connected' | 'failed'; readonly url: string; readonly email: string; readonly problem: string }>>;
}

export interface Connected {
  readonly config_path: string;
  readonly servers: readonly ServerRow[];
  readonly problems: Readonly<Record<string, string>>;
  readonly catalog?: readonly CatalogEntry[];
  readonly programs?: Readonly<Record<string, boolean>>;
}

interface Proposal {
  readonly name: string;
  readonly title: string;
  readonly entry: Record<string, unknown>;
  readonly connects: string;
  readonly local: boolean;
  readonly signs_in: boolean;
  readonly missing: Missing | null;
  readonly secrets: readonly { readonly ref: string; readonly label: string; readonly value: string }[];
}

// ── Words and small rules, tested on their own ───────────────────────────

/** A server's state, in a few words. */
export function statusText(s: Pick<ServerRow, 'status' | 'problem'>): string {
  switch (s.status) {
    case 'connected':
      return 'Connected';
    case 'starting':
      return 'Connecting…';
    case 'signing_in':
      return 'Waiting for you to sign in';
    case 'disabled':
      return 'Turned off';
    case 'stopped':
      return 'Stopped';
    default:
      return s.problem ? `Not connected: ${s.problem}` : 'Not connected';
  }
}

/** The line under the section's name in the sidebar. */
export function connectedSummary(c: Connected | null): string {
  if (!c || !c.servers.length) return 'None';
  const live = c.servers.filter((s) => s.status === 'connected').length;
  return live === c.servers.length ? `${live} connected` : `${live} of ${c.servers.length}`;
}

const fold = (s: string): string => s.toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '');

/** The featured entries a search and a category choose. */
export function filterCatalog(entries: readonly CatalogEntry[], query: string, category: string): CatalogEntry[] {
  const words = fold(query).split(/\s+/).filter(Boolean);
  return entries.filter((e) => {
    if (category && e.category !== category) return false;
    const hay = fold(`${e.title} ${e.blurb} ${e.category}`);
    return words.every((w) => hay.includes(w));
  });
}

/** The tools the server itself says only read, which still ask first. */
export function readingTools(tools: readonly ToolRow[]): ToolRow[] {
  return tools.filter((t) => t.says_read_only && t.asks_first);
}

const INK_DARK = 'var(--color-ink-on-light)';
const INK_LIGHT = 'var(--color-ink-on-dark)';

/** Dark letters on a light colour, light letters on a dark one. */
export function inkFor(hex: string): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return INK_LIGHT;
  const n = parseInt(m[1] ?? '', 16);
  const [r = 0, g = 0, b = 0] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.45 ? INK_DARK : INK_LIGHT;
}

const BADGE: Record<CatalogEntry['how'], string> = {
  sign_in: 'Sign in',
  key: 'Needs a key',
  google: 'Sign in with Google',
  slack: 'Set up',
  telegram: 'Set up',
};

// ── The screen ───────────────────────────────────────────────────────────

/** Where a featured service's icon is: bundled with the app, so opening Settings
 * never tells 26 companies you did. */
export const iconPath = (id: string): string => `apps/${encodeURIComponent(id)}.png`;

/** A connected catalogue service, as the board needs it to show a card's source tile. */
export interface SourceTile {
  readonly id: string;
  readonly title: string;
  readonly color: string;
  readonly glyph: string;
}

/**
 * The catalogue services she can read: a server set up and turned on, or a Google service with an
 * account signed in. Chat channels (Slack, Telegram) are not sources.
 */
export function sourceTiles(c: Connected | null, g: Pick<GoogleState, 'services'> | null = null): SourceTile[] {
  if (!c?.catalog) return [];
  // Set up and on, not only connected this second, so a slow start does not hide a tile.
  const on = new Set(c.servers.filter((s) => s.catalog && !s.disabled).map((s) => s.catalog));
  const google = (e: CatalogEntry): boolean => e.how === 'google' && !!e.service && (g?.services[e.service]?.accounts.length ?? 0) > 0;
  return c.catalog.filter((e) => on.has(e.id) || google(e)).map(({ id, title, color, glyph }) => ({ id, title, color, glyph }));
}

/** A service's tile: its own icon, or its colour and a letter or two until (or if
 * not) the icon loads. `xs` is the size beside a card's source on the board. */
export function mark(color: string, glyph: string, size: 'xs' | 'sm' | 'lg' = 'sm', id = ''): HTMLElement {
  const m = el('span', `connected-mark connected-mark-${size}`, glyph);
  m.style.background = color;
  m.style.color = inkFor(color);
  m.setAttribute('aria-hidden', 'true');
  if (id) {
    const img = el('img', 'connected-icon');
    img.alt = '';
    img.decoding = 'async';
    img.draggable = false;
    img.addEventListener('load', () => m.classList.add('has-icon'));
    img.addEventListener('error', () => img.remove());
    img.src = iconPath(id);
    m.append(img);
  }
  return m;
}

function plainMark(size: 'sm' | 'lg' = 'sm'): HTMLElement {
  const m = el('span', `connected-mark connected-mark-${size} connected-mark-plain`);
  m.append(icon('connected', size === 'lg' ? 26 : 20));
  m.setAttribute('aria-hidden', 'true');
  return m;
}

export function createConnected(
  host: Host,
  api: Api,
  changed: () => void,
): {
  open(): Promise<void>;
  reveal(key: string): void;
  start(catalogId: string): Promise<void>;
  browse(category: string): void;
  summary(): string;
  load(): Promise<void>;
  tiles(): SourceTile[];
} {
  let last: Connected | null = null;
  let g: GoogleState | null = null;
  let sl: SlackState | null = null;
  let view: 'home' | 'setup' | 'own' | 'google' | 'chat' = 'home';
  let chatRedraw: (() => void) | null = null;
  let googleFollow: (() => void) | null = null;
  let query = '';
  let category = '';
  let expanded = '';
  let poll = 0;
  // A view that draws a server as it connects says which one.
  let following = '';
  let followDraw: ((s: ServerRow) => void) | null = null;

  const form = el('form', 'settings connected');
  form.addEventListener('submit', (ev) => ev.preventDefault());
  host.setAttribute('aria-labelledby', 'connected-title');
  host.replaceChildren(form);

  const byCatalog = (id: string): CatalogEntry | undefined => last?.catalog?.find((e) => e.id === id);
  const serverMark = (s: ServerRow, size: 'sm' | 'lg' = 'sm'): HTMLElement => {
    const entry = s.catalog ? byCatalog(s.catalog) : undefined;
    return entry ? mark(entry.color, entry.glyph, size, entry.id) : plainMark(size);
  };

  // A read under way, so a caller that needs the data waits for it rather than asking twice.
  let loading: Promise<Connected | null> | null = null;

  function load(): Promise<Connected | null> {
    loading ??= read().finally(() => {
      loading = null;
    });
    return loading;
  }

  async function read(): Promise<Connected | null> {
    try {
      const [r, rg, rs] = await Promise.all([
        api('GET', '/api/mcp'),
        api('GET', '/api/google').catch(() => null),
        api('GET', '/api/slack').catch(() => null),
      ]);
      if (!r.ok) throw new Error(String(r.status));
      last = (await r.json()) as Connected;
      g = rg && rg.ok ? ((await rg.json()) as GoogleState) : null;
      sl = rs && rs.ok ? ((await rs.json()) as SlackState) : null;
      changed();
      return last;
    } catch {
      return null;
    }
  }

  /** Keep asking while something is connecting or waiting for a sign-in. */
  function watch(): void {
    window.clearTimeout(poll);
    const busy =
      view === 'chat' ||
      last?.servers.some((s) => s.status === 'starting' || s.status === 'signing_in') ||
      Object.values(g?.attempts ?? {}).some((a) => a.status === 'signing_in') ||
      Object.values(sl?.channels ?? {}).some((c) => c.on && c.status === 'connecting');
    if (!busy || !host.open) return;
    poll = window.setTimeout(async () => {
      if (!host.open) return;
      await load();
      const s = last?.servers.find((x) => x.name === following);
      if (view === 'google' && googleFollow) googleFollow();
      else if (view === 'chat' && chatRedraw) chatRedraw();
      else if (view !== 'home' && s && followDraw) followDraw(s);
      else if (view === 'home') drawHome();
      watch();
    }, 1200);
  }

  async function send(method: 'POST' | 'PUT' | 'PATCH' | 'DELETE', path: string, body: unknown = {}): Promise<{ ok: boolean; data: Record<string, unknown> }> {
    try {
      const r = await api(method, path, body);
      const data = (await r.json().catch(() => ({}))) as Record<string, unknown>;
      return { ok: r.ok, data };
    } catch {
      return { ok: false, data: { detail: 'Trillion could not be reached.' } };
    }
  }

  function header(title: string, note: string, back?: () => void): HTMLElement[] {
    const out: HTMLElement[] = [];
    if (back) {
      const b = button('All connections', 'quiet connected-back', back);
      b.prepend(el('span', '', '‹ '));
      out.push(b);
    }
    const h = el('h2', '', title);
    h.id = 'connected-title';
    out.push(h);
    if (note) out.push(el('p', 'settings-note', note));
    return out;
  }

  // ── Home: what is connected, and the gallery ──────────────────────────

  function drawHome(): void {
    view = 'home';
    followDraw = null;
    const c = last;
    const parts: HTMLElement[] = header(
      'Connected tools',
      'Connect her to the apps you use. She asks you before she does anything with them, unless you say she may, and she treats what they send back as information, never as instructions.',
    );
    if (!c) {
      parts.push(el('p', 'settings-hint', 'The connected tools could not be loaded.'));
      form.replaceChildren(...parts);
      return;
    }
    const googleOn = Object.entries(g?.services ?? {}).filter(([, v]) => v.accounts.length);
    const chatsOn = (['slack', 'telegram'] as const).filter((p) => chatConnected(p));
    if (c.servers.length || googleOn.length || chatsOn.length || Object.keys(c.problems).length) {
      parts.push(el('h3', 'connected-heading', 'Your connections'));
      const list = el('div', 'connected-list');
      for (const [service] of googleOn) list.append(googleCard(service));
      for (const p of chatsOn) list.append(chatCard(p));
      for (const s of c.servers) list.append(card(s));
      for (const [name, why] of Object.entries(c.problems)) {
        list.append(el('p', 'settings-hint connected-warn', `${name} in mcp.json could not be used: ${why}`));
      }
      parts.push(list);
    }
    parts.push(el('h3', 'connected-heading', c.servers.length || googleOn.length || chatsOn.length ? 'Add another' : 'Add a connection'));
    parts.push(gallery(c));
    parts.push(
      el(
        'p',
        'settings-hint connected-foot',
        `Connections are kept in ${c.config_path}, in the format Claude Desktop and Cursor use, and keys in your system keychain.`,
      ),
    );
    form.replaceChildren(...parts);
  }

  function gallery(c: Connected): HTMLElement {
    const wrap = el('div', 'connected-gallery');
    const tools = el('div', 'connected-filter');
    const searchWrap = el('label', 'connected-search');
    const search = el('input');
    search.type = 'search';
    search.placeholder = 'Search apps';
    search.setAttribute('aria-label', 'Search apps to connect');
    search.value = query;
    search.autocomplete = 'off';
    search.spellcheck = false;
    searchWrap.append(icon('search', 16), search);
    const chips = el('div', 'connected-chips');
    chips.setAttribute('role', 'group');
    chips.setAttribute('aria-label', 'Kinds of app');
    const categories = [...new Set((c.catalog ?? []).map((e) => e.category))];
    for (const cat of ['', ...categories]) {
      const chip = button(cat || 'All', 'connected-chip', () => {
        category = cat;
        drawHome();
      });
      chip.setAttribute('aria-pressed', String(category === cat));
      chips.append(chip);
    }
    tools.append(searchWrap, chips);
    const grid = el('div', 'connected-grid');
    const fill = (): void => {
      const shown = filterCatalog(c.catalog ?? [], query, category);
      const tiles = shown.map((e) => tile(e, c));
      if (!query || fold('add your own server mcp custom').includes(fold(query))) tiles.push(ownTile());
      grid.replaceChildren(...(tiles.length ? tiles : [el('p', 'settings-hint', 'Nothing matches. Try Add your own.')]));
    };
    search.addEventListener('input', () => {
      query = search.value;
      fill();
    });
    fill();
    wrap.append(tools, grid);
    return wrap;
  }

  /** The card an entry already has under Your connections (its key), or null when it is not set up. */
  function setUpAs(e: CatalogEntry, c: Connected): string | null {
    if (e.how === 'google') {
      const on = !!e.service && (g?.services[e.service]?.accounts.length ?? 0) > 0;
      return on ? `google:${e.service}` : null;
    }
    if (e.how === 'slack' || e.how === 'telegram') return chatConnected(e.how) ? `chat:${e.how}` : null;
    return c.servers.find((s) => s.catalog === e.id)?.name ?? null;
  }

  /** What tapping an entry's tile does, and what Home's Connect does: its card if set up, else its own setup. */
  function act(e: CatalogEntry, c: Connected): void {
    const key = setUpAs(e, c);
    if (key) {
      expanded = key;
      drawHome();
      form.querySelector(`[data-server="${CSS.escape(key)}"]`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    } else if (e.how === 'google') drawGoogle(e);
    else if (e.how === 'slack' || e.how === 'telegram') drawChat(e, e.how);
    else drawSetup(e);
  }

  function tile(e: CatalogEntry, c: Connected): HTMLElement {
    const connected = setUpAs(e, c);
    const t = button('', 'connected-tile', () => act(e, c));
    const words = el('span', 'connected-tile-words');
    words.append(el('strong', '', e.title), el('small', '', e.blurb));
    // Google signs in with one click only once this copy has a Google app to sign in as.
    const label = e.how === 'google' && !g?.ready ? 'One-time setup' : BADGE[e.how];
    const badge = connected
      ? el('span', 'connected-badge connected-badge-on', 'Connected')
      : el('span', `connected-badge connected-badge-${e.how === 'google' && !g?.ready ? 'key' : e.how}`, label);
    t.append(mark(e.color, e.glyph, 'sm', e.id), words, badge);
    t.setAttribute('aria-label', `${e.title}: ${e.blurb}. ${connected ? 'Connected' : label}`);
    return t;
  }

  function ownTile(): HTMLElement {
    const t = button('', 'connected-tile connected-own', () => drawOwn());
    const words = el('span', 'connected-tile-words');
    words.append(el('strong', '', 'Add your own'), el('small', '', 'Any MCP server, by its address or settings'));
    const plus = el('span', 'connected-mark connected-mark-sm connected-mark-plain', '+');
    plus.setAttribute('aria-hidden', 'true');
    t.append(plus, words);
    return t;
  }

  // ── A connected server ────────────────────────────────────────────────

  function dot(s: ServerRow): HTMLElement {
    const kind = s.status === 'connected' ? 'on' : s.status === 'failed' ? 'bad' : s.status === 'disabled' ? 'off' : 'wait';
    return el('span', `connected-dot connected-dot-${kind}`);
  }

  function card(s: ServerRow): HTMLElement {
    const open = expanded === s.name;
    const box = el('div', `connected-card${open ? ' is-open' : ''}`);
    box.dataset.server = s.name;
    const head = el('div', 'connected-card-head');
    const words = el('span', 'connected-card-words');
    const state = el('small', 'connected-state');
    const said = el('span', 'connected-state-main');
    said.append(dot(s), document.createTextNode(statusText(s)));
    state.append(said);
    const free = s.tools.filter((t) => !t.asks_first).length;
    const counts = s.tools.length ? ` · ${s.tools.length} tool${s.tools.length === 1 ? '' : 's'}, ${free ? `${free} run${free === 1 ? 's' : ''} without asking` : s.tools.length === 1 ? 'asks first' : 'all ask first'}` : '';
    if (s.status === 'connected') state.append(document.createTextNode(counts));
    words.append(el('strong', '', s.title), state);
    const side = el('span', 'connected-card-side');
    if (s.status === 'signing_in' && s.sign_in_url) {
      side.append(button('Sign in', 'approve', () => openSignIn(s.sign_in_url)));
    }
    const toggle = button(open ? 'Close' : 'Manage', 'quiet', () => {
      expanded = open ? '' : s.name;
      drawHome();
    });
    toggle.setAttribute('aria-expanded', String(open));
    side.append(toggle);
    head.append(serverMark(s), words, side);
    box.append(head);
    if (open) box.append(manage(s));
    return box;
  }

  function manage(s: ServerRow): HTMLElement {
    const body = el('div', 'connected-manage');
    body.append(el('p', 'settings-path', s.runs));
    if (s.missing) body.append(missingNote(s.missing));
    const absent = s.keys.filter((k) => !k.saved);
    if (absent.length) body.append(keyForm(s, absent.map((k) => k.name)));
    if (s.keys_in_file.length) {
      body.append(el('p', 'settings-hint connected-warn', `A key is written into mcp.json (${s.keys_in_file.join(', ')}). Remove it there and add the server again here, so it is kept in your keychain.`));
    }
    if (s.tools.length) body.append(toolList(s));
    const skipped = Object.entries(s.skipped);
    if (skipped.length) body.append(el('p', 'settings-hint', `Not offered to her: ${skipped.map(([n, why]) => `${n} (${why})`).join('; ')}`));
    const status = el('p', 'settings-status');
    status.setAttribute('role', 'status');
    const actions = el('div', 'connected-actions');
    const act = (label: string, run: () => Promise<void>, cls = 'quiet'): HTMLButtonElement => {
      const b = button(label, cls, async () => {
        b.disabled = true;
        status.textContent = '';
        await run();
        b.disabled = false;
      });
      return b;
    };
    const after = async (r: { ok: boolean; data: Record<string, unknown> }): Promise<void> => {
      if (!r.ok) status.textContent = String(r.data.detail || 'That did not work.');
      await load();
      drawHome();
      watch();
    };
    if (!s.disabled) actions.append(act('Reconnect', async () => after(await send('POST', `/api/mcp/servers/${s.name}/reconnect`))));
    if (s.signs_in && s.signed_in) actions.append(act('Sign in again', async () => after(await send('POST', `/api/mcp/servers/${s.name}/sign-out`))));
    actions.append(act(s.disabled ? 'Turn on' : 'Turn off', async () => after(await send('PATCH', `/api/mcp/servers/${s.name}`, { disabled: !s.disabled }))));
    actions.append(
      act('View log', async () => {
        const r = await api('GET', `/api/mcp/servers/${s.name}/log`).catch(() => null);
        const pre = el('pre', 'connected-log', r && r.ok ? await r.text() : 'The log could not be read.');
        body.querySelector('.connected-log')?.remove();
        body.insertBefore(pre, actions);
      }),
    );
    const remove = button('Remove', 'quiet connected-danger', () => {
      const confirm = el('div', 'connected-confirm');
      confirm.append(
        el('span', '', `Remove ${s.title}? Its keys and sign-in are deleted from your keychain too.`),
        button('Keep it', 'quiet', () => confirm.replaceWith(actions)),
        act('Remove', async () => {
          const r = await send('DELETE', `/api/mcp/servers/${s.name}`);
          expanded = '';
          await after(r);
        }, 'connected-danger-solid'),
      );
      actions.replaceWith(confirm);
    });
    actions.append(remove);
    body.append(status, actions);
    return body;
  }

  function missingNote(m: Missing): HTMLElement {
    const p = el('p', 'settings-hint connected-warn', `This runs with ${m.name}, which is not on this computer yet. `);
    if (m.link) p.append(link(`Get ${m.name}`, m.link), document.createTextNode(', then choose Reconnect.'));
    return p;
  }

  function keyForm(s: ServerRow, names: readonly string[]): HTMLElement {
    const box = el('div', 'connected-fields');
    box.append(el('p', 'settings-hint connected-warn', 'It needs a key that is not saved yet.'));
    const inputs = new Map<string, HTMLInputElement>();
    for (const n of names) {
      const row = el('div', 'settings-row');
      const label = el('label', '', n.split('/').pop() ?? n);
      const input = el('input');
      input.type = 'password';
      input.autocomplete = 'off';
      input.id = `key-${s.name}-${n}`.replace(/[^a-z0-9-]/gi, '-');
      label.htmlFor = input.id;
      inputs.set(n, input);
      row.append(label, input);
      box.append(row);
    }
    const save = button('Save to keychain and connect', 'approve', async () => {
      save.disabled = true;
      const secrets = Object.fromEntries([...inputs].map(([k, i]) => [k, i.value.trim()]));
      await send('POST', `/api/mcp/servers/${s.name}/keys`, { secrets });
      await load();
      drawHome();
      watch();
    });
    box.append(save);
    return box;
  }

  function toolList(s: ServerRow): HTMLElement {
    const wrap = el('div', 'connected-tools-wrap');
    const top = el('div', 'connected-tools-top');
    top.append(el('strong', '', 'What she may do on her own'));
    const reading = readingTools(s.tools);
    if (reading.length) {
      const b = button(`Let ${reading.length} reading tool${reading.length === 1 ? '' : 's'} run`, 'quiet', async () => {
        b.disabled = true;
        await setTools(s, Object.fromEntries(reading.map((t) => [t.name, 'read'])));
      });
      b.title = 'Tools the service says only look things up. Anything that changes something keeps asking you first.';
      top.append(b);
    }
    wrap.append(top);
    const list = el('ul', 'connected-tools');
    for (const t of s.tools) {
      const row = el('li');
      const words = el('span');
      words.append(el('strong', '', t.name), el('small', '', t.description || (t.says_read_only ? 'Looks something up.' : '')));
      const sw = el('label', 'connected-switch');
      const box = el('input');
      box.type = 'checkbox';
      box.setAttribute('role', 'switch');
      box.checked = !t.asks_first;
      box.setAttribute('aria-label', `${t.name} runs without asking`);
      box.addEventListener('change', () => void setTools(s, { [t.name]: box.checked ? 'read' : 'ask' }));
      const said = el('small', '', box.checked ? 'Runs on its own' : 'Asks first');
      sw.append(said, box, el('span', 'connected-switch-track'));
      if (t.asks_first && t.says_read_only) sw.title = 'The service says this only reads. It still asks until you turn this on.';
      row.append(words, sw);
      list.append(row);
    }
    wrap.append(list);
    return wrap;
  }

  async function setTools(s: ServerRow, tools: Record<string, 'read' | 'ask'>): Promise<void> {
    const r = await send('PATCH', `/api/mcp/servers/${s.name}`, { tools });
    await load();
    if (view === 'home') drawHome();
    else if (followDraw && r.ok) followDraw(r.data as unknown as ServerRow);
  }

  // ── Signing in ────────────────────────────────────────────────────────

  /** Opened from a click, so it is not blocked; the new tab cannot reach this page. */
  function openSignIn(url: string): void {
    if (!signInTab().go(url)) window.location.assign(url);
  }

  // ── Setting up a featured service ─────────────────────────────────────

  function drawSetup(e: CatalogEntry): void {
    view = 'setup';
    const parts = header(e.title, '', () => drawHome());
    const hero = el('div', 'connected-hero');
    const words = el('div');
    const title = parts[1] ?? el('h2', '', e.title); // the heading, beside the service's tile
    words.append(title, el('p', 'settings-hint', e.blurb));
    hero.append(mark(e.color, e.glyph, 'lg', e.id), words);
    parts.splice(1, 1, hero);


    const what = el('div', 'connected-box');
    what.append(
      el('small', 'connected-label', e.local ? 'Runs on this computer' : 'Connects to'),
      el('p', 'settings-path', e.connects),
      el(
        'p',
        'settings-hint',
        e.local
          ? `A program ${e.title} publishes, run with your permissions. This exact version, nothing newer.`
          : `${e.title}'s own server. Nothing is installed on this computer.`,
      ),
    );
    parts.push(what);
    const needs = e.local ? e.connects.split(' ')[0] : '';
    if (needs && last?.programs?.[needs] === false) {
      parts.push(missingNote({ program: needs, name: needs === 'npx' ? 'Node.js' : needs, link: needs === 'npx' ? 'https://nodejs.org/en/download' : '' }));
    }

    const inputs = new Map<string, HTMLInputElement>();
    for (const f of e.fields) {
      const row = el('div', 'settings-row');
      const label = el('label', '', f.label);
      const input = el('input');
      input.type = f.secret ? 'password' : 'text';
      input.placeholder = f.placeholder;
      input.autocomplete = 'off';
      input.spellcheck = false;
      input.id = `field-${e.id}-${f.id}`;
      label.htmlFor = input.id;
      inputs.set(f.id, input);
      row.append(label, input);
      if (f.link) row.append(link(f.link_label || 'Where to get it', f.link));
      if (f.secret) row.append(el('small', 'settings-hint', 'Saved in your system keychain. It is never shown again or written to a file.'));
      parts.push(row);
    }

    const how = el('ul', 'connected-promises');
    const promises = [
      e.how === 'sign_in' ? `You sign in with ${e.title} in your browser. Trillion never sees your password.` : '',
      'Every tool asks you first. After connecting you choose which may run on their own.',
      "What it sends back is treated as information, never as instructions.",
      e.note,
    ].filter(Boolean);
    for (const p of promises) how.append(el('li', '', p));
    parts.push(how);

    const status = el('div', 'connected-progress');
    status.setAttribute('role', 'status');
    const go = button(e.how === 'sign_in' ? `Sign in with ${e.title}` : 'Connect', 'approve connected-go', async () => {
      const values = Object.fromEntries([...inputs].map(([k, i]) => [k, i.value]));
      if (e.fields.some((f) => !values[f.id]?.trim())) {
        status.replaceChildren(el('p', 'connected-warn', `${e.fields.find((f) => !values[f.id]?.trim())?.label} is needed.`));
        return;
      }
      // Opened now, from the click, so a pop-up blocker lets it through; it is
      // pointed at the sign-in page once the server says where that is.
      const tab = e.how === 'sign_in' ? signInTab(`Signing in to ${e.title}…`) : null;
      go.disabled = true;
      status.replaceChildren(el('p', 'settings-hint', 'Connecting…'));
      const r = await send('POST', '/api/mcp/servers', { catalog: e.id, values });
      if (!r.ok) {
        tab?.close();
        go.disabled = false;
        status.replaceChildren(el('p', 'connected-warn', String(r.data.detail || 'That did not work.')));
        return;
      }
      const row = r.data as unknown as ServerRow;
      if (tab && row.sign_in_url) tab.go(row.sign_in_url);
      else tab?.close();
      await load();
      follow(row, status, actions);
    });
    const actions = el('div', 'settings-actions');
    actions.append(button('Cancel', 'quiet', () => drawHome()), go);
    parts.push(status, actions);
    form.replaceChildren(...parts);
    inputs.values().next().value?.focus();
  }

  /** Show a server as it connects: sign in, then choose what it may do. */
  function follow(first: ServerRow, status: HTMLElement, actions: HTMLElement): void {
    following = first.name;
    const draw = (s: ServerRow): void => {
      if (s.status === 'starting') {
        status.replaceChildren(el('p', 'settings-hint', 'Connecting…'));
      } else if (s.status === 'signing_in') {
        const p = el('div', 'connected-waiting');
        p.append(
          el('p', '', `Finish signing in to ${s.title} ${opened()}. This page carries on by itself.`),
          button('Open the sign-in page again', 'quiet', () => openSignIn(s.sign_in_url)),
        );
        status.replaceChildren(p);
      } else if (s.status === 'connected') {
        followDraw = null;
        const done = el('div', 'connected-done');
        done.append(el('p', 'connected-ok', `${s.title} is connected. ${s.tools.length === 1 ? 'Its one tool asks' : `All ${s.tools.length} tools ask`} you first until you say otherwise.`));
        if (s.tools.length) done.append(toolList(s));
        status.replaceChildren(done);
        actions.replaceChildren(button('Done', 'approve', () => {
          expanded = '';
          drawHome();
        }));
        followDraw = (next) => {
          const list = done.querySelector('.connected-tools-wrap');
          if (list && next.tools) list.replaceWith(toolList(next));
        };
      } else {
        const p = el('div', 'connected-failed-box');
        p.append(el('p', 'connected-warn', statusText(s)));
        if (s.missing) p.append(missingNote(s.missing));
        status.replaceChildren(p);
        actions.replaceChildren(
          button('Remove it', 'quiet', async () => {
            await send('DELETE', `/api/mcp/servers/${s.name}`);
            await load();
            drawHome();
          }),
          button('Try again', 'approve', async () => {
            const r = await send('POST', `/api/mcp/servers/${s.name}/reconnect`);
            await load();
            draw(r.data as unknown as ServerRow);
            watch();
          }),
        );
      }
    };
    followDraw = draw;
    draw(first);
    watch();
  }

  // ── Google (built in) ─────────────────────────────────────────────────

  const googleEntry = (service: string): CatalogEntry | undefined => last?.catalog?.find((e) => e.service === service);

  function googleCard(service: string): HTMLElement {
    const key = `google:${service}`;
    const sv = g?.services[service];
    const open = expanded === key;
    const box = el('div', `connected-card${open ? ' is-open' : ''}`);
    box.dataset.server = key;
    const head = el('div', 'connected-card-head');
    const words = el('span', 'connected-card-words');
    const state = el('small', 'connected-state');
    const said = el('span', 'connected-state-main');
    said.append(el('span', 'connected-dot connected-dot-on'), document.createTextNode(`Connected · ${(sv?.accounts ?? []).join(', ')}`));
    state.append(said);
    const tools = sv?.tools ?? [];
    const free = tools.filter((t) => !t.asks_first).length;
    state.append(document.createTextNode(` · ${tools.length} tools, ${free ? `${free} run${free === 1 ? 's' : ''} without asking` : 'all ask first'}`));
    words.append(el('strong', '', sv?.title ?? service), state);
    const side = el('span', 'connected-card-side');
    const toggle = button(open ? 'Close' : 'Manage', 'quiet', () => {
      expanded = open ? '' : key;
      drawHome();
    });
    toggle.setAttribute('aria-expanded', String(open));
    side.append(toggle);
    const entry = googleEntry(service);
    head.append(entry ? mark(entry.color, entry.glyph, 'sm', entry.id) : plainMark(), words, side);
    box.append(head);
    if (open) box.append(googleManage(service));
    return box;
  }

  function googleManage(service: string): HTMLElement {
    const body = el('div', 'connected-manage');
    const sv = g?.services[service];
    if (sv) body.append(googleTools(service, sv.tools));
    const status = el('p', 'settings-status');
    status.setAttribute('role', 'status');
    if (service === 'drive') body.append(driveFiles(sv?.accounts ?? [], status));
    const accounts = el('div', 'connected-accounts');
    for (const email of sv?.accounts ?? []) {
      const row = el('div', 'connected-account');
      row.append(el('span', '', email));
      const remove = button('Remove', 'quiet connected-danger', () => {
        const confirm = el('div', 'connected-confirm');
        confirm.append(
          el('span', '', `Disconnect ${sv?.title ?? service} for ${email}? If it was the last Google service for this account, Trillion signs out of it with Google.`),
          button('Keep it', 'quiet', () => confirm.replaceWith(row)),
          button('Disconnect', 'connected-danger-solid', async () => {
            const r = await send('POST', '/api/google/remove', { email, service });
            if (!r.ok) status.textContent = String(r.data.detail || 'That did not work.');
            expanded = '';
            await load();
            drawHome();
          }),
        );
        row.replaceWith(confirm);
      });
      row.append(remove);
      accounts.append(row);
    }
    const entry = googleEntry(service);
    const another = button('Add another Google account', 'quiet', () => entry && drawGoogle(entry));
    body.append(accounts, another, status);
    return body;
  }

  function googleTools(_service: string, tools: readonly GoogleTool[]): HTMLElement {
    return switchList(tools, setGoogleTools, 'Looking things up, and writing drafts only you see. Sending email and changing your calendar always ask.');
  }

  /** Drive: she reads only files the user picked in Google's own file picker. */
  function driveFiles(accounts: readonly string[], status: HTMLElement): HTMLElement {
    const box = el('div', 'connected-tools-wrap');
    const top = el('div', 'connected-tools-top');
    top.append(el('strong', '', 'Files she can read'));
    box.append(top, el('p', 'settings-hint', 'She sees only the files you choose here, never the rest of your Drive.'));
    if (!g?.picker) {
      box.append(pickerKeyField(() => drawHome()));
      return box;
    }
    for (const email of accounts) {
      const choose = button(accounts.length > 1 ? `Choose files from ${email}` : 'Choose files', 'quiet', async () => {
        const tab = signInTab();
        const r = await send('POST', '/api/google/picker', { email });
        if (!r.ok) {
          tab?.close();
          status.textContent = String(r.data.detail || 'That did not work.');
          return;
        }
        const url = String(r.data.url ?? '');
        if (!tab.go(url)) window.location.assign(url);
        status.textContent = `Choose the files ${opened()}, then come back here.`;
      });
      top.append(choose);
      const picked = g.files[email] ?? [];
      const list = el('ul', 'connected-tools');
      for (const f of picked) {
        const row = el('li');
        row.append(el('span', '', f.name || f.id), button('Remove', 'quiet', async () => {
          await send('POST', '/api/google/files/forget', { email, id: f.id });
          await load();
          drawHome();
        }));
        list.append(row);
      }
      if (!picked.length) box.append(el('p', 'settings-hint', 'No files yet.'));
      else box.append(list);
    }
    return box;
  }

  function pickerKeyField(after: () => void): HTMLElement {
    const wrap = el('div', 'connected-fields');
    const [row, input] = field('Picker API key', 'AIza…', false);
    const status = el('p', 'settings-status');
    const save = button('Save', 'approve', async () => {
      save.disabled = true;
      const r = await send('PUT', '/api/google/picker-key', { key: input.value });
      save.disabled = false;
      if (!r.ok) {
        status.textContent = String(r.data.detail || 'That did not work.');
        return;
      }
      await load();
      after();
    });
    wrap.append(el('p', 'settings-hint connected-warn', 'Choosing Drive files needs the Picker API key from the same Google Cloud project.'), row, status, save);
    return wrap;
  }

  async function setGoogleTools(tools: Record<string, 'read' | 'ask'>): Promise<void> {
    await send('PATCH', '/api/google/tools', { tools });
    await load();
    if (view === 'home') drawHome();
    else if (googleFollow) googleFollow();
  }

  const GOOGLE_DOES: Record<string, string[]> = {
    gmail: ['Search and read your email', 'Write drafts only you see', 'Send email, always asking you first'],
    calendar: ['See your events and when you are free', 'Add, change and delete events, always asking you first'],
    drive: ['Read the Drive files you choose: Docs, Sheets, Slides and text', 'Never the rest of your Drive: you pick each file in Google’s own file picker'],
  };

  function drawGoogle(e: CatalogEntry): void {
    view = 'google';
    googleFollow = null;
    const service = e.service ?? '';
    const parts = header(e.title, '', () => drawHome());
    const hero = el('div', 'connected-hero');
    const words = el('div');
    words.append(parts[1] ?? el('h2', '', e.title), el('p', 'settings-hint', e.blurb));
    hero.append(mark(e.color, e.glyph, 'lg', e.id), words);
    parts.splice(1, 1, hero);
    if (!g?.ready) {
      parts.push(...googleSetup(e));
      form.replaceChildren(...parts);
      return;
    }
    const what = el('div', 'connected-box');
    what.append(el('small', 'connected-label', 'Built into Trillion'));
    const does = el('ul', 'connected-promises');
    for (const d of GOOGLE_DOES[service] ?? []) does.append(el('li', '', d));
    what.append(does);
    parts.push(what);
    const already = g.services[service]?.accounts ?? [];
    if (already.length) parts.push(el('p', 'settings-hint', `Already connected: ${already.join(', ')}. Sign in again to add another account.`));
    const promises = el('ul', 'connected-promises');
    for (const p of [
      'You sign in with Google in your browser. Trillion never sees your password.',
      'Each tool asks you first until you say otherwise.',
      'What emails, events and files say is treated as information, never as instructions.',
    ]) promises.append(el('li', '', p));
    parts.push(promises);
    const status = el('div', 'connected-progress');
    status.setAttribute('role', 'status');
    const actions = el('div', 'settings-actions');
    const go = button('Sign in with Google', 'approve connected-go', async () => {
      const tab = signInTab('Signing in with Google…');
      go.disabled = true;
      const r = await send('POST', '/api/google/sign-in', { service });
      if (!r.ok) {
        tab?.close();
        go.disabled = false;
        status.replaceChildren(el('p', 'connected-warn', String(r.data.detail || 'That did not work.')));
        return;
      }
      const url = String(r.data.url ?? '');
      if (!tab.go(url)) window.location.assign(url);
      await load();
      googleFollow = () => {
        const a = g?.attempts[service];
        if (!a || a.status === 'signing_in') {
          const p = el('div', 'connected-waiting');
          p.append(el('p', '', `Finish signing in with Google ${opened()}. This page carries on by itself.`), button('Open the sign-in page again', 'quiet', () => openSignIn(url)));
          status.replaceChildren(p);
        } else if (a.status === 'connected') {
          const done = el('div', 'connected-done');
          done.append(el('p', 'connected-ok', `${e.title} is connected for ${a.email}.`), googleTools(service, g?.services[service]?.tools ?? []));
          if (service === 'drive') {
            const note = el('p', 'settings-status');
            done.append(driveFiles([a.email], note), note);
          }
          status.replaceChildren(done);
          actions.replaceChildren(button('Done', 'approve', () => {
            googleFollow = null;
            drawHome();
          }));
        } else {
          status.replaceChildren(el('p', 'connected-warn', `Not connected: ${a.problem}`));
          go.disabled = false;
        }
      };
      googleFollow();
      watch();
    });
    actions.append(button('Cancel', 'quiet', () => drawHome()), go);
    parts.push(status, actions);
    form.replaceChildren(...parts);
  }

  /** The one-time setup: Google needs an OAuth client in a Google Cloud project. */
  function googleSetup(e: CatalogEntry): HTMLElement[] {
    const out: HTMLElement[] = [];
    out.push(
      el(
        'p',
        'settings-note',
        'Google asks every app that signs people in to register first. This copy of Trillion has not been registered yet, so it takes a one-time setup in your own free Google Cloud project. It takes about five minutes, and then Gmail, Calendar and Drive all sign in with one click.',
      ),
    );
    const steps = el('ol', 'connected-steps');
    const step = (title: string, ...rest: (Node | string)[]): void => {
      const li = el('li');
      li.append(el('strong', '', title), ...rest);
      steps.append(li);
    };
    step('Make a project. ', link('Create a Google Cloud project', 'https://console.cloud.google.com/projectcreate'), document.createTextNode(' (any name, like "Trillion").'));
    const apis = el('span');
    apis.append(document.createTextNode('Turn on '));
    const list = Object.values(g?.apis ?? {});
    list.forEach((a, i) => {
      apis.append(link(a.name, `https://console.cloud.google.com/apis/library/${a.id}`));
      apis.append(document.createTextNode(i < list.length - 2 ? ', ' : i === list.length - 2 ? ' and ' : '.'));
    });
    apis.append(document.createTextNode(' For Drive, also turn on the '), link('Google Picker API', 'https://console.cloud.google.com/apis/library/picker.googleapis.com'), document.createTextNode(' and make an API key restricted to it.'));
    step('Switch on the APIs. ', apis);
    step(
      'Describe the app. ',
      link('Open Google Auth Platform', 'https://console.cloud.google.com/auth/overview'),
      document.createTextNode(': Get started, choose External, and add your own address as a test user. Then, under Audience, choose Publish app, so the sign-in does not expire after a week. Google will show "unverified app" when you sign in: it is your own, so choose Continue.'),
    );
    step(
      'Make a client. ',
      link('Create an OAuth client', 'https://console.cloud.google.com/auth/clients/create'),
      document.createTextNode(': Application type Desktop app, then Create, then Download JSON.'),
    );
    step('Give it to Trillion. ', document.createTextNode('Paste the downloaded file below, or its client ID and secret.'));
    out.push(steps);
    const area = el('textarea', 'connected-paste');
    area.rows = 4;
    area.spellcheck = false;
    area.placeholder = 'Paste the JSON file Google gave you: {"installed":{"client_id":"…","client_secret":"…"}}';
    area.setAttribute('aria-label', 'The JSON file Google gave you');
    out.push(area);
    const idRow = el('div', 'settings-row');
    const idLabel = el('label', '', 'Or the client ID');
    const id = el('input');
    id.id = 'google-client-id';
    id.placeholder = '1234-abc.apps.googleusercontent.com';
    id.spellcheck = false;
    idLabel.htmlFor = id.id;
    idRow.append(idLabel, id);
    const secretRow = el('div', 'settings-row');
    const secretLabel = el('label', '', 'and the client secret');
    const secret = el('input');
    secret.id = 'google-client-secret';
    secret.type = 'password';
    secret.autocomplete = 'off';
    secretLabel.htmlFor = secret.id;
    secretRow.append(secretLabel, secret, el('small', 'settings-hint', 'Kept in your system keychain.'));
    const keyRow = el('div', 'settings-row');
    const keyLabel = el('label', '', 'Picker API key, for Google Drive (optional)');
    const pickerKey = el('input');
    pickerKey.id = 'google-picker-key';
    pickerKey.placeholder = 'AIza…';
    pickerKey.spellcheck = false;
    pickerKey.autocomplete = 'off';
    keyLabel.htmlFor = pickerKey.id;
    keyRow.append(keyLabel, pickerKey, el('small', 'settings-hint', 'An API key restricted to the Google Picker API, so you can choose which Drive files she may read.'));
    out.push(idRow, secretRow, keyRow);
    const status = el('p', 'settings-status');
    status.setAttribute('role', 'status');
    const save = button('Save and continue', 'approve', async () => {
      save.disabled = true;
      const r = await send('PUT', '/api/google/client', { file: area.value, client_id: id.value, client_secret: secret.value, picker_key: pickerKey.value });
      save.disabled = false;
      if (!r.ok) {
        status.textContent = String(r.data.detail || 'That did not work.');
        return;
      }
      await load();
      drawGoogle(e);
    });
    const actions = el('div', 'settings-actions');
    actions.append(button('Cancel', 'quiet', () => drawHome()), save);
    out.push(status, actions);
    return out;
  }

  // ── Slack and Telegram (built in) ─────────────────────────────────────

  type Chat = 'slack' | 'telegram';

  function chatConnected(p: Chat): boolean {
    if (!sl) return false;
    return p === 'slack' ? sl.slack.connected || sl.channels.slack.configured : sl.channels.telegram.configured;
  }

  async function slackSend(method: 'POST' | 'PUT' | 'PATCH', path: string, body: unknown): Promise<string> {
    const r = await send(method, path, body);
    await load();
    return r.ok ? '' : String(r.data.detail || 'That did not work.');
  }

  function chatCard(p: Chat): HTMLElement {
    const key = `chat:${p}`;
    const open = expanded === key;
    const entry = last?.catalog?.find((e) => e.id === p);
    const box = el('div', `connected-card${open ? ' is-open' : ''}`);
    box.dataset.server = key;
    const head = el('div', 'connected-card-head');
    const words = el('span', 'connected-card-words');
    const state = el('small', 'connected-state');
    const ch = sl?.channels[p];
    const bits: string[] = [];
    if (p === 'slack' && sl?.slack.connected) bits.push(`Tools in ${sl.slack.workspace || 'your workspace'}`);
    if (ch?.configured) bits.push(chatText(ch));
    const live = ch?.status === 'connected' || (p === 'slack' && sl?.slack.connected);
    const said = el('span', 'connected-state-main');
    said.append(el('span', `connected-dot connected-dot-${ch?.status === 'failed' ? 'bad' : live ? 'on' : 'wait'}`), document.createTextNode(bits.join(' · ')));
    state.append(said);
    const waiting = ch?.knocks.length ?? 0;
    if (waiting) state.append(document.createTextNode(` · ${waiting} waiting for you to allow`));
    words.append(el('strong', '', entry?.title ?? p), state);
    const side = el('span', 'connected-card-side');
    side.append(button('Manage', 'quiet', () => entry && drawChat(entry, p)));
    head.append(entry ? mark(entry.color, entry.glyph, 'sm', entry.id) : plainMark(), words, side);
    box.append(head);
    return box;
  }

  function people(p: Chat): HTMLElement {
    const ch = sl?.channels[p];
    const wrap = el('div', 'connected-people');
    const status = el('p', 'settings-status');
    status.setAttribute('role', 'status');
    const act = async (path: string, id: string): Promise<void> => {
      status.textContent = await slackSend('POST', path, { platform: p, id });
      chatRedraw?.();
    };
    if (ch?.knocks.length) {
      wrap.append(el('strong', '', 'Waiting for you to allow'));
      for (const k of ch.knocks) {
        const row = el('div', 'connected-account connected-knock');
        row.append(el('span', '', `${k.name} wrote ${k.times === 1 ? 'once' : `${k.times} times`}`));
        const btns = el('span', 'connected-card-side');
        btns.append(button('Ignore', 'quiet', () => void act('/api/channels/ignore', k.id)), button('Allow', 'approve', () => void act('/api/channels/allow', k.id)));
        row.append(btns);
        wrap.append(row);
      }
    }
    wrap.append(el('strong', '', 'Who she answers'));
    if (!ch?.allowed.length) {
      wrap.append(el('p', 'settings-hint', p === 'slack' ? 'Nobody yet. Message the Trillion app in Slack (under Apps), and you will appear above to allow.' : `Nobody yet. Open your bot in Telegram${ch?.handle ? ` (@${ch.handle})` : ''} and say hi, and you will appear above to allow.`));
    }
    for (const a of ch?.allowed ?? []) {
      const row = el('div', 'connected-account');
      row.append(el('span', '', a.name), button('Remove', 'quiet connected-danger', () => void act('/api/channels/disallow', a.id)));
      wrap.append(row);
    }
    wrap.append(status);
    return wrap;
  }

  function chatControls(p: Chat): HTMLElement {
    const ch = sl?.channels[p];
    const box = el('div', 'connected-box');
    const head = el('div', 'connected-card-head');
    const words = el('span', 'connected-card-words');
    const said = el('small', 'connected-state');
    said.append(el('span', `connected-dot connected-dot-${ch?.status === 'connected' ? 'on' : ch?.status === 'failed' ? 'bad' : ch?.on ? 'wait' : 'off'}`), document.createTextNode(ch ? chatText(ch) : ''));
    words.append(el('strong', '', p === 'slack' ? 'Talking to her in Slack' : 'Talking to her in Telegram'), said);
    const side = el('span', 'connected-card-side');
    side.append(
      button(ch?.on ? 'Turn off' : 'Turn on', 'quiet', async () => {
        await slackSend('POST', '/api/channels/turn', { platform: p, on: !ch?.on });
        chatRedraw?.();
      }),
      button('Disconnect', 'quiet connected-danger', async () => {
        await slackSend('POST', '/api/channels/forget', { platform: p });
        chatRedraw?.();
      }),
    );
    head.append(words, side);
    box.append(head, el('p', 'settings-hint', 'She only answers here, with no tools: she cannot act, look anything up, or approve anything. Only people you allow get an answer, and in a group only when they @mention her.'), people(p));
    return box;
  }

  function field(label: string, placeholder: string, secret = true): [HTMLElement, HTMLInputElement] {
    const row = el('div', 'settings-row');
    const l = el('label', '', label);
    const input = el('input');
    input.id = `f-${label.toLowerCase().replace(/[^a-z]+/g, '-')}`;
    input.type = secret ? 'password' : 'text';
    input.placeholder = placeholder;
    input.autocomplete = 'off';
    input.spellcheck = false;
    l.htmlFor = input.id;
    row.append(l, input);
    return [row, input];
  }

  function drawChat(e: CatalogEntry, p: Chat): void {
    view = 'chat';
    const draw = (): void => {
      const parts = header(e.title, '', () => {
        chatRedraw = null;
        drawHome();
      });
      const hero = el('div', 'connected-hero');
      const words = el('div');
      words.append(parts[1] ?? el('h2', '', e.title), el('p', 'settings-hint', e.blurb));
      hero.append(mark(e.color, e.glyph, 'lg', e.id), words);
      parts.splice(1, 1, hero);
      parts.push(...(p === 'slack' ? slackSheet() : telegramSheet()));
      form.replaceChildren(...parts);
    };
    chatRedraw = draw;
    draw();
    watch();
  }

  function slackSheet(): HTMLElement[] {
    const out: HTMLElement[] = [];
    const s = sl?.slack;
    const ch = sl?.channels.slack;
    if (!s?.connected || !ch?.configured) {
      const steps = el('ol', 'connected-steps');
      const li = (title: string, ...rest: (Node | string)[]): void => {
        const item = el('li');
        item.append(el('strong', '', title), ...rest);
        steps.append(item);
      };
      li('Make the app. ', link('Create the Trillion app in Slack', s?.create_app_url ?? 'https://api.slack.com/apps'), document.createTextNode(': pick your workspace, then Next and Create. Everything is filled in for you.'));
      li('Install it. ', document.createTextNode('On the app’s page, open Install App, choose Install to your workspace, then Allow.'));
      li('Copy its tokens. ', document.createTextNode('The same page shows a User OAuth Token (xoxp-) and a Bot User OAuth Token (xoxb-). For talking to her, also open Basic Information → App-Level Tokens → Generate, add the connections:write scope, and copy it (xapp-).'));
      out.push(el('p', 'settings-note', 'Slack connects through an app of your own, made in a minute from the settings below. Nothing is installed on this computer, and nothing here can be reached from the internet.'), steps);
    }
    // Her tools in Slack.
    const tools = el('div', 'connected-box');
    tools.append(el('small', 'connected-label', 'Let her use your Slack'));
    if (s?.connected) {
      tools.append(el('p', 'connected-ok', `Connected to ${s.workspace || 'your workspace'}${s.user ? ` as ${s.user}` : ''}.`));
      tools.append(switchList(s.tools, async (choices) => {
        await slackSend('PATCH', '/api/slack/tools', { tools: choices });
        chatRedraw?.();
      }, 'Searching and reading. Posting always asks.'));
      tools.append(button('Disconnect her tools', 'quiet connected-danger', async () => {
        await slackSend('POST', '/api/slack/user/forget', {});
        chatRedraw?.();
      }));
    } else {
      tools.append(el('p', 'settings-hint', 'Search and read your messages, and post as you. Posting always asks you first.'));
      const [row, input] = field('User OAuth Token', 'xoxp-…');
      const status = el('p', 'settings-status');
      const go = button('Connect', 'approve', async () => {
        go.disabled = true;
        status.textContent = await slackSend('PUT', '/api/slack/user', { token: input.value });
        go.disabled = false;
        if (!status.textContent) chatRedraw?.();
      });
      tools.append(row, el('small', 'settings-hint', 'Checked with Slack, then kept in your system keychain.'), status, go);
    }
    out.push(tools);
    // Talking to her.
    if (ch?.configured) out.push(chatControls('slack'));
    else {
      const chat = el('div', 'connected-box');
      chat.append(el('small', 'connected-label', 'Talk to her in Slack'), el('p', 'settings-hint', 'Message her directly, or @mention her in a channel. She answers in the thread.'));
      const [botRow, bot] = field('Bot User OAuth Token', 'xoxb-…');
      const [appRow, app] = field('App-level token', 'xapp-…');
      const status = el('p', 'settings-status');
      const go = button('Turn on', 'approve', async () => {
        go.disabled = true;
        status.textContent = await slackSend('PUT', '/api/channels/slack', { bot_token: bot.value, app_token: app.value });
        go.disabled = false;
        if (!status.textContent) chatRedraw?.();
      });
      chat.append(botRow, appRow, status, go);
      out.push(chat);
    }
    return out;
  }

  function telegramSheet(): HTMLElement[] {
    const out: HTMLElement[] = [];
    const ch = sl?.channels.telegram;
    if (ch?.configured) {
      out.push(chatControls('telegram'));
      if (ch.handle) out.push(el('p', 'settings-hint'), link(`Open @${ch.handle} in Telegram`, `https://t.me/${encodeURIComponent(ch.handle)}`));
      return out;
    }
    out.push(el('p', 'settings-note', 'This works through a Telegram bot of your own. Making one takes a minute, and nothing on this computer can be reached from the internet.'));
    const steps = el('ol', 'connected-steps');
    const li = (title: string, ...rest: (Node | string)[]): void => {
      const item = el('li');
      item.append(el('strong', '', title), ...rest);
      steps.append(item);
    };
    li('Make a bot. ', document.createTextNode('In Telegram, open '), link('@BotFather', 'https://t.me/BotFather'), document.createTextNode(' and send /newbot. Give it a name and a username ending in “bot”.'));
    li('Copy its token. ', document.createTextNode('BotFather replies with a token like 123456:ABC…'));
    li('Paste it here. ', document.createTextNode('Then say hi to your bot, and allow yourself below.'));
    out.push(steps);
    const [row, input] = field('Bot token', '123456789:ABC…');
    const status = el('p', 'settings-status');
    const go = button('Connect', 'approve', async () => {
      go.disabled = true;
      status.textContent = await slackSend('PUT', '/api/channels/telegram', { token: input.value });
      go.disabled = false;
      if (!status.textContent) chatRedraw?.();
    });
    const actions = el('div', 'settings-actions');
    actions.append(button('Cancel', 'quiet', () => {
      chatRedraw = null;
      drawHome();
    }), go);
    out.push(row, el('small', 'settings-hint', 'Checked with Telegram, then kept in your system keychain.'), status, actions);
    return out;
  }

  /** Tool switches for a built-in connection: only tools that change nothing can run on their own. */
  function switchList(tools: readonly GoogleTool[], save: (choices: Record<string, 'read' | 'ask'>) => Promise<void>, hint: string): HTMLElement {
    const wrap = el('div', 'connected-tools-wrap');
    const top = el('div', 'connected-tools-top');
    top.append(el('strong', '', 'What she may do on her own'));
    const reading = tools.filter((t) => t.freeable && t.asks_first);
    if (reading.length) {
      const b = button(`Let ${reading.length} reading tool${reading.length === 1 ? '' : 's'} run`, 'quiet', async () => {
        b.disabled = true;
        await save(Object.fromEntries(reading.map((t) => [t.name, 'read'])));
      });
      b.title = hint;
      top.append(b);
    }
    wrap.append(top);
    const list = el('ul', 'connected-tools');
    for (const t of tools) {
      const row = el('li');
      const words = el('span');
      words.append(el('strong', '', t.title), el('small', '', t.description));
      if (t.freeable) {
        const sw = el('label', 'connected-switch');
        const box = el('input');
        box.type = 'checkbox';
        box.setAttribute('role', 'switch');
        box.checked = !t.asks_first;
        box.setAttribute('aria-label', `${t.title} runs without asking`);
        box.addEventListener('change', () => void save({ [t.name]: box.checked ? 'read' : 'ask' }));
        sw.append(el('small', '', box.checked ? 'Runs on its own' : 'Asks first'), box, el('span', 'connected-switch-track'));
        row.append(words, sw);
      } else row.append(words, el('small', 'connected-always', 'Always asks'));
      list.append(row);
    }
    wrap.append(list);
    return wrap;
  }

  // ── Adding your own ───────────────────────────────────────────────────

  function drawOwn(): void {
    view = 'own';
    followDraw = null;
    const parts = header(
      'Add your own',
      'Paste what the server’s instructions give you: its address, the command that runs it, or its settings for Claude Desktop, Cursor or VS Code. Keys in it are moved to your keychain.',
      () => drawHome(),
    );
    const area = el('textarea', 'connected-paste');
    area.rows = 7;
    area.spellcheck = false;
    area.placeholder = 'https://mcp.example.com/mcp\n\nor   npx -y @example/mcp-server\n\nor   { "mcpServers": { "example": { "command": "npx", "args": [...] } } }';
    area.setAttribute('aria-label', 'The server’s address, command or settings');
    const status = el('p', 'settings-status');
    status.setAttribute('role', 'status');
    const next = button('Continue', 'approve', async () => {
      next.disabled = true;
      const r = await send('POST', '/api/mcp/parse', { text: area.value });
      next.disabled = false;
      if (!r.ok) {
        status.textContent = String(r.data.detail || 'That could not be read.');
        return;
      }
      const found = (r.data.servers ?? []) as Proposal[];
      if (found.length) review(found, 0);
    });
    const actions = el('div', 'settings-actions');
    actions.append(button('Cancel', 'quiet', () => drawHome()), next);
    parts.push(area, status, actions);
    form.replaceChildren(...parts);
    area.focus();
  }

  function review(found: Proposal[], at: number): void {
    const p = found[at];
    if (!p) return;
    const parts = header('Check before connecting', '', () => drawOwn());
    const nameRow = el('div', 'settings-row');
    const nameLabel = el('label', '', 'Name');
    const name = el('input');
    name.id = 'own-name';
    name.value = p.title;
    name.maxLength = 60;
    nameLabel.htmlFor = name.id;
    nameRow.append(nameLabel, name);
    parts.push(nameRow);

    const what = el('div', `connected-box${p.local ? ' connected-box-local' : ''}`);
    what.append(el('small', 'connected-label', p.local ? 'Runs on this computer' : 'Connects to'), el('p', 'settings-path', p.connects));
    what.append(
      el(
        'p',
        'settings-hint',
        p.local
          ? 'This program runs with your permissions and can do anything you can. Trillion controls what it asks it to do, not what it does on its own. Only add servers you trust.'
          : p.signs_in
            ? `If it asks you to sign in, ${inApp() ? 'your browser opens' : 'a tab opens'} for that.`
            : 'Its key is sent with every request.',
      ),
    );
    parts.push(what);
    if (p.missing) parts.push(missingNote(p.missing));

    const inputs = new Map<string, HTMLInputElement>();
    for (const s of p.secrets) {
      const row = el('div', 'settings-row');
      const label = el('label', '', s.label);
      const input = el('input');
      input.type = 'password';
      input.value = s.value;
      input.autocomplete = 'off';
      input.id = `own-${s.ref}`.replace(/[^a-z0-9-]/gi, '-');
      input.placeholder = s.value ? '' : 'Paste the key';
      label.htmlFor = input.id;
      inputs.set(s.ref, input);
      row.append(label, input, el('small', 'settings-hint', 'Saved in your system keychain; mcp.json only names it.'));
      parts.push(row);
    }
    if (found.length > 1) parts.push(el('p', 'settings-hint', `What you pasted has ${found.length} servers. This is ${at + 1} of ${found.length}.`));

    const status = el('div', 'connected-progress');
    status.setAttribute('role', 'status');
    const actions = el('div', 'settings-actions');
    const go = button('Connect', 'approve', async () => {
      const secrets = Object.fromEntries([...inputs].map(([k, i]) => [k, i.value.trim()]));
      if (Object.values(secrets).some((v) => !v)) {
        status.replaceChildren(el('p', 'connected-warn', 'A key is needed.'));
        return;
      }
      const tab = p.signs_in ? signInTab() : null;
      go.disabled = true;
      status.replaceChildren(el('p', 'settings-hint', 'Connecting…'));
      const r = await send('POST', '/api/mcp/servers', { entry: p.entry, proposed: p.name, title: name.value, secrets });
      if (!r.ok) {
        tab?.close();
        go.disabled = false;
        status.replaceChildren(el('p', 'connected-warn', String(r.data.detail || 'That did not work.')));
        return;
      }
      const row = r.data as unknown as ServerRow;
      if (tab && row.sign_in_url) tab.go(row.sign_in_url);
      else tab?.close();
      await load();
      follow(row, status, actions);
      if (at + 1 < found.length) actions.append(button('Next server', 'quiet', () => review(found, at + 1)));
    });
    actions.append(button('Cancel', 'quiet', () => drawHome()), go);
    parts.push(status, actions);
    form.replaceChildren(...parts);
  }

  return {
    async open() {
      host.showModal();
      if ((view === 'google' && !googleFollow) || view === 'chat') {
        view = 'home';
        chatRedraw = null;
      }
      if (view === 'home') form.replaceChildren(...header('Connected tools', ''), el('p', 'settings-hint', 'Loading…'));
      await load();
      if (view === 'home') drawHome();
      watch();
    },
    /** Open at one connection's card, expanded: after signing in, to confirm it. */
    reveal(key: string) {
      expanded = key;
      view = 'home';
      googleFollow = null;
      chatRedraw = null;
      window.setTimeout(() => {
        form.querySelector(`[data-server="${CSS.escape(key)}"]`)?.scrollIntoView({ block: 'center' });
      }, 300);
    },
    /**
     * Home's Connect: run that entry's tile action, as a tap on it would. Called just after
     * Settings opens, so it waits for that open (which resets the view) and for the data.
     */
    async start(catalogId: string) {
      await Promise.resolve();
      if (loading) await loading;
      else if (!last) await load();
      const e = byCatalog(catalogId);
      if (!e || !last) return;
      chatRedraw = null;
      googleFollow = null;
      act(e, last);
      watch();
    },
    /** Home's "+N more": the gallery, showing only that kind of app. */
    browse(cat: string) {
      category = cat;
      query = '';
      expanded = '';
      view = 'home';
      googleFollow = null;
      chatRedraw = null;
      if (last) drawHome();
    },
    summary: () => connectedSummary(last),
    // The board's source tiles need the servers before Settings is ever opened.
    load: async () => void (await load()),
    tiles: () => sourceTiles(last, g),
  };
}
