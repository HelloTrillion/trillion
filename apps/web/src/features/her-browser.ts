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
import { OPEN_HER_BROWSER } from '../notice';
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
  readonly settings: { act: boolean; stronger: boolean; search: string; live: boolean; open_any?: boolean };
  readonly engines: readonly { id: string; name: string }[];
  readonly sites: readonly string[];
  readonly tasks: readonly TaskView[];
  readonly free: TaskView | null;
  /** The Brave Search API: whether a key is saved, never the key. */
  readonly brave?: BraveState;
  /** A line to show after a change, such as a key saved at its plan's limit. */
  readonly note?: string;
}

export interface BraveState {
  readonly engine: string;
  readonly saved: boolean;
  readonly keychain: boolean;
  readonly key_link: string;
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
/** What the pill says. `openUrl` is the page the live picture shows, when known. */
export function pillWords(task: TaskView, openUrl?: string | null): string {
  if (task.state === 'needs_you') {
    const what = task.ask?.text ?? '';
    return what ? `Needs you · ${what.endsWith('?') ? what : `${what}?`}`.replace(/^Needs you · Needs you: /, 'Needs you · ') : 'Needs you';
  }
  if (task.in_control) return 'You have control';
  const where = hostOf(openUrl ?? null) ?? hostOf(task.url ?? null) ?? hostOf(task.origin ?? null);
  return where ? `Browsing ${where}` : 'Browsing';
}

function hostOf(url: string | null): string | null {
  if (!url) return null;
  try {
    return new URL(url).host || null;
  } catch {
    return null;
  }
}

/** A page coordinate for a click on the picture, drawn letterboxed (contain) in `box`. */
export function pagePoint(
  clientX: number,
  clientY: number,
  box: { left: number; top: number; width: number; height: number },
  page: { width: number; height: number },
): { x: number; y: number } {
  const scale = Math.min(box.width / Math.max(page.width, 1), box.height / Math.max(page.height, 1)) || 1;
  const left = box.left + (box.width - page.width * scale) / 2;
  const top = box.top + (box.height - page.height * scale) / 2;
  const x = (clientX - left) / scale;
  const y = (clientY - top) / scale;
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
  /** The overlay, for tests. */
  readonly overlay: HTMLElement;
}

/** The task or free browser the pill speaks for, and what it says. */
export function pillTarget(state: BrowserState | null, openUrl?: string | null): { id: string; words: string; needsYou: boolean } | null {
  const task = activeTask(state);
  if (task) return { id: task.id, words: pillWords(task, openUrl), needsYou: task.state === 'needs_you' };
  if (state?.free) return { id: 'free', words: 'Her browser is open', needsYou: false };
  return null;
}

/** What the title bar says of where it is and what is happening. */
const ENDED: Readonly<Record<string, string>> = {
  answered: 'Finished',
  declined: 'Declined: nothing was sent',
  expired: 'Ran out of time: nothing was sent',
  stopped: 'Stopped',
  cancelled: 'Stopped',
  time: 'Ran out of time',
  steps: 'Ran out of steps',
  failed: 'Couldn’t finish',
};

export function stateWords(t: TaskView | null, free: boolean): string {
  if (!t) return free ? 'Closed' : 'Nothing running';
  if (t.state === 'done') return ENDED[t.outcome ?? ''] ?? 'Finished';
  if (free) return 'You have control';
  if (t.in_control) return 'You have control';
  if (t.state === 'needs_you') return 'Needs you';
  if (t.state === 'queued') return 'Waiting its turn';
  return t.mode === 'act' ? 'Working (may fill in a form)' : 'Working';
}

export function createHerBrowser(sectionHost: Host, api: Api, root: HTMLElement): HerBrowser {
  let state: BrowserState | null = null;
  let viewing: string | null = null;
  let watch: Watch | null = null;
  let page = { width: 1280, height: 800 };
  let message = '';
  let installing = false;
  let poll: ReturnType<typeof setInterval> | null = null;
  let framed = false;
  // The last picture of each, kept in this page only, so opening the window shows it at
  // once (a still page sends no new frames). And the page it shows.
  const lastFrame = new Map<string, string>();
  const lastUrl = new Map<string, string>();
  let watching: string | null = null;
  // Each card's detail, fetched once, and whether it was reported on screen.
  const cards = new Map<string, Card>();
  const reported = new Set<string>();
  let drawing = 0;

  // The overlay: a window of its own above everything, in the top layer.
  const overlay = el('dialog', 'her-browser-overlay');
  overlay.setAttribute('aria-labelledby', 'her-browser-title');
  const win = el('div', 'her-browser-window');
  const bar = el('header', 'her-browser-titlebar');
  const title = el('h2', 'her-browser-title', 'Her browser');
  title.id = 'her-browser-title';
  const site = el('span', 'her-browser-site-name');
  const stateLine = el('span', 'her-browser-state');
  const barActions = el('div', 'her-browser-bar-actions');
  bar.append(title, site, stateLine, barActions);
  const address = el('div', 'her-browser-addressbar');
  const addressForm = el('form', 'her-browser-address');
  const addressInput = el('input');
  addressInput.type = 'text';
  addressInput.placeholder = 'Type an address, such as example.com';
  addressInput.setAttribute('aria-label', 'Address');
  const go = el('button', 'approve', 'Go');
  go.type = 'submit';
  addressForm.append(addressInput, go);
  addressForm.addEventListener('submit', (ev) => {
    ev.preventDefault();
    void call('POST', '/api/browser/free/goto', { url: addressInput.value }).then(redraw);
  });
  address.append(addressForm);
  const frame = el('div', 'her-browser-frame');
  const picture = el('img', 'her-browser-picture');
  picture.alt = 'Her browser, live';
  picture.tabIndex = 0;
  picture.hidden = true;
  const waiting = el('p', 'her-browser-waiting', 'Waiting for the first picture…');
  frame.append(picture, waiting);
  const foot = el('div', 'her-browser-foot');
  const ask = el('div', 'her-browser-ask');
  const status = el('p', 'her-browser-status');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  foot.append(ask, status);
  win.append(bar, address, frame, foot);
  overlay.append(win);
  root.append(overlay);

  const isOpen = (): boolean => overlay.isConnected && overlay.hasAttribute('open');
  const closeOverlay = (): void => {
    if (typeof (overlay as HTMLDialogElement).close === 'function') (overlay as HTMLDialogElement).close();
    else overlay.removeAttribute('open');
    onClosed();
  };
  // Esc closes the overlay only: Settings below it stays as it was. Closing never stops a task.
  overlay.addEventListener('cancel', (ev) => {
    ev.preventDefault();
    closeOverlay();
  });
  overlay.addEventListener('keydown', (ev) => ev.stopPropagation());
  // Esc while it's open is the overlay's, wherever the focus is: Settings and the
  // conversation below never see it.
  addEventListener(
    'keydown',
    (ev) => {
      if (ev.key !== 'Escape' || !isOpen()) return;
      ev.preventDefault();
      ev.stopImmediatePropagation();
      closeOverlay();
    },
    true,
  );

  // The pill: a working task, or her browser open, above the voice dock.
  const pill = el('button', 'her-browser-pill');
  pill.type = 'button';
  pill.hidden = true;
  const thumb = el('img', 'her-browser-thumb');
  thumb.alt = '';
  thumb.hidden = true;
  const pillText = el('span', 'her-browser-pill-text');
  pill.append(thumb, pillText);
  pill.addEventListener('click', () => void view(pillTarget(state)?.id));
  root.append(pill);

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

  // A refresh in the background never puts up an error: only what the user did can.
  const load = async (): Promise<void> => {
    const keep = message;
    await call('GET', '/api/browser');
    message = keep;
  };

  const drawPill = (): void => {
    const target = pillTarget(state);
    pill.hidden = target === null || isOpen();
    if (!target) return;
    pill.classList.toggle('needs-you', target.needsYou);
    const words = pillTarget(state, lastUrl.get(target.id))?.words ?? target.words;
    pillText.textContent = words;
    const where = target.id === 'free' ? state?.free : state?.tasks.find((t) => t.id === target.id);
    thumb.hidden = !framed || !where?.url || !(state?.settings.live ?? true);
  };

  const target = (): TaskView | null => {
    if (!state || !viewing) return null;
    if (viewing === 'free') return state.free;
    return state.tasks.find((t) => t.id === viewing) ?? null;
  };

  const control = (id: string, take: boolean) => () => void call('POST', `/api/browser/tasks/${id}/control`, { take }).then(redraw);

  const drawView = async (): Promise<void> => {
    const t = target();
    const free = viewing === 'free';
    site.textContent = t?.origin ? t.origin.replace(/^https?:\/\//, '') : '';
    stateLine.textContent = stateWords(t, free);
    barActions.replaceChildren();
    const live = Boolean(t) && t?.state !== 'done';
    if (t && !free && live) {
      if (t.in_control) barActions.append(button('I’m done', 'approve', control(t.id, false)));
      else barActions.append(button('Take control', '', control(t.id, true)));
      if (t.in_control) barActions.append(button('Keep me signed in here', '', () => void keep(t.id)));
      barActions.append(button('Stop', '', () => void call('POST', `/api/browser/tasks/${t.id}/stop`).then(redraw)));
    }
    if (free && t) {
      barActions.append(button('Keep me signed in here', 'approve', () => void keep('free')));
      barActions.append(button('Close her browser', '', () => void call('DELETE', '/api/browser/free').then(closeOverlay)));
    }
    const x = button('×', 'her-browser-x', closeOverlay);
    x.setAttribute('aria-label', 'Close');
    x.title = 'Close (Esc). A task keeps working.';
    barActions.append(x);
    address.hidden = !(free && t);
    // Kept, not redrawn: what the user is typing, and where the focus is, stay put.
    if (free && t && document.activeElement !== addressInput) addressInput.value = t.url ?? '';
    frame.classList.toggle('in-control', Boolean(t?.in_control) || free);
    // Until it has opened a page there is nothing to see but a blank one: said in words.
    const shown = framed && Boolean(t?.url) && t?.state !== 'done';
    waiting.textContent = !t
      ? 'Nothing is running in her browser now.'
      : t.state === 'done'
        ? 'This task has ended.'
        : t.url
          ? 'Waiting for the first picture…'
          : free
            ? 'Type an address to open a page.'
            : 'Starting…';
    waiting.hidden = shown;
    picture.hidden = !shown;
    // Built aside and put in whole, by the newest draw only: a draw that overlaps
    // another never shows a card twice.
    const mine = ++drawing;
    const fresh = el('div');
    if (t?.ask && !free) await drawAsk(fresh, t, t.ask);
    else if (t && !free && t.in_control) fresh.append(el('p', 'her-browser-note', 'She sees nothing and nothing is recorded until you click “I’m done”.'));
    else if (free && t) fresh.append(el('p', 'her-browser-note', 'Sign in to a site, then choose “Keep me signed in here”. Only that site’s sign-in is kept, never your password.'));
    if (mine !== drawing) return;
    ask.replaceChildren(...fresh.childNodes);
    status.textContent = message;
  };

  const keep = async (id: string): Promise<void> => {
    const path = `/api/browser/tasks/${id}/keep`;
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

  const drawAsk = async (into: HTMLElement, t: TaskView, a: Ask): Promise<void> => {
    into.append(el('p', 'her-browser-ask-text', a.text));
    if (a.kind === 'card' && a.pending_id) {
      const pid = a.pending_id;
      try {
        let card = cards.get(pid);
        if (!card) {
          const r = await api('GET', `/api/browser/tasks/${t.id}/card`);
          if (r.ok) {
            card = (await r.json()) as Card;
            cards.set(pid, card);
          }
        }
        if (card) {
          into.append(el('pre', 'her-browser-card-detail', card.detail));
          if (!reported.has(pid)) {
            // On the screen now: only from here can a tap approve it.
            reported.add(pid);
            await api('POST', `/api/browser/tasks/${t.id}/cards/${pid}/shown`, {});
          }
        }
      } catch {
        into.append(el('p', 'her-browser-note', 'The card couldn’t be shown. Decline it, or try again.'));
      }
      into.append(
        button('Approve and send', 'approve', () => void call('POST', `/api/browser/tasks/${t.id}/cards/${pid}`, { decision: 'confirm' }).then(redraw)),
        button('Decline', '', () => void call('POST', `/api/browser/tasks/${t.id}/cards/${pid}`, { decision: 'cancel' }).then(redraw)),
      );
    } else if (a.kind === 'step') {
      into.append(button('Take control', 'approve', control(t.id, true)));
    } else {
      const answer = (word: 'allowed' | 'always' | 'declined') => () => void call('POST', `/api/browser/tasks/${t.id}/asks/${a.id}`, { answer: word }).then(redraw);
      into.append(button(a.kind === 'signin' ? 'Use it' : 'Allow', 'approve', answer('allowed')));
      if (a.kind === 'chip') {
        // Once and done: this site now, and any public site from now on, without asking.
        const always = button('Always allow', '', answer('always'));
        always.title = 'Let her open any site without asking. Pages on this computer and your network stay refused, and nothing is sent without your OK.';
        into.append(always);
      }
      into.append(button('Not now', '', answer('declined')));
    }
  };

  // What the section shows, and what it showed when it was last drawn. A poll redraws
  // it only when this changed, and never while the user is in it: rebuilding it would
  // close an open select, or take the focus from a checkbox, before its change came.
  const sectionShows = (): string => {
    const s = state;
    const what = s && { a: s.available, i: s.installed, g: s.installing, e: s.install_error, v: s.version, m: s.size_mb, t: s.about, s: s.sites, o: s.settings, n: s.engines, b: s.brave };
    return JSON.stringify([what, message, installing]);
  };
  let sectionDrawn: string | null = null;
  let sectionForm: HTMLElement | null = null;
  // A Brave key being typed, kept in this page only until it is saved, so a redraw
  // never loses it. Never stored anywhere else, and cleared once it is saved.
  let braveDraft = '';

  /** The key goes from the field straight to the keychain; the page keeps nothing. */
  const braveKey = async (method: 'PUT' | 'DELETE', body: unknown): Promise<void> => {
    try {
      const r = await api(method, '/api/browser/brave-key', body);
      if (!r.ok) message = await reason(r);
      else {
        state = (await r.json()) as BrowserState;
        message = '';
        braveDraft = '';
        if (state.note) message = state.note;
      }
    } catch {
      message = 'That didn’t work. Try again in a moment.';
    }
    await redraw();
  };

  const drawBrave = (s: BrowserState, b: BraveState): HTMLElement => {
    const box = el('div', 'her-browser-brave');
    const chosen = s.settings.search === b.engine;
    if (!chosen && b.saved) {
      // Not in use: only that one is kept, and a way to remove it.
      const row = el('div', 'her-browser-site');
      row.append(el('span', '', 'A Brave Search API key is saved in your keychain.'), button('Remove', '', () => void braveKey('DELETE', {})));
      box.append(row);
      return box;
    }
    box.append(el('h3', '', 'Brave Search API key'));
    const field = el('label', 'her-browser-brave-field');
    const input = el('input');
    input.type = 'password';
    input.id = 'her-browser-brave-key';
    input.autocomplete = 'off';
    input.spellcheck = false;
    input.placeholder = b.saved ? 'Saved. Type a new key to replace it' : 'Paste your key';
    input.value = braveDraft;
    input.addEventListener('input', () => {
      braveDraft = input.value;
    });
    field.append(el('span', '', 'Key'), input);
    const save = button('Save', 'approve', () => {
      if (input.value.trim()) void braveKey('PUT', { key: input.value });
    });
    const actions = el('div', 'her-browser-brave-actions');
    actions.append(save);
    if (b.saved) actions.append(button('Remove', '', () => void braveKey('DELETE', {})));
    const link = el('a', '', 'Get a key from Brave');
    link.href = b.key_link;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    actions.append(link);
    box.append(field, actions);
    box.append(
      el(
        'p',
        'settings-note',
        `${b.saved ? 'A key is saved in your keychain. ' : chosen ? 'No key yet: searches use Bing until you save one. ' : ''}Searches go to Brave with your key, from Trillion itself, never from her browser. The free plan has a monthly limit; when it runs out, she searches with Bing.`,
      ),
    );
    if (!b.keychain) box.append(el('p', 'settings-error', 'This computer has no keychain to keep a key in.'));
    return box;
  };

  /** After what the user did: everything is drawn again. */
  const redraw = async (): Promise<void> => {
    drawPill();
    if (isOpen()) await drawView();
    if (sectionHost.open) drawSection();
  };

  /** After a poll: the section only when what it shows changed, and the user isn't in it. */
  const redrawPolled = async (): Promise<void> => {
    drawPill();
    if (isOpen()) await drawView();
    if (!sectionHost.open || sectionShows() === sectionDrawn) return;
    const active = document.activeElement;
    if (sectionForm && active && active !== document.body && sectionForm.contains(active)) return;
    drawSection();
  };

  const onFrame = (f: { data: string; width: number; height: number; url: string | null }): void => {
    page = { width: f.width, height: f.height };
    const src = `data:image/jpeg;base64,${f.data}`;
    framed = true;
    if (watching) {
      lastFrame.set(watching, src);
      if (f.url) lastUrl.set(watching, f.url);
    }
    if (isOpen()) {
      picture.src = src;
      const shown = Boolean(target()?.url);
      picture.hidden = !shown;
      waiting.hidden = shown;
    }
    if (state?.settings.live ?? true) thumb.src = src;
    drawPill();
  };

  const canInput = (): boolean => {
    const t = target();
    return Boolean(watch && viewing && (t?.in_control || viewing === 'free'));
  };
  picture.addEventListener('click', (ev) => {
    if (!canInput() || !watch || !viewing) return;
    watch.input(viewing, { kind: 'click', ...pagePoint(ev.clientX, ev.clientY, picture.getBoundingClientRect(), page) });
    picture.focus();
  });
  picture.addEventListener('keydown', (ev) => {
    if (!canInput() || !watch || !viewing) return;
    const body = keyInput(ev);
    if (body) {
      ev.preventDefault();
      ev.stopPropagation();
      watch.input(viewing, body);
    }
  });
  picture.addEventListener('wheel', (ev) => {
    if (!canInput() || !watch || !viewing) return;
    ev.preventDefault();
    watch.input(viewing, { kind: 'wheel', dy: ev.deltaY });
  });

  function onClosed(): void {
    viewing = null;
    picture.removeAttribute('src');
    picture.hidden = true;
    drawPill();
  }

  const tick = async (): Promise<void> => {
    await load();
    const target = pillTarget(state);
    if (target && !watch) watch = connect(onFrame);
    if (watch) {
      const want = viewing ?? (state?.settings.live ? target?.id : null) ?? null;
      if (want && want !== watching) {
        watching = want;
        watch.watch(want);
      }
    }
    if (!target && !viewing && watch) {
      watch.close();
      watch = null;
      watching = null;
      framed = false;
      thumb.removeAttribute('src');
    }
    if (installing && state && (state.installed || state.install_error || !state.installing)) installing = false;
    await redrawPolled();
  };

  const startPolling = (): void => {
    if (poll === null) poll = setInterval(() => void tick(), POLL_MS);
  };

  const drawSection = (): void => {
    const s = state;
    const form = el('form', 'settings her-browser-settings');
    form.addEventListener('submit', (ev) => ev.preventDefault());
    const head = el('h2', '', 'Her browser');
    head.id = 'her-browser-section-title';
    sectionHost.setAttribute('aria-labelledby', head.id);
    form.append(head, el('p', 'settings-note', s?.about ?? 'Her own browser, separate from yours.'));
    if (!s) {
      form.append(el('p', 'settings-note', message || 'Loading…'));
    } else if (!s.available) {
      form.append(el('p', 'settings-note', 'This install of Trillion doesn’t include her browser. Install the browser extra (uv sync --extra browser), then restart Trillion.'));
    } else if (!s.installed) {
      if (installing || s.installing) {
        const busy = el('p', 'her-browser-installing');
        busy.setAttribute('role', 'status');
        busy.append(el('span', 'her-browser-spinner'), el('span', '', `Getting her browser… (about ${s.size_mb} MB, a minute or two)`));
        form.append(busy);
      } else {
        form.append(
          button(`Get her a browser of her own (about ${s.size_mb} MB)`, 'approve', () => {
            installing = true;
            message = '';
            drawSection();
            void call('POST', '/api/browser/install').then(() => {
              if (message) installing = false;
              return redraw();
            });
            startPolling();
          }),
        );
      }
      form.append(el('p', 'settings-note', 'This is never your browser. It is fetched once, now, and kept in Trillion’s own folder.'));
      if (s.install_error && !installing) form.append(el('p', 'settings-error', `It didn’t finish: ${s.install_error}. Try again in a moment.`));
    } else {
      form.append(el('p', 'settings-note', `Installed: Chromium ${s.version}.`));
      form.append(button('Open her browser', 'approve', () => void openFree()));
      const sites = el('div', 'her-browser-sites');
      sites.append(el('h3', '', 'Signed-in sites'));
      if (s.sites.length === 0) sites.append(el('p', 'settings-note', 'None yet. Open her browser, sign in to a site, and choose “Keep me signed in here”.'));
      for (const name of s.sites) {
        const row = el('div', 'her-browser-site');
        row.append(el('span', '', name), button('Remove', '', () => void call('DELETE', `/api/browser/sites/${encodeURIComponent(name)}`).then(redraw)));
        sites.append(row);
      }
      form.append(sites);
      const toggle = (key: 'act' | 'stronger' | 'live' | 'open_any', label: string, note: string): HTMLElement => {
        const wrap = el('label', 'her-browser-toggle');
        const box = el('input');
        box.type = 'checkbox';
        box.checked = s.settings[key] === true;
        box.addEventListener('change', () => void call('PUT', '/api/browser/settings', { [key]: box.checked }).then(redraw));
        wrap.append(box, el('span', '', label), el('small', '', note));
        return wrap;
      };
      form.append(
        toggle('act', 'Act mode', 'Let a task fill in a form when you ask it to. You approve everything it sends, on a card.'),
        toggle('stronger', 'Retry on a stronger model', 'When a task fails twice, try it once more on a cloud model you set up.'),
        toggle('open_any', 'Let her open any site without asking', 'No “Open this site?” question. Pages on this computer and your network stay refused, and nothing is sent without your OK.'),
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
      if (s.brave) form.append(drawBrave(s, s.brave));
    }
    if (message) form.append(el('p', 'settings-error', message));
    const actions = el('div', 'settings-actions');
    actions.append(button('Close', 'settings-close', () => sectionHost.close()));
    form.append(actions);
    sectionHost.replaceChildren(form);
    sectionForm = form;
    sectionDrawn = sectionShows();
  };

  const openFree = async (): Promise<void> => {
    if (!(await call('POST', '/api/browser/free'))) {
      drawSection();
      return;
    }
    await view('free');
  };

  const view = async (task?: string): Promise<void> => {
    await load();
    viewing = task ?? pillTarget(state)?.id ?? null;
    const kept = viewing ? lastFrame.get(viewing) : undefined;
    framed = kept !== undefined;
    if (kept) picture.src = kept;
    if (!watch) watch = connect(onFrame);
    if (viewing) {
      watching = viewing;
      watch.watch(viewing);
    }
    if (!isOpen()) {
      if (typeof (overlay as HTMLDialogElement).showModal === 'function') (overlay as HTMLDialogElement).showModal();
      else overlay.setAttribute('open', '');
    }
    await drawView();
    drawPill();
    startPolling();
  };

  void load().then(() => {
    if (pillTarget(state)) startPolling();
    drawPill();
  });

  return {
    overlay,
    async section() {
      // Shown at once, then filled in: never shown late, after the user moved on to
      // another section, which left the tab bar and the body disagreeing.
      drawSection();
      if (!sectionHost.open) sectionHost.showModal();
      await load();
      if (sectionHost.open) drawSection();
    },
    view,
    async refresh() {
      await tick();
      if (pillTarget(state)) startPolling();
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
    const browser = createHerBrowser(panel.host('her-browser'), api, document.body);
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
      // A browsing task changing is told as a helpers change. An ask answered in her
      // conversation shows here at once, not at the next poll.
      if (msg.type === 'helpers.changed' || msg.type === 'browser.ask.resolved') void browser.refresh();
    });
    // View, on a step's line in her conversation: the window, at that task.
    addEventListener(OPEN_HER_BROWSER, (ev) => {
      const task = (ev as CustomEvent<unknown>).detail;
      void browser.view(typeof task === 'string' && task ? task : undefined);
    });
  },
};
