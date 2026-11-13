/**
 * Her browser (the her browser spec, decisions 11, 11a, 12 and 17).
 *
 * Settings → Her browser: set it up, open it to sign in ahead of time, the sites it
 * is signed in to, act mode, the stronger-model retry, the search engine and the live
 * picture. And the view: her browser's page, live, in the app. While a helper works it
 * is watch-only, with Stop; "Take control" or "Needs you" passes the user's mouse and
 * keyboard to the page until "I'm done". A small pill shows a working task, with a live
 * thumbnail, and says "Needs you" when it waits on the user.
 *
 * The live picture comes over `/ws/browser`, behind the same door as every socket. It
 * is drawn and dropped: nothing here keeps a frame.
 */

import { HER, type Feature } from '../features';
import { addIcon } from '../icons';
import type { Host } from '../panel';
import type { Api } from '../settings';
import './her-browser.css';

// A window with an address bar and a globe: her own browser.
addIcon('her-browser', ['M3.5 5h17v14h-17z', 'M3.5 8.5h17', 'M12 11a3.5 3.5 0 1 0 0 7a3.5 3.5 0 0 0 0-7z', 'M8.5 14.5h7']);

const CORE = import.meta.env.DEV ? '127.0.0.1:7777' : location.host;
const WS_URL = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${CORE}/ws/browser`;
const TOKEN_KEY = 'trillion-token';
const POLL_MS = 2500;
const KEYS = new Set(['Enter', 'Tab', 'Backspace', 'Delete', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown']);

export interface Ask {
  readonly kind: 'chip' | 'card' | 'step' | 'signin';
  readonly id: string;
  readonly text: string;
  readonly origin: string | null;
  readonly pending_id: string | null;
}

export interface TaskView {
  readonly id: string;
  readonly state: 'queued' | 'working' | 'needs_you' | 'done';
  readonly mode: 'research' | 'act';
  readonly url?: string | null;
  readonly origin?: string | null;
  readonly ask?: Ask | null;
  readonly in_control?: boolean;
  readonly steps?: number;
  readonly step_limit?: number;
  readonly outcome?: string | null;
}

export interface BrowserState {
  readonly about: string;
  readonly available: boolean;
  readonly installed: boolean;
  readonly version: string;
  readonly size_mb: number;
  readonly installing: boolean;
  readonly install_error: string | null;
  readonly settings: { act: boolean; stronger: boolean; search: string; live: boolean };
  readonly engines: readonly { id: string; name: string }[];
  readonly sites: readonly string[];
  readonly tasks: readonly TaskView[];
  readonly free: TaskView | null;
}

export interface Card {
  readonly summary: string;
  readonly detail: string;
  readonly expires_at: number;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(label: string, cls: string, onClick: () => void): HTMLButtonElement {
  const b = el('button', cls, label);
  b.type = 'button';
  b.addEventListener('click', onClick);
  return b;
}

function storedToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

/** The calm words for an error reply: the server's own short reason, or a plain line. */
export async function reason(r: Response): Promise<string> {
  try {
    const body = (await r.json()) as { error?: unknown };
    if (typeof body.error === 'string' && body.error.length < 200) return body.error;
  } catch {
    // Not JSON: the plain line below.
  }
  return 'That didn’t work. Try again in a moment.';
}

/** The task the view and the pill are about: one that needs the user first, then one at work. */
export function activeTask(state: BrowserState | null): TaskView | null {
  if (!state) return null;
  return state.tasks.find((t) => t.state === 'needs_you') ?? state.tasks.find((t) => t.state === 'working') ?? null;
}

/** What the pill says. */
export function pillWords(task: TaskView): string {
  if (task.state === 'needs_you') return task.ask?.text ?? 'Needs you';
  if (task.in_control) return 'You have control';
  return task.origin ? `Browsing ${task.origin.replace(/^https?:\/\//, '')}` : 'Browsing';
}

/** A page coordinate for a click on the picture, from where it was drawn. */
export function pagePoint(
  clientX: number,
  clientY: number,
  box: { left: number; top: number; width: number; height: number },
  page: { width: number; height: number },
): { x: number; y: number } {
  const x = ((clientX - box.left) / Math.max(box.width, 1)) * page.width;
  const y = ((clientY - box.top) / Math.max(box.height, 1)) * page.height;
  return { x: Math.max(0, Math.min(page.width, x)), y: Math.max(0, Math.min(page.height, y)) };
}

/** What a key press sends to the page while the user has control, or null for nothing. */
export function keyInput(ev: { key: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean }): Record<string, unknown> | null {
  if (ev.ctrlKey || ev.metaKey || ev.altKey) return null;
  if (KEYS.has(ev.key)) return { kind: 'key', key: ev.key };
  if (ev.key === ' ') return { kind: 'key', key: 'Space' };
  if (ev.key.length === 1) return { kind: 'text', text: ev.key };
  return null;
}

interface Watch {
  watch(task: string): void;
  input(task: string, body: Record<string, unknown>): void;
  close(): void;
}

function connect(onFrame: (frame: { data: string; width: number; height: number; url: string | null; in_control: boolean }) => void): Watch {
  const token = storedToken();
  let ws: WebSocket | null = null;
  let wanted: string | null = null;
  let ready = false;
  const open = (): void => {
    if (!token) return;
    ws = new WebSocket(WS_URL);
    ws.addEventListener('open', () => ws?.send(JSON.stringify({ type: 'hello', token })));
    ws.addEventListener('message', (ev) => {
      let msg: Record<string, unknown>;
      try {
        msg = JSON.parse(String(ev.data)) as Record<string, unknown>;
      } catch {
        return;
      }
      if (msg.type === 'browser.ready') {
        ready = true;
        if (wanted) ws?.send(JSON.stringify({ type: 'watch', task: wanted }));
      } else if (msg.type === 'browser.frame' && typeof msg.data === 'string') {
        onFrame({
          data: msg.data,
          width: typeof msg.width === 'number' ? msg.width : 1280,
          height: typeof msg.height === 'number' ? msg.height : 800,
          url: typeof msg.url === 'string' ? msg.url : null,
          in_control: msg.in_control === true,
        });
      }
    });
    ws.addEventListener('close', () => {
      ready = false;
      ws = null;
    });
  };
  open();
  return {
    watch(task) {
      wanted = task;
      if (!ws) open();
      else if (ready) ws.send(JSON.stringify({ type: 'watch', task }));
    },
    input(task, body) {
      if (ws && ready) ws.send(JSON.stringify({ type: 'input', task, ...body }));
    },
    close() {
      wanted = null;
      ws?.close();
      ws = null;
    },
  };
}

export interface HerBrowser {
  section(): Promise<void>;
  view(task?: string): Promise<void>;
  refresh(): Promise<void>;
  summary(): string;
}

export function createHerBrowser(sectionHost: Host, viewHost: Host, api: Api, pillParent: HTMLElement): HerBrowser {
  let state: BrowserState | null = null;
  let viewing: string | null = null;
  let watch: Watch | null = null;
  let page = { width: 1280, height: 800 };
  let message = '';
  let poll: ReturnType<typeof setInterval> | null = null;
  const picture = el('img', 'her-browser-picture');
  picture.alt = 'Her browser, live';
  picture.tabIndex = 0;
  const thumb = el('img', 'her-browser-thumb');
  thumb.alt = '';
  const pill = el('button', 'her-browser-pill');
  pill.type = 'button';
  pill.hidden = true;
  const pillText = el('span', 'her-browser-pill-text');
  pill.append(thumb, pillText);
  pill.addEventListener('click', () => void view(activeTask(state)?.id));
  pillParent.append(pill);
  const status = el('p', 'her-browser-status');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');

  const call = async (method: 'GET' | 'POST' | 'PUT' | 'DELETE', path: string, body?: unknown): Promise<boolean> => {
    try {
      // Every change carries a JSON body, as the server asks of anything but a GET.
      const r = await api(method, path, method === 'GET' ? undefined : (body ?? {}));
      if (!r.ok) {
        message = await reason(r);
        return false;
      }
      const got = (await r.json()) as Partial<BrowserState>;
      if (got && typeof got === 'object' && 'tasks' in got) state = got as BrowserState;
      message = '';
      return true;
    } catch {
      message = 'Her browser isn’t answering just now. Try again in a moment.';
      return false;
    }
  };

  const load = async (): Promise<void> => {
    await call('GET', '/api/browser');
  };

  const drawPill = (): void => {
    const task = activeTask(state);
    pill.hidden = task === null || viewHost.open;
    if (!task) return;
    pill.classList.toggle('needs-you', task.state === 'needs_you');
    pillText.textContent = pillWords(task);
    thumb.hidden = !(state?.settings.live ?? true) || !thumb.src;
  };

  const target = (): TaskView | null => {
    if (!state || !viewing) return null;
    if (viewing === 'free') return state.free;
    return state.tasks.find((t) => t.id === viewing) ?? null;
  };

  const drawView = async (): Promise<void> => {
    const t = target();
    const wrap = el('div', 'her-browser-view');
    const head = el('div', 'her-browser-head');
    const title = el('h2', '', 'Her browser');
    title.id = 'her-browser-title';
    viewHost.setAttribute('aria-labelledby', title.id);
    head.append(title);
    const where = el('div', 'her-browser-where');
    if (viewing === 'free' && t) {
      const form = el('form', 'her-browser-address');
      const input = el('input');
      input.type = 'text';
      input.placeholder = 'Type an address, such as example.com';
      input.value = t.url ?? '';
      input.setAttribute('aria-label', 'Address');
      form.append(input, button('Go', 'approve', () => form.requestSubmit()));
      form.addEventListener('submit', (ev) => {
        ev.preventDefault();
        void call('POST', '/api/browser/free/goto', { url: input.value }).then(redraw);
      });
      where.append(form);
    } else {
      where.append(el('span', 'her-browser-url', t?.url ?? 'Waiting for a page…'));
    }
    const line = el(
      'p',
      'her-browser-line',
      !t
        ? 'Nothing is running in her browser now.'
        : viewing === 'free'
          ? 'You have control. Sign in to a site, then choose “Keep me signed in here”.'
          : t.in_control
            ? 'You have control. She sees nothing and nothing is recorded until you click “I’m done”.'
            : t.state === 'needs_you'
              ? 'She’s waiting for you.'
              : `Watching her work${t.mode === 'act' ? ' (she may fill in a form; you approve anything it sends)' : ''}.`,
    );
    const frame = el('div', 'her-browser-frame');
    frame.classList.toggle('in-control', Boolean(t?.in_control) || viewing === 'free');
    frame.append(picture);
    const ask = el('div', 'her-browser-ask');
    if (t?.ask && viewing !== 'free') await drawAsk(ask, t, t.ask);
    const actions = el('div', 'settings-actions');
    if (t && viewing !== 'free') {
      if (t.in_control) actions.append(button('I’m done', 'approve', () => void call('POST', `/api/browser/tasks/${t.id}/control`, { take: false }).then(redraw)));
      else actions.append(button('Take control', '', () => void call('POST', `/api/browser/tasks/${t.id}/control`, { take: true }).then(redraw)));
      if (t.in_control) actions.append(button('Keep me signed in here', '', () => void keep(t.id)));
      actions.append(button('Stop', 'danger', () => void call('POST', `/api/browser/tasks/${t.id}/stop`).then(redraw)));
    }
    if (viewing === 'free' && t) {
      actions.append(button('Keep me signed in here', 'approve', () => void keep('free')));
      actions.append(button('Close her browser', '', () => void call('DELETE', '/api/browser/free').then(() => viewHost.close())));
    }
    actions.append(button('Close', 'settings-close', () => viewHost.close()));
    status.textContent = message;
    wrap.append(head, where, line, frame, ask, status, actions);
    viewHost.replaceChildren(wrap);
  };

  const keep = async (id: string): Promise<void> => {
    const path = id === 'free' ? '/api/browser/tasks/free/keep' : `/api/browser/tasks/${id}/keep`;
    try {
      const r = await api('POST', path, {});
      if (!r.ok) message = await reason(r);
      else {
        const body = (await r.json()) as { site?: string } & BrowserState;
        state = body;
        message = body.site ? `She’ll stay signed in to ${body.site}.` : '';
      }
    } catch {
      message = 'That didn’t work. Try again in a moment.';
    }
    await redraw();
  };

  const drawAsk = async (into: HTMLElement, t: TaskView, ask: Ask): Promise<void> => {
    into.append(el('p', 'her-browser-ask-text', ask.text));
    if (ask.kind === 'card' && ask.pending_id) {
      const pid = ask.pending_id;
      try {
        const r = await api('GET', `/api/browser/tasks/${t.id}/card`);
        if (r.ok) {
          const card = (await r.json()) as Card;
          into.append(el('pre', 'her-browser-card-detail', card.detail));
          // On the screen now: only from here can a tap approve it.
          await api('POST', `/api/browser/tasks/${t.id}/cards/${pid}/shown`, {});
        }
      } catch {
        into.append(el('p', 'settings-note', 'The card couldn’t be shown. Decline it, or try again.'));
      }
      into.append(
        button('Approve and send', 'approve', () => void call('POST', `/api/browser/tasks/${t.id}/cards/${pid}`, { decision: 'confirm' }).then(redraw)),
        button('Decline', '', () => void call('POST', `/api/browser/tasks/${t.id}/cards/${pid}`, { decision: 'cancel' }).then(redraw)),
      );
    } else if (ask.kind === 'step') {
      into.append(button('Take control', 'approve', () => void call('POST', `/api/browser/tasks/${t.id}/control`, { take: true }).then(redraw)));
    } else {
      into.append(
        button(ask.kind === 'signin' ? 'Use it' : 'Allow', 'approve', () => void call('POST', `/api/browser/tasks/${t.id}/asks/${ask.id}`, { answer: 'allowed' }).then(redraw)),
        button('Not now', '', () => void call('POST', `/api/browser/tasks/${t.id}/asks/${ask.id}`, { answer: 'declined' }).then(redraw)),
      );
    }
  };

  const redraw = async (): Promise<void> => {
    drawPill();
    if (viewHost.open) await drawView();
    if (sectionHost.open) drawSection();
  };

  const onFrame = (f: { data: string; width: number; height: number }): void => {
    page = { width: f.width, height: f.height };
    const src = `data:image/jpeg;base64,${f.data}`;
    if (viewHost.open) picture.src = src;
    if (state?.settings.live ?? true) thumb.src = src;
  };

  picture.addEventListener('click', (ev) => {
    const t = target();
    if (!watch || !viewing || !(t?.in_control || viewing === 'free')) return;
    const box = picture.getBoundingClientRect();
    watch.input(viewing, { kind: 'click', ...pagePoint(ev.clientX, ev.clientY, box, page) });
    picture.focus();
  });
  picture.addEventListener('keydown', (ev) => {
    const t = target();
    if (!watch || !viewing || !(t?.in_control || viewing === 'free')) return;
    const body = keyInput(ev);
    if (body) {
      ev.preventDefault();
      watch.input(viewing, body);
    }
  });
  picture.addEventListener('wheel', (ev) => {
    const t = target();
    if (!watch || !viewing || !(t?.in_control || viewing === 'free')) return;
    ev.preventDefault();
    watch.input(viewing, { kind: 'wheel', dy: ev.deltaY });
  });
  viewHost.addEventListener('close', () => {
    viewing = null;
    picture.removeAttribute('src');
    drawPill();
  });

  const tick = async (): Promise<void> => {
    await load();
    const task = activeTask(state);
    if (task && !watch) watch = connect(onFrame);
    if (watch) {
      const want = viewing ?? (state?.settings.live ? task?.id : null) ?? null;
      if (want) watch.watch(want);
    }
    if (!task && !viewing && watch) {
      watch.close();
      watch = null;
      thumb.removeAttribute('src');
    }
    await redraw();
  };

  const startPolling = (): void => {
    if (poll === null) poll = setInterval(() => void tick(), POLL_MS);
  };

  const drawSection = (): void => {
    const s = state;
    const form = el('form', 'settings her-browser-settings');
    form.addEventListener('submit', (ev) => ev.preventDefault());
    const title = el('h2', '', 'Her browser');
    title.id = 'her-browser-section-title';
    sectionHost.setAttribute('aria-labelledby', title.id);
    form.append(title, el('p', 'settings-note', s?.about ?? 'Her own browser, separate from yours.'));
    if (!s) {
      form.append(el('p', 'settings-note', message || 'Loading…'));
    } else if (!s.available) {
      form.append(el('p', 'settings-note', 'This install of Trillion doesn’t include her browser. Install the browser extra (uv sync --extra browser), then restart Trillion.'));
    } else if (!s.installed) {
      const get = button(s.installing ? 'Getting it…' : `Get her a browser of her own (about ${s.size_mb} MB)`, 'approve', () => {
        void call('POST', '/api/browser/install').then(redraw);
        startPolling();
      });
      get.disabled = s.installing;
      form.append(get, el('p', 'settings-note', 'This is never your browser. It is fetched once, now, and kept in Trillion’s own folder.'));
      if (s.install_error) form.append(el('p', 'settings-error', s.install_error));
    } else {
      form.append(el('p', 'settings-note', `Installed: Chromium ${s.version}.`));
      form.append(button('Open her browser', 'approve', () => void openFree()));
      const sites = el('div', 'her-browser-sites');
      sites.append(el('h3', '', 'Signed-in sites'));
      if (s.sites.length === 0) sites.append(el('p', 'settings-note', 'None yet. Open her browser, sign in to a site, and choose “Keep me signed in here”.'));
      for (const site of s.sites) {
        const row = el('div', 'her-browser-site');
        row.append(el('span', '', site), button('Remove', '', () => void call('DELETE', `/api/browser/sites/${encodeURIComponent(site)}`).then(redraw)));
        sites.append(row);
      }
      form.append(sites);
      const toggle = (key: 'act' | 'stronger' | 'live', label: string, note: string): HTMLElement => {
        const wrap = el('label', 'her-browser-toggle');
        const box = el('input');
        box.type = 'checkbox';
        box.checked = s.settings[key];
        box.addEventListener('change', () => void call('PUT', '/api/browser/settings', { [key]: box.checked }).then(redraw));
        wrap.append(box, el('span', '', label), el('small', '', note));
        return wrap;
      };
      form.append(
        toggle('act', 'Act mode', 'Let a task fill in a form when you ask it to. You approve everything it sends, on a card.'),
        toggle('stronger', 'Retry on a stronger model', 'When a task fails twice, try it once more on a cloud model you set up.'),
        toggle('live', 'Show the live picture', 'A small live picture of her browser while a helper works.'),
      );
      const engine = el('label', 'her-browser-engine');
      const pick = el('select');
      for (const e of s.engines) {
        const opt = el('option', '', e.name);
        opt.value = e.id;
        opt.selected = e.id === s.settings.search;
        pick.append(opt);
      }
      pick.addEventListener('change', () => void call('PUT', '/api/browser/settings', { search: pick.value }).then(redraw));
      engine.append(el('span', '', 'Search engine'), pick);
      form.append(engine);
    }
    if (message) form.append(el('p', 'settings-error', message));
    const actions = el('div', 'settings-actions');
    actions.append(button('Close', 'settings-close', () => sectionHost.close()));
    form.append(actions);
    sectionHost.replaceChildren(form);
  };

  const openFree = async (): Promise<void> => {
    if (!(await call('POST', '/api/browser/free'))) {
      drawSection();
      return;
    }
    sectionHost.close();
    await view('free');
  };

  const view = async (task?: string): Promise<void> => {
    await load();
    viewing = task ?? activeTask(state)?.id ?? null;
    if (!watch) watch = connect(onFrame);
    if (viewing) watch.watch(viewing);
    await drawView();
    if (!viewHost.open) viewHost.showModal();
    drawPill();
    startPolling();
  };

  void load().then(() => {
    if (activeTask(state)) startPolling();
    drawPill();
  });

  return {
    async section() {
      await load();
      drawSection();
      if (!sectionHost.open) sectionHost.showModal();
    },
    view,
    async refresh() {
      await tick();
      if (activeTask(state)) startPolling();
    },
    summary() {
      if (!state) return '';
      if (!state.installed) return 'Not set up';
      return state.sites.length ? `Signed in to ${state.sites.length}` : 'Set up';
    },
  };
}

export const feature: Feature = {
  setup({ panel, api, on }) {
    const browser = createHerBrowser(panel.host('her-browser'), panel.host('her-browser-view'), api, document.body);
    panel.add({
      id: 'her-browser',
      label: 'Her browser',
      group: HER,
      icon: 'her-browser',
      after: 'mind',
      keywords: 'browser browse web chromium sign in act mode search engine helper',
      summary: () => browser.summary(),
      open: () => void browser.section(),
    });
    on((msg) => {
      // A browsing task changing is told as a helpers change.
      if (msg.type === 'helpers.changed') void browser.refresh();
    });
  },
};
