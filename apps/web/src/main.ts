import { createGalaxy, type Theme } from '@hellotrillion/galaxy';
import '@hellotrillion/galaxy/tokens.css';
import './app.css';
import { type Inline, parse } from './format';
import { createListeningSettings } from './listening';
import { createMic } from './mic';
import { type Api, createSettings } from './settings';
import { createSpeechSettings } from './speech';
import { createVoice } from './voice';
import {
  type Entry,
  INITIAL,
  STATE_TEXT,
  type ServerMsg,
  type View,
  expire,
  micFailed,
  reduce,
  sent,
  toolLabel,
  waiting,
} from './view';

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
const newBtn = $<HTMLButtonElement>('new');
const speechBtn = $<HTMLButtonElement>('speech');
const listeningBtn = $<HTMLButtonElement>('listening');
const micBtn = $<HTMLButtonElement>('mic');

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
const voice = createVoice({
  onActive: () => render(),
  onEnded: (turn, playedMs) => send({ type: 'playback.ended', turn_id: turn, played_ms: playedMs }),
  attach: (node) => galaxy.attachAnalyser(node, 'playback'),
});
let socket: WebSocket | null = null;
// Her ears. Frames go to the core as they are captured, as binary (VOI-1).
const mic = createMic({
  onFrame: (pcm) => {
    if (socket?.readyState === WebSocket.OPEN) socket.send(pcm);
  },
  onProblem: (problem) => {
    send({ type: 'interrupt' });
    apply(micFailed(view, problem));
  },
  attach: (node) => galaxy.attachAnalyser(node, 'mic'),
});
let retryMs = 1000;
const shown = new Set<string>();

function send(body: Record<string, unknown>): void {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(body));
}

// ── Rendering ────────────────────────────────────────────────────────────

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  // Always textContent: nothing the model or a tool wrote is ever parsed as markup.
  if (text !== undefined) node.textContent = text;
  return node;
}

const INLINE_TAG = { bold: 'strong', italic: 'em', code: 'code' } as const;

function spans(parts: readonly Inline[]): Node[] {
  return parts.map((part) =>
    part.kind === 'text' ? document.createTextNode(part.text) : el(INLINE_TAG[part.kind], '', part.text),
  );
}

/** Her reply as elements. The formatter yields plain values; text is only ever set as text. */
function formatted(text: string): HTMLElement[] {
  return parse(text).map((block) => {
    if (block.kind === 'code') return el('pre', '', block.text);
    if (block.kind === 'paragraph') {
      const p = el('p');
      p.append(...spans(block.inline));
      return p;
    }
    const list = el(block.ordered ? 'ol' : 'ul');
    for (const item of block.items) {
      const li = el('li');
      li.append(...spans(item));
      list.append(li);
    }
    return list;
  });
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
      const box = el('div', 'msg assistant');
      box.append(...formatted(e.text));
      if (e.interrupted) box.append(el('span', 'mark', '(stopped)'));
      return box;
    }
    case 'tool': {
      return el('p', `chip${e.ok ? '' : ' bad'}`, toolLabel(e.name, e.outcome, e.ok));
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
  replaced: 'This conversation is now open in another tab. Close this one, or start a new conversation.',
};

let rendered: readonly Entry[] = [];

// Shown under the conversation while she works and no text is arriving. The status
// in the top bar already announces it to screen readers, so this one stays silent.
const thinkingEl = el('p', 'thinking', 'Thinking');
thinkingEl.setAttribute('aria-hidden', 'true');
// The words she has made out so far, while the user is still speaking.
const hearingEl = el('p', 'msg user hearing');

function render(): void {
  const nearBottom = transcriptEl.scrollHeight - transcriptEl.scrollTop - transcriptEl.clientHeight < 80;
  // Streaming text touches only the last node; anything else rebuilds the list.
  const last = view.entries[view.entries.length - 1];
  const prev = rendered[rendered.length - 1];
  thinkingEl.remove();
  hearingEl.remove();
  const onlyTextGrew =
    view.entries.length === rendered.length &&
    last?.kind === 'assistant' &&
    prev?.kind === 'assistant' &&
    last.interrupted === prev.interrupted &&
    view.entries.slice(0, -1).every((e, i) => e === rendered[i]);
  if (onlyTextGrew && transcriptEl.lastElementChild) {
    transcriptEl.lastElementChild.replaceChildren(...formatted(last.text));
  } else if (view.entries !== rendered) {
    transcriptEl.replaceChildren(...view.entries.map(renderEntry));
  }
  rendered = view.entries;
  if (view.hearing) {
    hearingEl.textContent = view.hearing;
    transcriptEl.append(hearingEl);
  }
  if (waiting(view)) transcriptEl.append(thinkingEl);
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
  speechBtn.hidden = !ready;
  speechBtn.textContent = view.speech ? 'Voice: on' : 'Voice: off';
  speechBtn.title = 'Voice settings';
  listeningBtn.hidden = !ready;
  listeningBtn.textContent = view.listening ? 'Mic: on' : 'Mic: off';
  listeningBtn.title = 'Mic settings';
  newBtn.hidden = !ready || view.entries.length === 0;
  modelEl.textContent = view.model ?? 'Choose a model';
  modelEl.title = 'Model settings';
  // While her voice is sounding she is speaking, whatever the core has moved on to.
  galaxy.setState(!ready ? (view.link === 'connecting' ? 'idle' : 'error') : voice.active ? 'speaking' : view.orb);

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
  stopBtn.hidden = !(view.busy || voice.active || view.mic);
  micBtn.hidden = !canType || view.busy;
  micBtn.textContent = view.mic ? 'Done' : 'Speak';
  micBtn.title = !view.listening
    ? 'Choose how she hears you'
    : view.mic
      ? 'Send what you said'
      : 'Speak to her';
  micBtn.setAttribute('aria-pressed', String(view.mic));
  micBtn.classList.toggle('live', view.mic);
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
const speechSettings = createSpeechSettings($<HTMLDialogElement>('speech-settings'), api);
speechBtn.addEventListener('click', () => void speechSettings.open());
const listeningSettings = createListeningSettings($<HTMLDialogElement>('listening-settings'), api);
listeningBtn.addEventListener('click', () => void listeningSettings.open());
let offered = false;

newBtn.addEventListener('click', () => {
  // Forget which conversation this tab was in; the core starts a fresh one on reconnect.
  store(CONVERSATION_KEY, null);
  shown.clear();
  apply({ ...INITIAL, link: 'connecting' });
  const old = socket;
  socket = null;
  old?.close();
  connect();
  input.focus();
});

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
  ws.binaryType = 'arraybuffer';
  ws.addEventListener('message', (ev) => {
    if (ev.data instanceof ArrayBuffer) {
      voice.audio(ev.data);
      return;
    }
    let msg: ServerMsg;
    try {
      msg = JSON.parse(String(ev.data)) as ServerMsg;
    } catch {
      return;
    }
    if (typeof msg?.type !== 'string') return;
    if (msg.type === 'error' && msg['code'] === 'unauthenticated') store(TOKEN_KEY, null);
    if (msg.type === 'confirm.resolved' && msg['status'] === 'succeeded') galaxy.flare(0.6);
    if (msg.type === 'reply.sentence') voice.sentence(Number(msg['turn']), String(msg['text'] ?? ''));
    if (msg.type === 'audio.end') voice.end(Number(msg['turn']));
    if (msg.type === 'audio.stop' || msg.type === 'interrupted') voice.stop();
    // The core opens and closes the microphone; the page only ever follows (VOI-15d).
    if (msg.type === 'mic.start' && !mic.on) void mic.start();
    if (msg.type === 'mic.stop' || (msg.type === 'error' && msg['code'] === 'listening')) mic.stop();
    apply(reduce(view, msg));
    if (msg.type === 'session' || msg.type === 'speech') voice.configure(view.speech);
    // First run: with nobody to answer, the way to fix that opens by itself, once.
    if (msg.type === 'session' && view.model === null && !offered) {
      offered = true;
      void settings.open();
    }
  });
  ws.addEventListener('close', () => {
    if (socket !== ws) return;
    socket = null;
    voice.stop();
    mic.stop();
    if (view.link === 'unauthenticated' || view.link === 'replaced') return;
    apply({ ...view, link: 'offline', busy: false, orb: 'idle' });
    setTimeout(connect, retryMs);
    retryMs = Math.min(retryMs * 2, 10_000);
  });
}

// ── Input ────────────────────────────────────────────────────────────────

function submit(): void {
  const text = input.value.trim();
  if (!text || view.busy || view.link !== 'ready') return;
  voice.unlock(); // sending is a click or a key press, which is what lets a browser play sound
  voice.stop();
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
const interrupt = (): void => {
  voice.stop();
  mic.stop();
  send({ type: 'interrupt' });
};
stopBtn.addEventListener('click', interrupt);
micBtn.addEventListener('click', () => {
  if (!view.listening) {
    void listeningSettings.open();
  } else if (view.mic) {
    // Done speaking: what she heard so far is the message.
    send({ type: 'listen.stop' });
  } else if (!view.busy && view.link === 'ready') {
    voice.unlock(); // a click is what lets a browser play her answer
    voice.stop();
    send({ type: 'listen.start' });
    // Opened here, inside the click, so the browser's permission prompt belongs to it.
    void mic.start();
  }
});
addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape' && (view.busy || voice.active || view.mic)) interrupt();
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
