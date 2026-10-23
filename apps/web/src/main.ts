import { createGalaxy, type Theme } from '@hellotrillion/galaxy';
import '@hellotrillion/galaxy/tokens.css';
import './app.css';
import { type Inline, parse } from './format';
import { createAbout, createAppearance, createFolders, createShortcuts, createUsageSettings } from './browser';
import { TalkKeys, type TalkPrefs } from './hotkey';
import { icon } from './icons';
import { createConnected } from './connected';
import { createListeningSettings } from './listening';
import { createMic } from './mic';
import { BargeDetector, Preroll, frameLevel } from './barge';
import { createMicrophones } from './microphone';
import { createNamesSettings } from './names';
import {
  APPROVE,
  DECLINE,
  OPEN,
  type Onboarding,
  type Profile,
  type Ready,
  active,
  answer,
  begin,
  choices,
  configured,
  decide,
  failed,
  placeholder,
  saved,
} from './onboarding';
import { createPanel } from './panel';
import { primary, shortcut } from './platform';
import {
  type Theme as ThemeChoice,
  saveBarge,
  saveMic,
  saveTalk,
  saveTheme,
  savedBarge,
  savedMic,
  savedTalk,
  savedTheme,
  shownTheme,
} from './prefs';
import { type Api, createSettings } from './settings';
import { createSkills } from './skills';
import { createSpeechSettings } from './speech';
import { type Usage, fetchUsage, pill, sentence } from './usage';
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

/** A link back from signing in names the connection to show: #show=google:gmail.
 * A skill's id is a path (#show=skill:folder:/a/b), so `/` is allowed; it is only
 * ever compared, never fetched. Taken once, and the address bar cleaned, like the token. */
function takeShow(): string | null {
  const match = /(?:^#|&)show=([\w:./%-]+)/.exec(location.hash);
  if (!match?.[1]) return null;
  history.replaceState(null, '', location.pathname);
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return null;
  }
}
const showOnStart = takeShow();

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
const bannerEl = $('banner');
const transcriptEl = $('transcript');
const form = $<HTMLFormElement>('ask');
const input = $<HTMLTextAreaElement>('input');
const sendBtn = $<HTMLButtonElement>('send');
const stopBtn = $<HTMLButtonElement>('stop');
const footEl = $('foot');
const newBtn = $<HTMLButtonElement>('new');
const usageBtn = $<HTMLButtonElement>('usage');
let usage: Usage | null = null;
const micBtn = $<HTMLButtonElement>('mic');
const settingsBtn = $<HTMLButtonElement>('open-settings');
settingsBtn.replaceChildren(icon('menu', 20));
const choicesEl = $('choices');

const systemLight = matchMedia('(prefers-color-scheme: light)');
let themeChoice: ThemeChoice = savedTheme();
let talkPrefs: TalkPrefs = savedTalk();
const talkKeys = new TalkKeys(talkPrefs);
let version = '';
const galaxy = createGalaxy(canvas, { theme: shownTheme(themeChoice, systemLight.matches) });

function applyTheme(choice: ThemeChoice): void {
  themeChoice = choice;
  const shown: Theme = shownTheme(choice, systemLight.matches);
  document.documentElement.dataset['theme'] = shown;
  galaxy.setTheme(shown);
  saveTheme(choice);
}
applyTheme(themeChoice);
// Following the computer means following it when it changes, at sunset for instance.
systemLight.addEventListener('change', () => {
  if (themeChoice === 'system') applyTheme('system');
});

let view: View = INITIAL;
// Voice barge-in (VOI-9). While she answers a spoken question, the microphone is open
// here and "watching": frames stay on this page, measured against her own level, and
// nothing is sent. When the user talks over her she stops at once, the core is told,
// and the frames from just before are held until it is listening, then sent first.
let spokeLast = false;
let bargeOn = savedBarge();
let micMode: 'send' | 'watch' = 'send';
let bargeHeld: ArrayBuffer[] | null = null;
const detector = new BargeDetector();
const preroll = new Preroll();

function canWatch(): boolean {
  return (
    bargeOn &&
    spokeLast &&
    view.link === 'ready' &&
    view.listening !== null &&
    view.speech?.where === 'core' &&
    !view.mic &&
    !active(ob)
  );
}

function startWatching(): void {
  if (mic.on || !canWatch()) return;
  micMode = 'watch';
  bargeHeld = null;
  detector.reset();
  preroll.drain();
  void mic.start();
}

/** Close a watching microphone, unless the user has just cut in. */
function stopWatching(): void {
  if (micMode !== 'watch' || bargeHeld) return;
  mic.stop();
  micMode = 'send';
}

function watchFrame(pcm: ArrayBuffer): void {
  if (bargeHeld) {
    bargeHeld.push(pcm); // the user is talking: keep every word until she listens
    return;
  }
  preroll.push(pcm);
  if (detector.push(frameLevel(pcm), voice.level() ?? 0)) {
    bargeHeld = preroll.drain();
    voice.stop();
    send({ type: 'barge' });
  }
}

const voice = createVoice({
  onActive: (on) => {
    if (on) startWatching();
    render();
  },
  onEnded: (turn, playedMs) => {
    send({ type: 'playback.ended', turn_id: turn, played_ms: playedMs });
    // After a spoken turn the core opens the microphone for a follow-up, and the
    // watching one simply carries on as it. Otherwise it closes.
    setTimeout(() => {
      if (micMode === 'watch' && !view.mic) stopWatching();
    }, 1500);
  },
  onAudible: (turn, inMs) => send({ type: 'playback.started', turn_id: turn, in_ms: inMs }),
  attach: (node) => galaxy.attachAnalyser(node, 'playback'),
});
let socket: WebSocket | null = null;
// Her ears. Frames go to the core as they are captured, as binary (VOI-1).
const mic = createMic({
  onFrame: (pcm) => {
    if (micMode === 'watch') watchFrame(pcm);
    else if (socket?.readyState === WebSocket.OPEN) socket.send(pcm);
  },
  onSetup: (setup) => send({ type: 'mic.opened', ...setup }),
  device: savedMic,
  onProblem: (problem) => {
    if (micMode === 'watch' && !bargeHeld) {
      // Only watching for a barge-in: she carries on, and nothing needs saying.
      micMode = 'send';
      return;
    }
    micMode = 'send';
    bargeHeld = null;
    send({ type: 'interrupt' });
    apply(micFailed(view, problem));
  },
  attach: (node) => galaxy.attachAnalyser(node, 'mic'),
});
let retryMs = 1000;
const shown = new Set<string>();

// The first conversation, or a later change of names (ONB-3). Its lines are the
// page's own, so they sit in the transcript where it began and survive a reconnect.
// The card she shows as an example. It never reaches the core: nothing is pending.
const EXAMPLE_CARD = 'example';
let ob: Onboarding | null = null;
let greeted = false;
let obAt = 0;
let obDue = false;
let merged: { from: readonly Entry[]; ob: Onboarding | null; entries: readonly Entry[] } | null = null;

function entries(): readonly Entry[] {
  if (!ob) return view.entries;
  if (merged?.from === view.entries && merged.ob === ob) return merged.entries;
  const at = Math.min(obAt, view.entries.length);
  const lines: Entry[] = ob.lines.map((l) =>
    l.who === 'card'
      ? {
          kind: 'card',
          pendingId: EXAMPLE_CARD,
          summary: l.text,
          expiresAt: 0,
          tapOnly: false,
          status: l.status,
          note: l.status === 'done' ? 'Approved. Nothing happened: this was an example.' : 'Declined. Nothing happened.',
        }
      : l.who === 'you'
        ? { kind: 'user', text: l.text }
        : { kind: 'assistant', text: l.text, interrupted: false },
  );
  merged = { from: view.entries, ob, entries: [...view.entries.slice(0, at), ...lines, ...view.entries.slice(at)] };
  return merged.entries;
}

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
      const example = e.pendingId === EXAMPLE_CARD;
      const card = el('section', `card ${e.status}`);
      card.setAttribute('aria-label', example ? 'An example of an approval card' : 'Needs your approval');
      card.append(el('h2', 'card-title', example ? 'Example' : e.status === 'waiting' ? 'Needs your yes' : 'Request'));
      card.append(el('p', 'card-summary', e.summary));
      if (e.status !== 'waiting') {
        card.append(el('p', 'card-note', e.note));
        return card;
      }
      const row = el('div', 'card-actions');
      const approve = el('button', 'approve', 'Approve');
      approve.type = 'button';
      approve.addEventListener('click', () =>
        example ? reply(APPROVE) : send({ type: 'confirm', pending_id: e.pendingId }),
      );
      const decline = el('button', 'decline', 'Decline');
      decline.type = 'button';
      decline.addEventListener('click', () =>
        example ? reply(DECLINE) : send({ type: 'cancel', pending_id: e.pendingId }),
      );
      row.append(approve, decline);
      card.append(row);
      const hint = e.tapOnly ? 'This one can only be approved here. ' : 'Or just answer in the chat. ';
      card.append(
        el('p', 'card-note', example ? 'Or just answer in the chat. Nothing happens either way.' : hint + minutesLeft(e.expiresAt) + '.'),
      );
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
  const all = entries();
  const last = all[all.length - 1];
  const prev = rendered[rendered.length - 1];
  thinkingEl.remove();
  hearingEl.remove();
  const onlyTextGrew =
    all.length === rendered.length &&
    last?.kind === 'assistant' &&
    prev?.kind === 'assistant' &&
    last.interrupted === prev.interrupted &&
    all.slice(0, -1).every((e, i) => e === rendered[i]);
  if (onlyTextGrew && transcriptEl.lastElementChild) {
    transcriptEl.lastElementChild.replaceChildren(...formatted(last.text));
  } else if (all !== rendered) {
    transcriptEl.replaceChildren(...all.map(renderEntry));
  }
  rendered = all;
  if (view.hearing) {
    hearingEl.textContent = view.hearing;
    transcriptEl.append(hearingEl);
  }
  if (waiting(view)) transcriptEl.append(thinkingEl);
  if (nearBottom) transcriptEl.scrollTop = transcriptEl.scrollHeight;

  // The core records a card as delivered only once it is really on screen (SPN-17).
  for (const e of view.entries) {
    if (e.kind === 'card' && e.status === 'waiting' && !shown.has(e.pendingId) && e.pendingId !== EXAMPLE_CARD) {
      shown.add(e.pendingId);
      send({ type: 'confirm.shown', pending_id: e.pendingId });
    }
  }

  const ready = view.link === 'ready';
  const linkText = LINK_TEXT[view.link];
  statusEl.textContent = ready ? STATE_TEXT[view.orb] : view.link === 'connecting' ? 'Connecting' : 'Offline';
  newBtn.hidden = !ready || view.entries.length === 0;
  const introducing = ready && active(ob);
  settingsBtn.hidden = !ready || introducing;
  settingsBtn.title = `Settings (${shortcut(',')})`;
  if (panel.isOpen) panel.refresh();
  const quick = introducing && ob ? choices(ob) : [];
  const quickKey = introducing && ob ? ob.step + quick.map((c) => c.label).join('|') : '';
  choicesEl.hidden = quick.length === 0;
  if (choicesEl.dataset['for'] !== quickKey) {
    choicesEl.dataset['for'] = quickKey;
    choicesEl.replaceChildren(
      ...quick.map((c) => {
        const b = el('button', 'choice', c.label);
        b.type = 'button';
        b.addEventListener('click', () => reply(c.value));
        return b;
      }),
    );
  }
  // While her voice is sounding she is speaking, whatever the core has moved on to.
  galaxy.setState(!ready ? (view.link === 'connecting' ? 'idle' : 'error') : voice.active ? 'speaking' : view.orb);

  const noModel = ready && view.model === null;
  const banner =
    linkText ||
    view.banner ||
    (noModel && !introducing ? 'Nobody is here to answer yet. Choose a model to get started (top right).' : '');
  bannerEl.hidden = !banner;
  bannerEl.textContent = banner;

  // Introductions need no model: the app writes the names, not her.
  const canType = ready && (!noModel || introducing);
  input.disabled = !canType;
  input.placeholder = introducing && ob ? placeholder(ob) : canType ? `Message ${view.agentName}` : '';
  sendBtn.disabled = !canType || view.busy;
  sendBtn.hidden = view.busy;
  stopBtn.hidden = !(view.busy || voice.active || view.mic);
  micBtn.hidden = !canType || view.busy || introducing;
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
// One panel for every setting (Settings, top right). The model, voice and microphone
// sections are the same modules that used to be separate dialogs.
const panel = createPanel($<HTMLDialogElement>('settings-panel'));
const settings = createSettings(panel.host('model'), api);
const speechSettings = createSpeechSettings(panel.host('voice'), api);
const listeningSettings = createListeningSettings(
  panel.host('mic'),
  api,
  createMicrophones(
    savedMic,
    (id) => {
      saveMic(id);
      panel.refresh();
    },
    {
      on: () => bargeOn,
      set: (on) => {
        bargeOn = on;
        saveBarge(on);
        if (!on) stopWatching();
      },
    },
  ),
);
const namesSettings = createNamesSettings(panel.host('names'), api, {
  talk: () => talkNames(),
  // The core tells every open page the new name itself; only the summary here changes.
  saved: () => panel.refresh(),
});
const shortcuts = createShortcuts(
  panel.host('shortcuts'),
  () => talkPrefs,
  (prefs) => {
    talkPrefs = prefs;
    talkKeys.use(prefs);
    saveTalk(prefs);
    panel.refresh();
  },
);
const appearance = createAppearance(
  panel.host('appearance'),
  () => themeChoice,
  (t) => {
    applyTheme(t);
    panel.refresh();
  },
);
const folders = createFolders(panel.host('folders'), () => view.grantedRoots);
const connected = createConnected(panel.host('connected'), api, () => panel.refresh());
const skills = createSkills(panel.host('skills'), api, () => panel.refresh());
const about = createAbout(panel.host('about'), () => ({ version, model: view.model ?? 'None chosen' }));
async function refreshUsage(): Promise<Usage | null> {
  usage = await fetchUsage(api);
  usageBtn.hidden = usage === null;
  if (usage) {
    usageBtn.textContent = pill(usage);
    usageBtn.title = sentence(usage);
    usageBtn.setAttribute('aria-label', `${pill(usage)}. ${sentence(usage)}`);
  }
  return usage;
}
const usageSettings = createUsageSettings(panel.host('usage'), refreshUsage);
const HER = 'Her';
const YOU = 'You';
const COMPUTER = 'This computer';
panel.add({ id: 'model', label: 'Model', group: HER, icon: 'model', keywords: 'brain provider key anthropic openai ollama', summary: () => view.model ?? 'None chosen', open: () => settings.open() });
panel.add({ id: 'voice', label: 'Voice', group: HER, icon: 'voice', keywords: 'speech speak elevenlabs sound', summary: () => (view.speech ? 'On' : 'Off'), open: () => speechSettings.open() });
panel.add({ id: 'mic', label: 'Hearing', group: HER, icon: 'mic', keywords: 'microphone mic listen whisper deepgram', summary: () => (view.listening ? 'On' : 'Off'), open: () => listeningSettings.open() });
panel.add({ id: 'names', label: 'Names and tone', group: HER, icon: 'names', keywords: 'name blunt gentle timezone personality', summary: () => namesSettings.summary(), open: () => namesSettings.open() });
panel.add({ id: 'shortcuts', label: 'Shortcuts', group: YOU, icon: 'shortcuts', keywords: 'hotkey keyboard space push to talk key', summary: () => shortcuts.summary(), open: () => shortcuts.open() });
panel.add({ id: 'appearance', label: 'Appearance', group: YOU, icon: 'appearance', keywords: 'theme dark light system mode', summary: () => ({ system: 'Match system', dark: 'Dark', light: 'Light' })[themeChoice], open: () => appearance.open() });
panel.add({ id: 'usage', label: 'API usage', group: YOU, icon: 'usage', keywords: 'cost spend bill tokens money', summary: () => (usage ? pill(usage) : ''), open: () => usageSettings.open() });
panel.add({ id: 'connected', label: 'Connected tools', group: HER, icon: 'connected', keywords: 'mcp server tools plugins apps connect integrations calendar gmail drive notion linear github sign in', summary: () => connected.summary(), open: () => connected.open() });
panel.add({ id: 'skills', label: 'Skills', group: HER, icon: 'skills', keywords: 'skills skill.md instructions abilities claude agent skills folder', summary: () => skills.summary(), open: () => skills.open() });
panel.add({ id: 'folders', label: 'Shared folders', group: COMPUTER, icon: 'folders', keywords: 'files grant folder', summary: () => (view.grantedRoots.length ? `${view.grantedRoots.length} shared` : 'None'), open: () => folders.open() });
panel.add({ id: 'about', label: 'About', group: COMPUTER, icon: 'about', keywords: 'version doctor help', summary: () => version, open: () => about.open() });
function openSettings(at?: string): void {
  void namesSettings.peek().then(() => panel.refresh());
  panel.open(at);
}
settingsBtn.addEventListener('click', () => openSettings());
// Back from signing in to a service: Settings, at that connection, to confirm it.
// A changed skill links straight to its card: #show=skill:<id>.
if (showOnStart?.startsWith('skill:')) {
  skills.reveal(showOnStart.slice('skill:'.length));
  openSettings('skills');
} else if (showOnStart) {
  connected.reveal(showOnStart);
  openSettings('connected');
}
usageBtn.addEventListener('click', () => openSettings('usage'));
let offered = false;

// ── Introductions ────────────────────────────────────────────────────────

function zone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone ?? '';
  } catch {
    return '';
  }
}

function introduce(current: Profile): void {
  ob = begin({ ...current, timezone: current.timezone || zone() });
  obAt = view.entries.length;
  render();
  input.focus();
}

async function finish(): Promise<void> {
  if (!ob) return;
  const { userName, agentName, timezone, directness } = ob.draft;
  let code = '';
  let suggestion = '';
  try {
    const r = await api('PUT', '/api/settings/persona', {
      user_name: userName,
      agent_name: agentName,
      timezone,
      directness,
    });
    if (!r.ok) {
      const body = (await r.json().catch(() => ({}))) as { error?: unknown; suggestion?: unknown };
      code = String(body.error ?? 'unreachable');
      suggestion = typeof body.suggestion === 'string' ? body.suggestion : '';
    }
  } catch {
    code = 'unreachable';
  }
  if (!ob) return;
  ob = code ? failed(ob, code, suggestion) : saved(ob, ready());
  render();
  // A timezone the browser reported but this machine does not know is not the user's
  // to fix: it is saved again without it (or with the nearest real one), at once.
  if (ob.step === 'saving') void finish();
  else after();
}

const ready = (): Ready => ({ model: view.model, speech: view.speech !== null, listening: view.listening !== null });

/** What follows a step: the model dialog if she never got to ask, or her first real words. */
function after(): void {
  if (!ob || ob.step === 'saving') return;
  // She asks about a model herself on the ladder, so the dialog does not open over her.
  if (ob.step === 'model') offered = true;
  if (active(ob)) return;
  if (ob.step === 'done' && ob.greet && !greeted) {
    greeted = true;
    send({ type: 'greet' });
  }
  if (view.model === null && !offered) {
    offered = true;
    void settings.open();
  }
}

function openFor(step: Onboarding['step']): void {
  if (step === 'model') void settings.open();
  else if (step === 'voice') void speechSettings.open();
  else if (step === 'listening') void listeningSettings.open();
}

function reply(text: string): void {
  if (!ob || !active(ob)) return;
  input.value = '';
  input.style.height = '';
  if (ob.step === 'model' || ob.step === 'voice' || ob.step === 'listening' || ob.step === 'demo') {
    const next = text === OPEN ? ob : decide(ob, text, ready());
    // Anything that is not "skip" means "show me": the settings for this step open.
    if (next === ob) openFor(ob.step);
    ob = next;
    render();
    after();
    if (active(ob)) input.focus();
    return;
  }
  ob = answer(ob, text);
  render();
  if (ob.step === 'saving') void finish();
  else if (ob.step === 'skipped') {
    void api('POST', '/api/settings/persona/skip', {}).catch(() => undefined);
    after();
  }
  if (active(ob)) input.focus();
}

/** Change names by talking: the same conversation as onboarding, from current answers. */
function talkNames(): void {
  void (async () => {
    let current: Profile = { userName: '', agentName: view.agentName, timezone: '', directness: 'balanced' };
    try {
      const r = await api('GET', '/api/settings/persona');
      if (r.ok) {
        const p = (await r.json()) as Record<string, unknown>;
        current = {
          userName: typeof p['user_name'] === 'string' ? p['user_name'] : '',
          agentName: typeof p['agent_name'] === 'string' ? p['agent_name'] : view.agentName,
          timezone: typeof p['timezone'] === 'string' ? p['timezone'] : '',
          directness: (['blunt', 'balanced', 'gentle'] as const).find((d) => d === p['directness']) ?? 'balanced',
        };
      }
    } catch {
      // Offline: start from what the page knows. Saving will say if it cannot.
    }
    introduce(current);
  })();
}

newBtn.addEventListener('click', () => {
  // Forget which conversation this tab was in; the core starts a fresh one on reconnect.
  store(CONVERSATION_KEY, null);
  shown.clear();
  if (!active(ob)) ob = null;
  obAt = 0;
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
    if (msg.type === 'audio.stop' || msg.type === 'interrupted') {
      voice.stop();
      stopWatching();
    }
    if (msg.type === 'transcript.final') spokeLast = true;
    // The core opens and closes the microphone; the page only ever follows (VOI-15d).
    if (msg.type === 'mic.start') {
      if (micMode === 'watch' && mic.on) {
        // Already open: what was held goes first, then the microphone carries on.
        const held = bargeHeld ?? [];
        bargeHeld = null;
        micMode = 'send';
        for (const frame of held) if (socket?.readyState === WebSocket.OPEN) socket.send(frame);
      } else if (!mic.on) {
        micMode = 'send';
        void mic.start();
      }
    }
    const busyBarge = msg.type === 'error' && msg['code'] === 'busy' && bargeHeld !== null;
    if (msg.type === 'mic.stop' || (msg.type === 'error' && msg['code'] === 'listening') || busyBarge) {
      mic.stop();
      micMode = 'send';
      bargeHeld = null;
    }
    apply(reduce(view, msg));
    if (msg.type === 'session' || msg.type === 'turn.complete') void refreshUsage();
    if (msg.type === 'session' || msg.type === 'speech') voice.configure(view.speech);
    if (msg.type === 'session') {
      obDue = msg['onboarding'] === 'due';
      version = typeof msg['version'] === 'string' ? msg['version'] : version;
    }
    // Something was chosen in a dialog while she was asking about it: she moves on.
    if (ob && active(ob) && (msg.type === 'model' || msg.type === 'speech' || msg.type === 'listening')) {
      const next = configured(ob, ready());
      if (next !== ob) {
        ob = next;
        render();
        after();
      }
    }
    // First run: she introduces herself before anything else, in an empty conversation.
    if (msg.type === 'state' && obDue && !ob && view.entries.length === 0) {
      obDue = false;
      introduce({ userName: '', agentName: view.agentName, timezone: '', directness: 'balanced' });
    }
    // With nobody to answer, the way to fix that opens by itself, once. Not over an
    // introduction: that comes first and needs no model.
    if (msg.type === 'state' && view.model === null && !offered && !obDue && !active(ob)) {
      offered = true;
      void settings.open();
    }
  });
  ws.addEventListener('close', () => {
    if (socket !== ws) return;
    socket = null;
    voice.stop();
    mic.stop();
    micMode = 'send';
    bargeHeld = null;
    if (view.link === 'unauthenticated' || view.link === 'replaced') return;
    apply({ ...view, link: 'offline', busy: false, orb: 'idle' });
    setTimeout(connect, retryMs);
    retryMs = Math.min(retryMs * 2, 10_000);
  });
}

// ── Input ────────────────────────────────────────────────────────────────

function submit(): void {
  const text = input.value.trim();
  if (ob && active(ob)) {
    if (text && view.link === 'ready') reply(text);
    return;
  }
  if (!text || view.busy || view.link !== 'ready') return;
  voice.unlock(); // sending is a click or a key press, which is what lets a browser play sound
  voice.stop();
  spokeLast = false; // a typed conversation: the microphone stays closed while she answers
  stopWatching();
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
  micMode = 'send';
  bargeHeld = null;
  send({ type: 'interrupt' });
};
stopBtn.addEventListener('click', interrupt);
/** Start listening, or, while listening, send what she heard. The Speak button and Space. */
function talk(): void {
  if (!view.listening) {
    void listeningSettings.open();
  } else if (view.mic) {
    // Done speaking: what she heard so far is the message.
    send({ type: 'listen.stop' });
  } else if (!view.busy && view.link === 'ready') {
    voice.unlock(); // a click is what lets a browser play her answer
    voice.stop();
    send({ type: 'listen.start' });
    // Opened here, inside the click or key press, so the permission prompt belongs to it.
    void mic.start();
  }
}
micBtn.addEventListener('click', talk);
/** What the talk shortcut can see of one key event. */
function keyOf(ev: KeyboardEvent): Parameters<TalkKeys['down']>[0] {
  const target = ev.target instanceof HTMLElement ? ev.target : null;
  return {
    code: ev.code,
    ctrl: ev.ctrlKey,
    alt: ev.altKey,
    shift: ev.shiftKey,
    meta: ev.metaKey,
    repeat: ev.repeat,
    inMessageBox: target === input,
    messageEmpty: input.value.trim() === '',
    // Another field, a button, or anything in Settings takes keys itself.
    inOtherControl:
      panel.isOpen ||
      (target !== null && target !== input && target.closest('input, textarea, select, button, [contenteditable], dialog') !== null),
  };
}

/** Do what the talk shortcut asked. */
function onTalk(action: ReturnType<TalkKeys['down']>, ev: KeyboardEvent): void {
  if (action === null || view.link !== 'ready' || active(ob)) return;
  ev.preventDefault();
  if (action === 'toggle') talk();
  else if (action === 'start' && !view.mic) talk();
  else if (action === 'stop' && view.mic) send({ type: 'listen.stop' });
  else if (action === 'cancel' && view.mic) interrupt();
}

addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape' && (view.busy || voice.active || view.mic)) interrupt();
  // The usual way to Settings: ⌘, on a Mac, Ctrl+, elsewhere. Not the other one.
  if (ev.key === ',' && primary(ev) && !panel.isOpen) {
    ev.preventDefault();
    openSettings();
    return;
  }
  onTalk(talkKeys.down(keyOf(ev)), ev);
  if (ev.key.toLowerCase() === 'k' && primary(ev)) {
    ev.preventDefault();
    input.focus();
  }
});
addEventListener('keyup', (ev) => onTalk(talkKeys.up({ code: ev.code }), ev));
// A key let go in another window never arrives; start fresh when the tab comes back.
addEventListener('blur', () => talkKeys.use(talkPrefs));
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
