import { createGalaxy, type Theme } from '@hellotrillion/galaxy';
import '@hellotrillion/galaxy/tokens.css';
import './app.css';
import { type Api, createSettings } from './settings';
import { type Entry, INITIAL, STATE_TEXT, type ServerMsg, type View, expire, reduce, sent } from './view';

const PROTOCOL = 1;
const TOKEN_KEY = 'trillion-token';
const CONVERSATION_KEY = 'trillion-conversation';
const THEME_KEY = 'trillion-theme';
// In development the page comes from Vite and the core runs beside it.
const CORE = import.meta.env.DEV ? '127.0.0.1:7777' : location.host;
const WS_URL = `ws://${CORE}/ws`;
const API_URL = import.meta.env.DEV ? `http://${CORE}` : '';

const $ = <T extends HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing #${id}`);
  return el as T;
};

function store(key: string, value?: string | null): string | null {
  try {
    if (value === undefined) return sessionStorage.getItem(key);
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, value);
  } catch {
    // Storage can be blocked; the page then simply forgets on reload.
  }
  return value ?? null;
}

/** The token arrives in the URL fragment, which is never sent anywhere. Take it and hide it. */
function takeToken(): string | null {
  const match = /(?:^#|&)token=([\w-]+)/.exec(location.hash);
  if (match?.[1]) {
    store(TOKEN_KEY, match[1]);
    history.replaceState(null, '', location.pathname);
  }
  return store(TOKEN_KEY);
}

const canvas = $<HTMLCanvasElement>('galaxy');
const statusEl = $('status');
const modelEl = $<HTMLButtonElement>('model');
const bannerEl = $('banner');
const transcriptEl = $('transcript');
const form = $<HTMLFormElement>('ask');
const input = $<HTMLTextAreaElement>('input');
const sendBtn = $<HTMLButtonElement>('send');
const stopBtn = $<HTMLButtonElement>('stop');
const footEl = $('foot');

function savedTheme(): Theme {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === 'light' || saved === 'dark') return saved;
  } catch {
    // fall through to the system preference
  }
  return matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

let theme = savedTheme();
const galaxy = createGalaxy(canvas, { theme });

function applyTheme(next: Theme): void {
  theme = next;
  document.documentElement.dataset['theme'] = next;
  galaxy.setTheme(next);
  for (const b of document.querySelectorAll<HTMLButtonElement>('[data-theme-choice]')) {
    b.setAttribute('aria-checked', String(b.dataset['themeChoice'] === next));
  }
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch {
    // not remembered, still applied
  }
}
for (const b of document.querySelectorAll<HTMLButtonElement>('[data-theme-choice]')) {
  b.addEventListener('click', () => applyTheme(b.dataset['themeChoice'] === 'light' ? 'light' : 'dark'));
}
applyTheme(theme);

let view: View = INITIAL;
let socket: WebSocket | null = null;
let retryMs = 1000;
const shown = new Set<string>();

function send(body: Record<string, unknown>): void {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(body));
}

// ── Rendering ────────────────────────────────────────────────────────────

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls: string, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.className = cls;
  // Always textContent: nothing the model or a tool wrote is ever parsed as markup.
  if (text !== undefined) node.textContent = text;
  return node;
}

function minutesLeft(expiresAt: number): string {
  const mins = Math.max(1, Math.ceil((expiresAt - Date.now()) / 60_000));
  return `Waits ${mins} more minute${mins === 1 ? '' : 's'}`;
}

function renderEntry(e: Entry): HTMLElement {
  switch (e.kind) {
    case 'user':
      return el('p', 'msg user', e.text);
    case 'assistant': {
      const p = el('p', 'msg assistant', e.text);
      if (e.interrupted) p.append(el('span', 'mark', ' (stopped)'));
      return p;
    }
    case 'tool': {
      const label = e.outcome === null ? `${e.name} …` : `${e.name}: ${e.outcome.replaceAll('_', ' ')}`;
      return el('p', `chip${e.ok ? '' : ' bad'}`, label);
    }
    case 'notice':
      return el('p', 'notice', e.text);
    case 'card': {
      const card = el('section', `card ${e.status}`);
      card.setAttribute('aria-label', 'Needs your approval');
      card.append(el('h2', 'card-title', e.status === 'waiting' ? 'Needs your yes' : 'Request'));
      card.append(el('p', 'card-summary', e.summary));
      if (e.status !== 'waiting') {
        card.append(el('p', 'card-note', e.note));
        return card;
      }
      const row = el('div', 'card-actions');
      const approve = el('button', 'approve', 'Approve');
      approve.type = 'button';
      approve.addEventListener('click', () => send({ type: 'confirm', pending_id: e.pendingId }));
      const decline = el('button', 'decline', 'Decline');
      decline.type = 'button';
      decline.addEventListener('click', () => send({ type: 'cancel', pending_id: e.pendingId }));
      row.append(approve, decline);
      card.append(row);
      const hint = e.tapOnly ? 'This one can only be approved here. ' : 'Or just answer in the chat. ';
      card.append(el('p', 'card-note', hint + minutesLeft(e.expiresAt) + '.'));
      return card;
    }
  }
}

const LINK_TEXT: Readonly<Record<View['link'], string>> = {
  connecting: 'Connecting',
  ready: '',
  offline: 'Trillion is not running. Start it with: trillion run',
  unauthenticated: 'This page is not signed in. Open the link that "trillion run" printed.',
};

let rendered: readonly Entry[] = [];

function render(): void {
  const nearBottom = transcriptEl.scrollHeight - transcriptEl.scrollTop - transcriptEl.clientHeight < 80;
  // Streaming text touches only the last node; anything else rebuilds the list.
  const last = view.entries[view.entries.length - 1];
  const prev = rendered[rendered.length - 1];
  const onlyTextGrew =
    view.entries.length === rendered.length &&
    last?.kind === 'assistant' &&
    prev?.kind === 'assistant' &&
    last.interrupted === prev.interrupted &&
    view.entries.slice(0, -1).every((e, i) => e === rendered[i]);
  if (onlyTextGrew && transcriptEl.lastElementChild) {
    transcriptEl.lastElementChild.textContent = last.text;
  } else if (view.entries !== rendered) {
    transcriptEl.replaceChildren(...view.entries.map(renderEntry));
  }
  rendered = view.entries;
  if (nearBottom) transcriptEl.scrollTop = transcriptEl.scrollHeight;

  // The core records a card as delivered only once it is really on screen (SPN-17).
  for (const e of view.entries) {
    if (e.kind === 'card' && e.status === 'waiting' && !shown.has(e.pendingId)) {
      shown.add(e.pendingId);
      send({ type: 'confirm.shown', pending_id: e.pendingId });
    }
  }

  const ready = view.link === 'ready';
  const linkText = LINK_TEXT[view.link];
  statusEl.textContent = ready ? STATE_TEXT[view.orb] : view.link === 'connecting' ? 'Connecting' : 'Offline';
  modelEl.hidden = !ready;
  modelEl.textContent = view.model ?? 'Choose a model';
  modelEl.title = 'Model settings';
  galaxy.setState(ready ? view.orb : view.link === 'connecting' ? 'idle' : 'error');

  const noModel = ready && view.model === null;
  const banner =
    linkText ||
    view.banner ||
    (noModel ? 'Nobody is here to answer yet. Choose a model to get started (top right).' : '');
  bannerEl.hidden = !banner;
  bannerEl.textContent = banner;

  const canType = ready && !noModel;
  input.disabled = !canType;
  input.placeholder = canType ? `Message ${view.agentName}` : '';
  sendBtn.disabled = !canType || view.busy;
  sendBtn.hidden = view.busy;
  stopBtn.hidden = !view.busy;
  footEl.textContent = !ready
    ? ''
    : view.grantedRoots.length
      ? `Files: ${view.grantedRoots.join(', ')}. Every change asks first.`
      : 'No folders are shared, so file tools are off. Start with: trillion run --grant <a folder>';
}

function apply(next: View): void {
  if (next === view) return;
  const before = view;
  view = next;
  if (view.conversationId && view.conversationId !== before.conversationId) {
    store(CONVERSATION_KEY, view.conversationId);
  }
  render();
}

// ── Settings ─────────────────────────────────────────────────────────────

const api: Api = (method, path, body) =>
  fetch(API_URL + path, {
    method,
    headers: {
      authorization: `Bearer ${store(TOKEN_KEY) ?? ''}`,
      ...(body === undefined ? {} : { 'content-type': 'application/json' }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
const settings = createSettings($<HTMLDialogElement>('settings'), api);
modelEl.addEventListener('click', () => void settings.open());
let offered = false;

// ── Connection ───────────────────────────────────────────────────────────

function connect(): void {
  const token = takeToken();
  if (!token) {
    apply({ ...view, link: 'unauthenticated' });
    return;
  }
  const ws = new WebSocket(WS_URL);
  socket = ws;
  ws.addEventListener('open', () => {
    retryMs = 1000;
    send({ type: 'hello', token, protocol: PROTOCOL, client: 'web', resume: store(CONVERSATION_KEY) });
  });
  ws.addEventListener('message', (ev) => {
    let msg: ServerMsg;
    try {
      msg = JSON.parse(String(ev.data)) as ServerMsg;
    } catch {
      return;
    }
    if (typeof msg?.type !== 'string') return;
    if (msg.type === 'error' && msg['code'] === 'unauthenticated') store(TOKEN_KEY, null);
    if (msg.type === 'confirm.resolved' && msg['status'] === 'succeeded') galaxy.flare(0.6);
    apply(reduce(view, msg));
    // First run: with nobody to answer, the way to fix that opens by itself, once.
    if (msg.type === 'session' && view.model === null && !offered) {
      offered = true;
      void settings.open();
    }
  });
  ws.addEventListener('close', () => {
    if (socket !== ws) return;
    socket = null;
    if (view.link === 'unauthenticated') return;
    apply({ ...view, link: 'offline', busy: false, orb: 'idle' });
    setTimeout(connect, retryMs);
    retryMs = Math.min(retryMs * 2, 10_000);
  });
}

// ── Input ────────────────────────────────────────────────────────────────

function submit(): void {
  const text = input.value.trim();
  if (!text || view.busy || view.link !== 'ready') return;
  send({ type: 'turn.text', text });
  input.value = '';
  input.style.height = '';
  apply(sent(view, text));
}

form.addEventListener('submit', (ev) => {
  ev.preventDefault();
  submit();
});
input.addEventListener('keydown', (ev) => {
  if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) {
    ev.preventDefault();
    submit();
  }
});
input.addEventListener('input', () => {
  input.style.height = '';
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
});
stopBtn.addEventListener('click', () => send({ type: 'interrupt' }));
addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape' && view.busy) send({ type: 'interrupt' });
  if (ev.key === 'k' && (ev.metaKey || ev.ctrlKey)) {
    ev.preventDefault();
    input.focus();
  }
});
setInterval(() => apply(expire(view, Date.now())), 15_000);

// Pasting the launch link into a tab that is already open changes only the fragment.
addEventListener('hashchange', () => {
  if (view.link === 'unauthenticated' && takeToken()) {
    apply({ ...view, link: 'connecting' });
    connect();
  }
});

render();
connect();
