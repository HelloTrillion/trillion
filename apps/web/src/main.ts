import { createGalaxy, type Theme } from '@hellotrillion/galaxy';
import '@hellotrillion/galaxy/tokens.css';
import './app.css';
import { type Inline, parse } from './format';
import { appearanceSummary, createAbout, createAppearance, createBoardSettings, createShortcuts, createUsageSettings } from './browser';
import { type Choice, createFolderPanel, createShares, createSharedFolders, footText, loadRoutines, parseFrame } from './folders';
import { type Key, ShellKeys, TalkKeys, type TalkPrefs, contextOf, enterInField, matches } from './hotkey';
import * as desktop from './desktop';
import { icon } from './icons';
import { createConnected } from './connected';
import { createListeningSettings } from './listening';
import { createMic } from './mic';
import { BargeDetector, HOLD_MS, Preroll, afterOver, frameLevel, holdForReply, watchMode, watchTitle } from './barge';
import { createMicrophones } from './microphone';
import { createNamesSettings } from './names';
import {
  APPROVE,
  DECLINE,
  OPEN,
  type Onboarding,
  type Profile,
  type Offer,
  type Ready,
  active,
  answer,
  begin,
  choices,
  climbing,
  configured,
  decide,
  failed,
  folderOffers,
  placeholder,
  placesFound,
  saved,
  sharedFolder,
} from './onboarding';
import { createPanel, type Surface } from './panel';
import { MAC, pageHost, primary, shortcut } from './platform';
import {
  type Theme as ThemeChoice,
  perfModeOf,
  saveBarge,
  saveDetail,
  saveMic,
  saveTalk,
  saveTheme,
  lastHomeDate,
  saveHomeDate,
  saveHomeDaily,
  savedBarge,
  savedDetail,
  savedHomeDaily,
  savedMic,
  savedMicName,
  savedTalk,
  savedTheme,
  shownTheme,
} from './prefs';
import { announcement, statusText } from './announce';
import { readyLine } from './ready';
import { renderScheduled } from './notice';
import { createReminders } from './reminders';
import { createConfirmations } from './confirmations';
import { createMemories } from './memories';
import { createSpeaking } from './speaking';
import { createSidebar, revealForCard } from './sidebar';
import { createDayOpener, createThreads, escapeAction, listPath, whenIdle } from './threads';
import { type Api, createSettings } from './settings';
import { createSkills } from './skills';
import { createSpeechSettings } from './speech';
import { createDevicesSettings, deviceId } from './devices';
import { type Helper, askTarget, messagePlaceholder, capNameOf, createAskQueue, createHelpers, createdOpener, helloOnce, stepTarget } from './helpers';
import { lookOf, picture } from './look';
import { createRail } from './rail';
import { chatHead, offerLine } from './offer';
import { createSpendCards, createSwitchLines, speakCapped, spendReveals } from './spend';
import { type Usage, fetchUsage, pill, pillState, sentence } from './usage';
import { createVoice } from './voice';
import { createWorkspace } from './workspace';
import { type Kit, createKit, isKit } from './kit';
import { openShowcase, showcaseScene } from './showcase';
import { boardTitle, buttonAction, createBoardView, createSeen } from './board';
import { createHome, dayBoardStep, endsBuild, frameKeeps, herOpensHome, localDate, returnsToHome, shouldOpenHome } from './home';
import { createApprovals, outcomeWords } from './approvals';
import { type DraftCard, createDraftPin } from './draft';
import { DOCK, isPhone } from './layout';
import { captionOf, createCaptions } from './captions';
import {
  type Entry,
  INITIAL,
  type ServerMsg,
  boardOpen,
  type View,
  expire,
  micFailed,
  offerChosen,
  pinnedDraft,
  reduce,
  sent,
  toolLabel,
  backgroundLine,
  unreadFor,
  waiting,
} from './view';

const PROTOCOL = 1;
const TOKEN_KEY = 'trillion-token';
const CONVERSATION_KEY = 'trillion-conversation';
// In development the page comes from Vite and the core runs beside it.
const CORE = import.meta.env.DEV ? '127.0.0.1:7777' : location.host;
const WS_URL = `ws://${CORE}/ws`;
const API_URL = import.meta.env.DEV ? `http://${CORE}` : '';
// A design preview of the workspace with sample content (#showcase). Read before the token is taken from the fragment.
const showcaseAt = showcaseScene(location.search, location.hash);
// The widget style guide (#kit), likewise.
const kitAt = isKit(location.hash);

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
 * A skill's id is a path (#show=skill:folder:/a/b, or C:\a\b on Windows), so `/` and
 * `\` are allowed; it is only ever compared, never fetched. Taken once, and the
 * address bar cleaned, like the token. */
function takeShow(): string | null {
  const match = /(?:^#|&)show=([\w:./\\%-]+)/.exec(location.hash);
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
// Under the banner: a switch made at a spend cap while it lasts, then any spend card,
// one put off with "stop for now" kept there as a line to choose again from (BRN-8).
const switchEl = document.createElement('div');
switchEl.className = 'spend';
const spendEl = document.createElement('div');
spendEl.className = 'spend';
bannerEl.after(switchEl, spendEl);
// Its api is read when a button is pressed, long after the page has set it up.
const switchLines = createSwitchLines(switchEl, (method, path, body) => api(method, path, body));
const transcriptEl = $('transcript');
const form = $<HTMLFormElement>('ask');
// Her draft of a new helper, pinned above the message box while it waits: Create and Not
// now are its card's Approve and Decline, sent as every card's are.
const draftPin = createDraftPin({
  onCreate: (id) => send({ type: 'confirm', pending_id: id }),
  onNotNow: (id) => send({ type: 'cancel', pending_id: id }),
  onConnect: (catalogId) => openStep(`connect:${catalogId}`),
});
form.before(draftPin.el);
const input = $<HTMLTextAreaElement>('input');
const sendBtn = $<HTMLButtonElement>('send');
const stopBtn = $<HTMLButtonElement>('stop');
// The ask bar's actions are icons, each named for readers and on hover.
sendBtn.replaceChildren(icon('send', 18));
sendBtn.setAttribute('aria-label', 'Send');
sendBtn.title = 'Send (Enter)';
stopBtn.replaceChildren(icon('stop', 16));
stopBtn.setAttribute('aria-label', 'Stop');
stopBtn.title = 'Stop her (Esc)';
let micIcon = '';
const footEl = $('foot');
const jobsEl = $('jobs-line');
const newBtn = $<HTMLButtonElement>('new');
newBtn.replaceChildren(icon('edit', 18));
const agentNameEl = $('agent-name');
const threadTitleEl = $('thread-title');
const threadSwitch = $<HTMLButtonElement>('thread-switch');
const threadsEl = $('threads');
const historyBtn = $<HTMLButtonElement>('history');
historyBtn.replaceChildren(icon('history', 18));
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
// In the desktop app: `<html data-shell="desktop">`, links for a new tab open in the browser, and
// whether the shell made the talk shortcut global (then the shell alone reports that key).
desktop.markShell();
desktop.routeLinks();
let talkGlobal: boolean | null = null;
let shellKeys = new ShellKeys(talkKeys);
let version = '';
// How much the galaxy draws, as chosen in this browser: automatic unless changed (GAL-6).
let detailChoice = savedDetail();
const galaxy = createGalaxy(canvas, {
  theme: shownTheme(themeChoice, systemLight.matches),
  perfMode: perfModeOf(detailChoice),
});

// The docked orb's size and place come from DOCK; the CSS (its button, the hole in the workspace) reads them here.
document.documentElement.style.setProperty('--dock-size', `${DOCK.size}px`);
document.documentElement.style.setProperty('--dock-inset', `${DOCK.inset}px`);
document.documentElement.style.setProperty('--dock-phone-size', `${DOCK.phoneSize}px`);
document.documentElement.style.setProperty('--dock-phone-inset', `${DOCK.phoneInset}px`);

const showSidebarBtn = $<HTMLButtonElement>('show-sidebar');
const hideSidebarBtn = $<HTMLButtonElement>('hide-sidebar');
const workspaceCloseBtn = $<HTMLButtonElement>('workspace-close');
showSidebarBtn.replaceChildren(icon('sidebar', 20));
hideSidebarBtn.replaceChildren(icon('sidebar', 20));
workspaceCloseBtn.replaceChildren(icon('close', 18));
showSidebarBtn.title = `Show sidebar (${shortcut('\\')})`;
hideSidebarBtn.title = `Hide sidebar (${shortcut('\\')})`;
const sidebar = createSidebar($('sidebar'), $('side-resize'), showSidebarBtn, hideSidebarBtn, $('ask'));
const workspace = createWorkspace({
  region: $('workspace'),
  title: $('workspace-title'),
  body: $('workspace-body'),
  close: workspaceCloseBtn,
  dock: $<HTMLButtonElement>('orb-dock'),
  sidebarWidth: () => sidebar.width(),
  sheetTop: () => sidebar.sheetTop(),
  hold: (open) => sidebar.hold(open),
  place: (rect, animate) => galaxy.setStage(rect, { animate }),
  // A caption never sits over the workspace.
  changed: () => showCaptions(),
  fallback: () => (sidebar.hidden ? showSidebarBtn : null),
});
const captions = createCaptions($('captions'));
function placeCaptions(): void {
  const w = innerWidth;
  captions.place({ w, h: innerHeight, sidebar: sidebar.width(), workspace: false, phone: isPhone(w) });
}
placeCaptions();
addEventListener('resize', placeCaptions);
/** Captions show only while the sidebar is hidden and the workspace closed. */
function showCaptions(): void {
  captions.update(captionOf(view), sidebar.hidden && !workspace.isOpen);
}
sidebar.onChange((animate) => {
  workspace.layout(animate);
  placeCaptions();
  showCaptions();
});
// A hidden sidebar is inert and so silent: its news is read out here instead (SUR-6).
const announceEl = $('announce');

/** Put the cursor in the message box, showing the sidebar first if it is hidden. */
function focusInput(): void {
  // Showing it moves focus to its hide button, so this comes first.
  sidebar.setHidden(false);
  input.focus();
}
// The first placement is where the orb starts, not somewhere it glides to.
workspace.layout(false);

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
// The conversations opened on this page, the latest last; + New helper asks in one.
let visited: string[] = [];
// + New helper's words, asked from a helper's chat: sent once her conversation is open.
const askQueue = createAskQueue({
  ready: () => view.link === 'ready' && !view.busy,
  send: (words) => sendTurn(words),
  keep: (words) => keepDraft(words),
});
// Set once the helpers panel and the chat's head exist: a render before then draws no head.
let chatHeadOn = false;
// Voice barge-in (VOI-9). While she answers a spoken question, the microphone is open
// and "watching". With hearing that judges turns, every frame goes to the core, which
// tells her echo from the user by the words: it ducks her when they may be cutting in
// (`overlap.duck`), and then lets her carry on or stops her. Otherwise frames stay on
// this page, measured against her own level, and nothing is sent: when the user talks
// over her she stops at once, the core is told, and the frames from just before are
// held until it is listening, then sent first.
let spokeLast = false;
let bargeOn = savedBarge();
let micMode: 'send' | 'watch' = 'send';
// Watching by streaming to the core (`overlap.watch`): the core ends it.
let streamWatch = false;
// Asked to stop watching while the core was deciding: done once it has answered.
let unwatchPending = false;
let bargeHeld: ArrayBuffer[] | null = null;
// She is ducked and the core is deciding: every frame goes to it as heard.
let overlapping = false;
// The user cut in (an overlap or a barge) and the core has not yet opened or closed the
// microphone for it. A refusal meanwhile must close it, even after `audio.stop`.
let cutIn = false;
const detector = new BargeDetector();
const preroll = new Preroll();
// The microphone was kept open from the user's turn and her reply has not sounded yet.
let awaitingHer = false;
let holdTimer: ReturnType<typeof setTimeout> | null = null;
// Counts each new watch, so a late "close soon" never closes a newer one.
let watchGen = 0;

function endHold(): void {
  awaitingHer = false;
  if (holdTimer !== null) clearTimeout(holdTimer);
  holdTimer = null;
  render();
}

/** Open and only watching for the user to cut in: shown, whatever is sent (VOI-15d). */
function watching(): boolean {
  return (micMode === 'watch' || streamWatch) && mic.on;
}

/** Watch by streaming to the core: every frame is sent, and the core decides. */
function streamToCore(): boolean {
  return watchMode(view.listening?.turns === true) === 'stream';
}

/** Start watching by streaming: the core is told, and frames go to it as they come. */
function beginStreamWatch(): void {
  micMode = 'send';
  streamWatch = true;
  unwatchPending = false;
  bargeHeld = null;
  send({ type: 'overlap.watch' });
}

/** Wait for her reply with the microphone open, but not forever. */
function armHold(): void {
  endHold();
  awaitingHer = true;
  holdTimer = setTimeout(() => {
    holdTimer = null;
    if (!awaitingHer || voice.active) return;
    awaitingHer = false;
    stopWatching();
  }, HOLD_MS);
}

/** Listening is over and her reply is coming: the open microphone goes on watching
 * instead of closing (see holdForReply). Until she sounds, her level is 0, so the
 * detector hears the room, and the user going on talking counts as cutting in. Its
 * warm-up runs here as at any start: it learns the room just after the user stopped,
 * and decides nothing for half a second. That is never deafer than before, when the
 * microphone was closed until her first sound, and it starts over then anyway. With
 * hearing that judges turns there is no detector: the microphone streams to the core,
 * which goes on hearing the user from here. */
function holdForHer(): void {
  watchGen++;
  armHold();
  if (streamToCore()) {
    beginStreamWatch();
  } else {
    micMode = 'watch';
    bargeHeld = null;
    detector.reset();
    preroll.drain();
  }
  render();
}

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
  const held = awaitingHer;
  endHold();
  if (streamWatch && mic.on) {
    // Already streaming to the core since the user's turn: it goes on as it is.
    if (!canWatch()) {
      if (held) stopWatching();
      return;
    }
    watchGen++;
    return;
  }
  if (micMode === 'watch' && mic.on) {
    if (!canWatch()) {
      if (held) stopWatching();
      return;
    }
    // Already open (since the user's turn, or still from her last reply): not opened
    // again, which is what glitched her first words. Only started over for her voice,
    // so the warm-up learns her echo from her first sound, as it did when the
    // microphone opened then. Not while the user's first words are held: they are
    // cutting in. A new watch: a late "close soon" from before must leave it be.
    if (!bargeHeld) detector.reset();
    watchGen++;
    return;
  }
  if (mic.on || !canWatch()) return;
  watchGen++;
  if (streamToCore()) {
    beginStreamWatch();
  } else {
    micMode = 'watch';
    bargeHeld = null;
    detector.reset();
    preroll.drain();
  }
  void mic.start();
}

/** Close a watching microphone, unless the user has just cut in. */
function stopWatching(): void {
  if (streamWatch) {
    // The core is deciding whether they cut in: once it answers, this closes it.
    if (overlapping) {
      unwatchPending = true;
      return;
    }
    unwatchPending = false;
    streamWatch = false;
    send({ type: 'overlap.unwatch' });
    endHold();
    mic.stop();
    micMode = 'send';
    render();
    return;
  }
  if (micMode !== 'watch' || bargeHeld || overlapping) return;
  endHold();
  mic.stop();
  micMode = 'send';
  render();
}

/** Watching here by loudness, for hearing that cannot judge turns. */
function watchFrame(pcm: ArrayBuffer): void {
  if (bargeHeld) {
    bargeHeld.push(pcm); // the user is talking: keep every word until she listens
    return;
  }
  preroll.push(pcm);
  if (detector.push(frameLevel(pcm), voice.level() ?? 0)) {
    // Before her reply sounds there is nothing to stop yet. Only hearing that judges
    // turns lets the core decide what this is; otherwise the pre-roll just rolls on,
    // and the user can still interrupt once she speaks.
    if (awaitingHer && !view.listening?.turns) return;
    if (view.listening?.turns) {
      // Duck, then decide (VOI-9): she goes quiet while the core hears what this is.
      voice.duck(true);
      overlapping = true;
      cutIn = true;
      micMode = 'send';
      send({ type: 'overlap' });
      render();
      // The words from just before go first, then the microphone carries on as heard.
      for (const frame of preroll.drain()) if (socket?.readyState === WebSocket.OPEN) socket.send(frame);
    } else {
      bargeHeld = preroll.drain();
      voice.stop();
      cutIn = true;
      send({ type: 'barge' });
    }
  }
}

/** A watching microphone closes a moment after she has finished, unless the core has
 * opened it for a follow-up by then. */
function stopWatchingSoon(): void {
  const gen = watchGen;
  setTimeout(() => {
    // Streaming, the core ends the watch: it goes on hearing her last words come back
    // for a moment after she ends, then opens the window or closes it.
    if (gen === watchGen && micMode === 'watch' && !view.mic) stopWatching();
  }, 1500);
}

const voice = createVoice({
  onActive: (on) => {
    if (on) startWatching();
    render();
  },
  onEnded: (turn, playedMs) => {
    send({ type: 'playback.ended', turn_id: turn, played_ms: playedMs });
    // A turn that ended without a sound from her ends the wait for one too.
    endHold();
    // After a spoken turn the core opens the microphone for a follow-up, and the
    // watching one simply carries on as it. Otherwise it closes.
    stopWatchingSoon();
  },
  onAudible: (turn, inMs) => send({ type: 'playback.started', turn_id: turn, in_ms: inMs }),
  // What the user has heard: an interrupted reply is kept up to here (VOI-9, D8).
  onSentence: (turn, seq) => send({ type: 'playback.sentence', turn_id: turn, seq }),
  attach: (node) => galaxy.attachAnalyser(node, 'playback'),
});
let socket: WebSocket | null = null;
// Her ears. Frames go to the core as they are captured, as binary (VOI-1).
const mic = createMic({
  onFrame: (pcm) => {
    if (micMode === 'watch') watchFrame(pcm);
    else if (socket?.readyState === WebSocket.OPEN) socket.send(pcm);
  },
  onSetup: (setup) => {
    send({ type: 'mic.opened', ...setup });
    render(); // a watching microphone is shown once it is really open
  },
  device: savedMic,
  deviceName: savedMicName,
  onDeviceFound: (id, name) => saveMic(id, name),
  onProblem: (problem) => {
    endHold();
    if (streamWatch) {
      // Unheard, the user cannot cut in: the core stops watching, and she carries on.
      streamWatch = false;
      unwatchPending = false;
      micMode = 'send';
      send({ type: 'overlap.unwatch' });
      if (overlapping) {
        voice.duck(false);
        overlapping = false;
        apply(micFailed(view, problem));
      }
      return;
    }
    if (overlapping) {
      // Unheard, the user cannot be talking over her any more: she comes back up and
      // carries on. The core hears nothing more and lets her, so nothing is stopped.
      voice.duck(false);
      overlapping = false;
      micMode = 'send';
      apply(micFailed(view, problem));
      return;
    }
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
// The next try after the link dropped. Cleared by any connect, so two sockets never race.
let retryTimer: ReturnType<typeof setTimeout> | null = null;
// A pending action's card: pinned on her board when it can open there, else in the conversation
// as always. It also tells the core when a full card is first on screen (SPN-17).
const approvals = createApprovals({
  send: (body) => send(body),
  entries: () => view.entries,
  where: () => ({ phone: isPhone(innerWidth), settingsShowing: workspace.showing === 'settings' }),
  boardShowing: () => workspace.showing === 'board',
  showBoard: () => showBoard(),
  closeBoard: () => workspace.close(),
  hasWidgets: () => Boolean(view.board?.open && view.board.widgets.length > 0),
  toConversation: () => {
    const reveal = revealForCard(isPhone(innerWidth), sidebar.hidden);
    if (reveal.unhide) sidebar.setHidden(false);
    if (reveal.raise) sidebar.raise(reveal.raise);
    // The entries did not change, only where their cards are drawn: draw the conversation afresh.
    rendered = [];
    render();
  },
});

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
          toolLabel: 'Example',
          reason: null,
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

/** Write to the core on this page's socket. False when it is not open, so nothing went. */
function send(body: Record<string, unknown>): boolean {
  if (socket?.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify(body));
  return true;
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
    case 'user': {
      if (!e.seen) return el('p', 'msg user', e.text);
      // She chose to say nothing to it (VOI-7): a quiet "Seen" under it, never a bubble.
      // Hidden from the log's live region, which would read it out as news.
      const turn = el('div', 'user-turn');
      const mark = el('p', 'seen', 'Seen');
      mark.setAttribute('aria-hidden', 'true');
      turn.append(el('p', 'msg user', e.text), mark);
      return turn;
    }
    case 'assistant': {
      const box = el('div', 'msg assistant');
      box.append(...formatted(e.text));
      if (e.interrupted) box.append(el('span', 'mark', '(stopped)'));
      return box;
    }
    case 'tool': {
      return el('p', `chip${e.ok ? '' : ' bad'}`, toolLabel(e.name, e.outcome, e.ok, e.label));
    }
    case 'notice':
      return el('p', 'notice', e.text);
    case 'offer':
      // Her offer of a helper: the tap is the user's own, sent as it is.
      return offerLine(
        e,
        (choice) => {
          if (send({ type: 'helper.offer', area: e.area, choice })) apply(offerChosen(view, e.area, choice));
        },
        view.agentName,
      );
    case 'host':
      // A ready line kept from an earlier visit: drawn again with its Open.
      if (e.conversationId) return readyLine(e.text, e.conversationId, openReady);
      return el('p', 'host-line', e.text);
    case 'scheduled': {
      // A routine's: one line that opens what it built.
      // From a helper, its name leads the line and opens its details in the panel.
      if (e.what === 'ready') return readyLine(e.title, e.conversationId, openReady, e.helper, showHelper);
      // Her line, or its title when she has none; Join, the detail and why she held it.
      return renderScheduled(e, formatted, undefined, showHelper);
    }
    case 'card': {
      if (e.draft) {
        // A helper's draft is pinned above the message box: here, a line that goes to it.
        if (e.status !== 'waiting') return el('p', 'chip ap-chip-done', `New helper ${e.draft.name}: ${outcomeWords(e.status, e.note)}`);
        const chip = el('button', 'ap-chip');
        chip.type = 'button';
        chip.append(el('span', 'ap-chip-label', `New helper: ${e.draft.name}`), el('span', 'ap-chip-view', 'View'));
        chip.addEventListener('click', () => draftPin.focus());
        return chip;
      }
      if (approvals.onBoard(e.pendingId)) {
        // Its full card is on the board: here, a line that jumps to it, then how it ended.
        if (e.status !== 'waiting') return el('p', 'chip ap-chip-done', `${e.toolLabel}: ${outcomeWords(e.status, e.note)}`);
        const chip = el('button', 'ap-chip');
        chip.type = 'button';
        chip.append(el('span', 'ap-chip-label', `Needs your OK: ${e.toolLabel}`), el('span', 'ap-chip-view', 'View'));
        chip.addEventListener('click', () => approvals.view(e.pendingId));
        return chip;
      }
      const example = e.pendingId === EXAMPLE_CARD;
      const card = el('section', `card ${e.status}`);
      card.setAttribute('aria-label', example ? 'An example of an approval card' : 'Needs your approval');
      card.append(el('h2', 'card-title', example ? 'Example' : e.status === 'waiting' ? 'Needs your yes' : 'Request'));
      const summary = el('p', 'card-summary', e.summary);
      if (e.look) {
        // A new helper's picture beside its name and job, as its tap will make it.
        const row = el('div', 'card-look');
        row.append(picture(e.look, 28), summary);
        card.append(row);
      } else card.append(summary);
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
// Her board's asks are hidden while a tap could not go as a turn: an approval is waiting (on the
// board or in the conversation), she is busy, or the link is down. Never a tap silently lost.
let asksOff = false;
// How many times the board has been drawn, so a render draws it at most once.
let boardDraws = 0;

// Shown under the conversation while she works and no text is arriving. The status
// in the sidebar's head already announces it to screen readers, so this one stays silent.
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
  draftPin.draw(view.link === 'ready' ? (pinnedDraft(view.entries) as DraftCard | null) : null);

  showCaptions();

  // The board follows its approvals, and the core records a card as delivered only once it is
  // really on screen (SPN-17).
  // Decided first, so a board that approvals.sync() redraws already has it.
  const off = view.busy || view.link !== 'ready' || view.entries.some((e) => e.kind === 'card' && e.status === 'waiting');
  const asksChanged = off !== asksOff;
  asksOff = off;
  const drawsBefore = boardDraws;
  approvals.sync();
  if (asksChanged && boardDraws === drawsBefore && workspace.showing === 'board') showBoard();

  const ready = view.link === 'ready';
  const linkText = LINK_TEXT[view.link];
  statusEl.textContent = statusText(view);
  newBtn.hidden = !ready || view.entries.length === 0;
  agentNameEl.textContent = view.agentName;
  if (chatHeadOn) {
    rail.name(view.agentName);
    rail.select(view.conversationId);
  }
  threadTitleEl.textContent = view.title ?? 'New conversation';
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
  const orbShown = !ready ? (view.link === 'connecting' ? 'idle' : 'error') : voice.active ? 'speaking' : view.orb;
  galaxy.setState(orbShown);
  // The desktop app's tray icon and floating orb follow the same state and caption (a repeat is not sent).
  desktop.reportState(desktop.orbState(orbShown), captionOf(view));

  const noModel = ready && view.model === null;
  // A spend card says its line itself: the banner does not say it again.
  const bannerLine = view.banner && !spendCards.has(view.banner) ? view.banner : '';
  const banner =
    linkText ||
    bannerLine ||
    (noModel && !introducing ? 'Nobody is here to answer yet. Choose a model in Settings to get started.' : '');
  bannerEl.hidden = !banner;
  bannerEl.textContent = banner;
  drawSwitches();

  // Introductions need no model: the app writes the names, not her.
  const canType = ready && (!noModel || introducing);
  input.disabled = !canType;
  input.placeholder =
    introducing && ob
      ? placeholder(ob)
      : !canType
        ? ''
        : messagePlaceholder(view.agentName, view.entries.length > 0, ownHere()?.name ?? null);
  // Reads she sent off keep running while the user talks: a quiet count, never busy.
  const jobsText = ready ? backgroundLine(view.jobs) : '';
  jobsEl.hidden = !jobsText;
  jobsEl.textContent = jobsText;
  sendBtn.disabled = !canType || view.busy;
  sendBtn.hidden = view.busy;
  stopBtn.hidden = !(view.busy || voice.active || view.mic);
  // Watching for a cut-in is a microphone open too, so it shows, even while she answers.
  const watch = !view.mic && watching();
  micBtn.hidden = !canType || (view.busy && !watch) || introducing;
  // Speaking, the button finishes the turn: a tick. Otherwise it is the microphone.
  const nextMic = view.mic ? 'check' : 'mic';
  if (nextMic !== micIcon) {
    micIcon = nextMic;
    micBtn.replaceChildren(icon(nextMic, 18));
  }
  micBtn.title = !view.listening
    ? 'Choose how she hears you'
    : view.mic
      ? 'Send what you said'
      : watch
        ? watchTitle(streamWatch ? 'stream' : 'local')
        : 'Speak to her';
  micBtn.setAttribute('aria-label', view.mic ? 'Done' : watch ? micBtn.title : 'Speak');
  micBtn.setAttribute('aria-pressed', String(view.mic));
  micBtn.classList.toggle('live', view.mic);
  micBtn.classList.toggle('watching', watch);
  // The shares as Settings reads them, read again on each session and each change.
  footEl.textContent = ready ? footText(shares.last) : '';
  footEl.title = footEl.textContent;
  drawChatHead();
}

function apply(next: View): void {
  if (next === view) return;
  const before = view;
  view = next;
  if (view.conversationId && view.conversationId !== before.conversationId) {
    store(CONVERSATION_KEY, view.conversationId);
    // Where + New helper asks her: the last open that is not a helper's own chat.
    const id = view.conversationId;
    visited = [...visited.filter((v) => v !== id), id].slice(-50);
  }
  if (sidebar.hidden) {
    const line = announcement(before, view);
    if (line) announceEl.textContent = line;
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
// One panel for every setting (Settings, at the top of the sidebar), drawn in the workspace while
// the orb docks. The model, voice and microphone sections are the same modules that used to be
// separate dialogs.
const settingsSurface: Surface = {
  get open() {
    return workspace.showing === 'settings';
  },
  show: (content, title) => workspace.open(title, content, 'settings'),
  close: () => {
    if (workspace.showing === 'settings') workspace.close();
  },
  onClose: (l) =>
    workspace.onClose((id) => {
      if (id === 'settings') l();
    }),
};
const panel = createPanel(settingsSurface);
const settings = createSettings(panel.host('model'), api);
const speechSettings = createSpeechSettings(panel.host('voice'), api);
const listeningSettings = createListeningSettings(
  panel.host('mic'),
  api,
  createMicrophones(
    savedMic,
    (id, name) => {
      saveMic(id, name);
      panel.refresh();
    },
    {
      on: () => bargeOn,
      set: (on) => {
        bargeOn = on;
        saveBarge(on);
        if (!on) stopWatching();
      },
      turns: () => view.listening?.turns === true,
    },
    savedMicName,
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
    registerTalk();
  },
  desktop.DESKTOP
    ? {
        global: () => talkGlobal,
        // While a new shortcut is recorded the old one is let go, so its keys reach the recorder.
        recording: (on) => {
          if (on) {
            ++talkAsked;
            talkGlobal = null;
            void desktop.setTalkShortcut(null, talkPrefs.mode);
          } else registerTalk();
        },
      }
    : undefined,
);
/** In the desktop app, make the talk shortcut work from any app; only the latest answer counts. */
let talkAsked = 0;
function registerTalk(): void {
  if (!desktop.DESKTOP) return;
  const asked = ++talkAsked;
  talkGlobal = null;
  shellKeys = new ShellKeys(talkKeys);
  shortcuts.redraw();
  void desktop.setTalkShortcut(talkPrefs.hotkey, talkPrefs.mode).then(({ global }) => {
    if (asked !== talkAsked) return;
    talkGlobal = global;
    shortcuts.redraw();
  });
}
registerTalk();
const appearance = createAppearance(
  panel.host('appearance'),
  () => themeChoice,
  (t) => {
    applyTheme(t);
    panel.refresh();
  },
  {
    get: () => detailChoice,
    set: (d) => {
      detailChoice = d;
      saveDetail(d);
      galaxy.setPerfMode(perfModeOf(d));
      panel.refresh();
    },
  },
);
const boardSettings = createBoardSettings(panel.host('board'), api, () => panel.refresh(), { get: savedHomeDaily, set: saveHomeDaily });
// Shares, read and changed through the routes: Settings and the Folders panel share them.
const shares = createShares(api);
const routineChoices = (): Promise<Choice[]> => loadRoutines(api);
// The Folders panel sits in the workspace beside what it shows (the board, or Settings).
// Opened with nothing else up, it brings the workspace with it, and takes it away again.
const workspaceEl = $('workspace');
let foldersBroughtWorkspace = false;
const folderPanel = createFolderPanel({
  api,
  shares,
  routines: routineChoices,
  conversation: () => view.conversationId,
  focusAfter: () => (workspace.isOpen ? workspaceCloseBtn : null),
  shown: (open) => {
    workspaceEl.classList.toggle('has-folders', open);
    if (open && !workspace.isOpen) {
      foldersBroughtWorkspace = true;
      showBoard();
    }
    if (open) return;
    const brought = foldersBroughtWorkspace;
    foldersBroughtWorkspace = false;
    if (brought && workspace.showing === 'board' && !view.board?.open && !approvals.holds()) workspace.close();
  },
});
workspaceEl.insertBefore(folderPanel.el, $('workspace-body'));
// Outside the panel, which is hidden until its first frame: that frame is announced too.
document.body.append(folderPanel.live);
workspace.onClose((_id, replaced) => {
  if (!replaced) folderPanel.close();
});
const folders = createSharedFolders(panel.host('folders'), {
  shares,
  routines: routineChoices,
  pick: desktop.DESKTOP ? desktop.pickFolder : null,
  browse: () => folderPanel.openPlaces(),
  changed: () => {
    panel.refresh();
    render();
  },
});
// Home (SUR-1a): the empty workspace's morning screen. Its buttons open Settings over it; closing
// Settings comes back to Home (backToHome), so a tile turns ready where the user left it.
let backToHome = false;
// Whether the board view is showing Home rather than her widgets.
let homeShown = false;
// Once per page load, at the first board: whether today's first load opens Home (D2).
let homeChecked = false;
function fromHome(): void {
  backToHome = workspace.showing === 'board';
}
const home = createHome({
  fetchHome: () => okJson(api('GET', '/api/workspace/home')),
  saveLayout: (layout) => okJson(api('PUT', '/api/workspace/home/layout', layout)),
  onBuild: () => void buildFromHome(),
  onConnect: (id) => {
    fromHome();
    openSettings('connected');
    void connected.start(id);
  },
  onBrowse: (category) => {
    fromHome();
    connected.browse(category);
    openSettings('connected');
  },
  onReveal: (key) => {
    fromHome();
    connected.reveal(key);
    openSettings('connected');
  },
});
const connected = createConnected(panel.host('connected'), api, () => {
  panel.refresh();
  // A connector set up or removed puts its tile on, or takes it off, the cards that name it.
  if (workspace.showing === 'board') showBoard();
  // And Home's tiles follow what is connected now.
  void home.refresh();
});
// Back from a sign-in in another tab or the browser: Home reads the state again.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') void home.refresh();
});
const skills = createSkills(panel.host('skills'), api, () => panel.refresh());
const memories = createMemories(panel.host('memories'), api, send, () => panel.refresh());
const reminders = createReminders(panel.host('reminders'), api, () => panel.refresh(), openReady);
// Read-only: a waiting row goes to its card's conversation, where it is answered (SPN-14).
const confirmations = createConfirmations(panel.host('confirmations'), api, () => panel.refresh(), openReady);
const speaking = createSpeaking(panel.host('speaking'), api, () => panel.refresh());
const about = createAbout(panel.host('about'), () => ({ version, model: view.model ?? 'None chosen' }));
async function refreshUsage(): Promise<Usage | null> {
  usage = await fetchUsage(api);
  usageBtn.hidden = usage === null;
  if (usage) {
    // Amber near or over a soft cap, red at a hard one; the words name the provider and cap.
    const at = pillState(usage);
    usageBtn.classList.toggle('warned', at.state === 'warned');
    usageBtn.classList.toggle('capped', at.state === 'capped');
    usageBtn.textContent = pill(usage);
    usageBtn.title = at.title || sentence(usage);
    usageBtn.setAttribute('aria-label', `${pill(usage)}. ${sentence(usage)}${at.note ? ` ${at.note}` : ''}`);
  }
  // Raised in Settings, not on the card: nothing is capped now, so the calm stop is over.
  if (usage?.providers && view.capped && !usage.providers.some((p) => p.state === 'capped')) apply({ ...view, capped: false });
  drawSwitches();
  return usage;
}
const usageSettings = createUsageSettings(panel.host('usage'), refreshUsage, api);
// A spend card's choice goes on this page's own socket, and only from a click (SPN-14).
/** Each switch in force, with whether its usual provider is still at its cap. */
function drawSwitches(): void {
  switchLines.render(view.link === 'ready' ? view.switches : [], (pid) => {
    const p = usage?.providers?.find((x) => x.provider_id === pid);
    return { name: p?.name ?? 'the usual one', capped: p?.state === 'capped' };
  });
}
const spendCards = createSpendCards(spendEl, (body) => send(body), undefined, (card, status) => {
  // Raised on a provider that has a switch: the banner asks whether to go back now.
  if (status === 'raised') switchLines.raised(card.provider_id);
});
const HER = 'Her';
const YOU = 'You';
const COMPUTER = 'This computer';
panel.add({ id: 'model', label: 'Model', group: HER, icon: 'model', keywords: 'brain provider key anthropic openai ollama', summary: () => view.model ?? 'None chosen', open: () => settings.open() });
panel.add({ id: 'voice', label: 'Voice', group: HER, icon: 'voice', keywords: 'speech speak elevenlabs sound', summary: () => (view.speech ? 'On' : 'Off'), open: () => speechSettings.open() });
panel.add({ id: 'mic', label: 'Hearing', group: HER, icon: 'mic', keywords: 'microphone mic listen whisper deepgram', summary: () => (view.listening ? 'On' : 'Off'), open: () => listeningSettings.open() });
panel.add({ id: 'names', label: 'Names and tone', group: HER, icon: 'names', keywords: 'name blunt gentle timezone personality', summary: () => namesSettings.summary(), open: () => namesSettings.open() });
panel.add({ id: 'shortcuts', label: 'Shortcuts', group: YOU, icon: 'shortcuts', keywords: 'hotkey keyboard space push to talk key', summary: () => shortcuts.summary(), open: () => shortcuts.open() });
panel.add({ id: 'appearance', label: 'Appearance', group: YOU, icon: 'appearance', keywords: 'theme dark light system mode performance smooth detail', summary: () => appearanceSummary(themeChoice, detailChoice), open: () => appearance.open() });
panel.add({ id: 'board', label: 'Board', group: YOU, icon: 'workspace', keywords: 'workspace widgets live refresh fresh update cards home morning daily briefing', summary: () => boardSettings.summary(), open: () => boardSettings.open() });
panel.add({ id: 'reminders', label: 'Reminders', group: YOU, icon: 'reminders', keywords: 'remind reminder schedule timed ask every weekday routine alarm notification', summary: () => reminders.summary(), open: () => reminders.open() });
panel.add({ id: 'confirmations', label: 'Confirmations', group: YOU, icon: 'confirmations', keywords: 'approvals approved approve decisions record history audit cards yes declined waiting unknown did that reach me', summary: () => confirmations.summary(), open: () => confirmations.open() });
panel.add({ id: 'speaking', label: 'Speaking up', group: YOU, icon: 'speaking', keywords: 'heads up meeting calendar email gmail urgent payments stripe quiet hours focus do not disturb microphone watch notify', summary: () => speaking.summary(), open: () => speaking.open() });
panel.add({ id: 'usage', label: 'Usage and limits', group: YOU, icon: 'usage', keywords: 'cost spend bill tokens money', summary: () => (usage ? pill(usage) : ''), open: () => usageSettings.open() });
panel.add({ id: 'connected', label: 'Connected tools', group: HER, icon: 'connected', keywords: 'mcp server tools plugins apps connect integrations calendar gmail drive notion linear github sign in', summary: () => connected.summary(), open: () => connected.open() });
panel.add({ id: 'skills', label: 'Skills', group: HER, icon: 'skills', keywords: 'skills skill.md instructions abilities claude agent skills folder', summary: () => skills.summary(), open: () => skills.open() });
panel.add({ id: 'memories', label: 'Memories', group: HER, icon: 'memories', keywords: 'memory remember remembered forget facts about me edit correct', summary: () => memories.summary(), open: () => memories.open() });
panel.add({ id: 'folders', label: 'Shared folders', group: COMPUTER, icon: 'folders', keywords: 'files grant folder share browse picker access read change', summary: () => folders.summary(), open: () => folders.open() });
const devicesSettings = createDevicesSettings(panel.host('devices'), api);
panel.add({ id: 'devices', label: 'Devices', group: COMPUTER, icon: 'devices', keywords: 'security sign in browser device forget new login', summary: () => devicesSettings.summary(), open: () => devicesSettings.open() });
panel.add({ id: 'about', label: 'About', group: COMPUTER, icon: 'about', keywords: 'version doctor help', summary: () => version, open: () => about.open() });
function openSettings(at?: string): void {
  void namesSettings.peek().then(() => panel.refresh());
  panel.open(at);
}
settingsBtn.addEventListener('click', () => openSettings());
// Back from signing in to a service: Settings, at that connection, to confirm it.
// #show=skill:<id> opens at that skill's card (nothing links there yet).
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
  // A first run offers a folder later on: read the user's places now, so it is ready.
  if (!ob.returning) void loadPlaces();
  render();
  focusInput();
}

// The Desktop and Documents, as the core finds them, for the folder step.
let obPlaces: readonly Offer[] = [];

async function loadPlaces(): Promise<void> {
  // Tried twice: a slow first answer should not cost the user the quick replies.
  for (let tries = 0; tries < 2; tries++) {
    try {
      const r = await api('POST', '/api/folders/browse', { place: 'places' });
      const frame = r.ok ? parseFrame(await r.json()) : null;
      if (frame) {
        obPlaces = folderOffers(frame.places ?? []);
        break;
      }
    } catch {
      // Tried again below, once.
    }
    if (tries === 0) await new Promise((done) => setTimeout(done, 1000));
  }
  // Read after she already asked for a folder: the buttons appear now.
  if (!ob) return;
  const next = placesFound(ob, obPlaces);
  if (next !== ob) {
    ob = next;
    render();
  }
}

/** The folder the user tapped, shared as Settings shares one: their own act, with change access. */
async function shareFolder(): Promise<void> {
  const place = ob?.sharing;
  if (!ob || !place) return;
  const error = await shares.save({ path: place.path, access: 'change' });
  if (!ob) return;
  ob = sharedFolder(ob, error, ready());
  render();
  after();
  if (active(ob)) focusInput();
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
  // The ladder may reach the folder step: know first whether one is shared (a `--grant`
  // run has one), so nobody who has a folder is asked for one.
  if (!code && !ob.returning && shares.last === null) await shares.load();
  if (!ob) return;
  ob = code ? failed(ob, code, suggestion) : saved(ob, ready());
  render();
  // A timezone the browser reported but this machine does not know is not the user's
  // to fix: it is saved again without it (or with the nearest real one), at once.
  if (ob.step === 'saving') void finish();
  else after();
}

const ready = (): Ready => ({
  model: view.model,
  speech: view.speech !== null,
  listening: view.listening !== null,
  // A folder her own turns reach: shared with everyone, and there. Null until the shares are read.
  folder: shares.last ? shares.last.shares.some((s) => s.scope === 'everyone' && !s.state) : null,
  places: obPlaces,
});

// A folder shared in Settings, opened from her folder step: she moves on. A share that
// turns up any other way is not the user's answer, so she does not claim it was.
let folderSettingsOpened = false;
shares.onChange(() => {
  if (ob?.step !== 'folder' || !folderSettingsOpened) return;
  const next = configured(ob, ready());
  if (next === ob) return;
  ob = next;
  render();
  after();
});

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
  else if (step === 'folder') {
    folderSettingsOpened = true;
    void folders.open();
  }
}

function reply(text: string): void {
  if (!ob || !active(ob)) return;
  input.value = '';
  input.style.height = '';
  if (climbing(ob)) {
    const next = text === OPEN ? ob : decide(ob, text, ready());
    // Anything that is not "skip" means "show me": the settings for this step open.
    if (next === ob) openFor(ob.step);
    const tapped = next !== ob && next.step === 'sharing';
    ob = next;
    render();
    if (tapped) void shareFolder();
    after();
    if (active(ob)) focusInput();
    return;
  }
  ob = answer(ob, text);
  render();
  if (ob.step === 'saving') void finish();
  else if (ob.step === 'skipped') {
    void api('POST', '/api/settings/persona/skip', {}).catch(() => undefined);
    after();
  }
  if (active(ob)) focusInput();
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

/** A ready line's Open: the conversation its routine built. */
function openReady(id: string): void {
  if (id !== view.conversationId) switchTo(id);
  else focusInput();
}

/** Open another conversation, or (null) a fresh one: reconnect and the core resumes it. */
function switchTo(id: string | null): void {
  // Somewhere else now: an ask waiting for another conversation is dropped.
  askQueue.drop();
  // Opened, so no longer unread.
  if (id) threads.markRead(id);
  // She stops first, so nothing of this conversation plays over the next one. Only a
  // reply under way needs the core told; the rest is this page's to quiet.
  // The user went somewhere themselves: a day.open still waiting is dropped.
  cancelDayOpen();
  day.cancel();
  if (view.busy) send({ type: 'interrupt' });
  quiet();
  store(CONVERSATION_KEY, id);
  approvals.reset();
  if (!active(ob)) ob = null;
  obAt = 0;
  apply({ ...INITIAL, link: 'connecting' });
  const old = socket;
  socket = null;
  old?.close();
  connect();
  focusInput();
}

newBtn.addEventListener('click', () => {
  threads.close();
  switchTo(null);
});

// ── Thread list ──────────────────────────────────────────────────────────

/** The body of a conversations call that answered, or a throw for one that did not. */
async function okJson(r: Promise<Response>): Promise<unknown> {
  const res = await r;
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
const succeeded = (r: Promise<Response>): Promise<boolean> => r.then((res) => res.ok).catch(() => false);
const threadPath = (id: string): string => `/api/conversations/${encodeURIComponent(id)}`;

// Your day (SUR-1): the entry and her `day_open` both come here; the build waits for the day's board.
const day = createDayOpener({
  fetchDay: () => okJson(api('GET', '/api/conversations/day')),
  current: () => view.conversationId,
  switchTo: (id) => switchTo(id),
  build: (spoken) => send(spoken ? { type: 'day.build', spoken: true } : { type: 'day.build' }),
});

// A `day.open` waiting for her to finish; a newer one replaces it.
let cancelDayOpen: () => void = () => undefined;

// The day conversation Home's button opened, until its board has widgets to show.
let homeDay: string | null = null;
/** Home's Build my briefing, or Open today's briefing: Your day, as its row opens it (D9). */
async function buildFromHome(): Promise<void> {
  threads.close();
  const result = await day.open();
  const id = day.id;
  if (result !== 'opened' || id === null) {
    // Nothing is coming: the button rests again.
    homeDay = null;
    home.built();
    void home.refresh();
    return;
  }
  homeDay = id;
  // Already there (no switch, so no new board): show it now if it is built.
  if (view.conversationId === id) homeTakesBoard();
}
/**
 * The day's board, once it has widgets, replaces Home: opened again if she had put it away.
 * Until then Home stays up while she builds it. True when this opened the board itself.
 */
function homeTakesBoard(): boolean {
  const step = dayBoardStep(homeDay, view.conversationId, view.board, workspace.showing);
  if (step === 'wait') return false;
  // Arrived, or went elsewhere: either way Build is no longer waiting.
  if (homeDay !== null) home.built();
  homeDay = null;
  if (step === 'forget' || step === 'shown') return false;
  // Never over Settings: the sidebar's chip says the board is there, as onBoard does.
  if (step === 'chip') {
    boardChip.hidden = false;
    return true;
  }
  openBoard();
  return true;
}

const threads = createThreads({
  root: threadsEl,
  fetchPage: ({ q, before }) => okJson(api('GET', listPath(q, before))),
  rename: (id, title) => succeeded(api('PATCH', threadPath(id), { title })),
  remove: (id) => succeeded(api('DELETE', threadPath(id), {})),
  restore: (id) => succeeded(api('POST', `${threadPath(id)}/restore`, {})),
  current: () => view.conversationId,
  pick: (id) => {
    if (id !== view.conversationId) switchTo(id);
    else focusInput();
  },
  deletedCurrent: () => switchTo(null),
  now: () => Date.now(),
  openDay: () => day.open(),
  dayId: () => day.id,
});
threads.onToggle((open) => {
  threadSwitch.setAttribute('aria-expanded', String(open));
  historyBtn.setAttribute('aria-expanded', String(open));
  historyBtn.toggleAttribute('data-on', open);
  // Closed from inside the list (Escape): focus goes back to what opened it.
  if (!open && threadsEl.contains(document.activeElement)) threadSwitch.focus();
  transcriptEl.hidden = open;
  threadsEl.hidden = !open;
});
threadSwitch.setAttribute('aria-controls', 'threads');
threadSwitch.title = `Conversations (${MAC ? '⌘⇧K' : 'Ctrl+Shift+K'})`;

function toggleThreads(): void {
  if (threads.isOpen) {
    threads.close();
    return;
  }
  // Showing the sidebar moves focus to its hide button; opening the list then takes it.
  sidebar.setHidden(false);
  // On a phone the list opens over the sheet at full height.
  sidebar.raise('full');
  threads.open();
}
threadSwitch.addEventListener('click', toggleThreads);
historyBtn.addEventListener('click', toggleThreads);
historyBtn.title = threadSwitch.title;

// ── Connection ───────────────────────────────────────────────────────────

function connect(): void {
  if (retryTimer !== null) clearTimeout(retryTimer);
  retryTimer = null;
  const token = takeToken();
  if (!token) {
    apply({ ...view, link: 'unauthenticated' });
    return;
  }
  const ws = new WebSocket(WS_URL);
  socket = ws;
  ws.addEventListener('open', () => {
    retryMs = 1000;
    // `host`: where the page runs, so the mic check leaves this app's own recording out.
    // `device`: this browser's own id, so a new device is told about (SEC-11); never an address.
    send({ type: 'hello', token, protocol: PROTOCOL, client: 'web', resume: store(CONVERSATION_KEY), host: pageHost(navigator, desktop.DESKTOP), device: deviceId() });
  });
  ws.binaryType = 'arraybuffer';
  ws.addEventListener('message', (ev) => {
    // A socket this page has moved on from (a switch, a reconnect) has nothing more to say.
    if (socket !== ws) return;
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
    // This conversation was deleted (here or in another tab): the core has closed it, so start a new one.
    if (msg.type === 'error' && msg['code'] === 'deleted') {
      switchTo(null);
      return;
    }
    // Let in (at start, after a reconnect, with a new token): what is connected now, for the cards' source tiles.
    if (msg.type === 'session') void connected.load();
    // The shares, read again: on every session (which also retries a failed first read),
    // and whenever the core says they changed, from Settings in another tab or a card.
    if (msg.type === 'session' || msg.type === 'shares') void shares.load();
    // A new connection starts unseen in the core, and may be another conversation: wait for its
    // board before saying the board is on a screen, so the old one is never reported seen.
    if (msg.type === 'session') {
      seen.reset();
      boardView.reset();
      day.session();
    }
    // She was asked about the day from a chat: the page opens it, as the entry does, once
    // her reply ("Opening your day.") is over, so the switch never cuts her off.
    if (msg.type === 'day.open') {
      // Asked for out loud: once her summary is heard, the microphone reopens.
      const spoken = msg['spoken'] === true;
      cancelDayOpen();
      cancelDayOpen = whenIdle(
        () => !view.busy && !voice.active,
        () => {
          cancelDayOpen = () => undefined;
          threads.close();
          // Asked for by her, not tapped: a failure is said in the conversation.
          void day.open({ spoken }).then((result) => {
            if (result === 'failed') apply({ ...view, entries: [...view.entries, { kind: 'notice', text: "Couldn't open your day." }] });
          });
        },
      );
    }
    helpers.handle(msg);
    memories.handle(msg);
    if (msg.type === 'conversation.title') threads.retitle(String(msg['id'] ?? ''), String(msg['title'] ?? ''));
    // A card is asked of the user: it is in the sidebar's transcript, so show the sidebar.
    // On the board when it can open there; the board opens once the card is in the view.
    // A helper's draft is pinned above the message box instead, never on the board.
    const draft = msg.type === 'confirm.request' && Boolean(msg['draft']);
    if (msg.type === 'confirm.request' && (draft || approvals.request(String(msg['pending_id'] ?? '')) === 'conversation')) {
      const reveal = revealForCard(isPhone(innerWidth), sidebar.hidden);
      if (reveal.unhide) sidebar.setHidden(false);
      if (reveal.raise) sidebar.raise(reveal.raise);
    }
    // At a spend limit the card must be seen too, as an approval's is: on a phone the sheet rises.
    if (spendReveals(msg)) {
      const reveal = revealForCard(isPhone(innerWidth), sidebar.hidden);
      if (reveal.unhide) sidebar.setHidden(false);
      if (reveal.raise) sidebar.raise(reveal.raise);
    }
    if (msg.type === 'confirm.resolved' && msg['status'] === 'succeeded') galaxy.flare(0.6);
    if (msg.type === 'reply.sentence') {
      voice.sentence(Number(msg['turn']), Number(msg['seq']), String(msg['text'] ?? ''));
    }
    if (msg.type === 'audio.end') voice.end(Number(msg['turn']));
    if (msg.type === 'audio.stop' || msg.type === 'interrupted') {
      // Stopping her also brings the volume back for the next reply. A watch streaming
      // to the core is the core's to end: it may be becoming the user's turn.
      voice.stop();
      overlapping = false;
      if (!streamWatch) stopWatching();
    }
    if (msg.type === 'overlap.duck') {
      // Words that may not be hers: she goes quiet while the core decides (VOI-9).
      voice.duck(true);
      overlapping = true;
      cutIn = true;
      render();
    }
    if (msg.type === 'overlap.over' && streamWatch) {
      voice.duck(false);
      overlapping = false;
      cutIn = false;
      const next = afterOver({ end: msg['end'] === true, pending: unwatchPending, canWatch: canWatch() });
      if (next === 'close') {
        // The watch is over without them: the microphone closes, unless it is the
        // user's turn by now.
        streamWatch = false;
        unwatchPending = false;
        endHold();
        if (!view.mic) {
          mic.stop();
          micMode = 'send';
        }
        render();
      } else if (next === 'stop') {
        // Asked to stop while the core decided (the switch turned off, the hold over).
        stopWatching();
      }
    } else if (msg.type === 'overlap.over') {
      // She carries on (or could not be stopped in time, which comes with `busy`):
      // full volume, and back to watching for the user, as before they spoke.
      voice.duck(false);
      if (overlapping && mic.on) {
        micMode = 'watch';
        detector.reset();
        preroll.drain();
        // Not started yet: it waits for her again. Finished while they spoke: the
        // watching ends as usual.
        if (!voice.active && awaitingHer) armHold();
        else if (!voice.active) stopWatchingSoon();
      }
      overlapping = false;
      // Answered: she carries on, and the microphone goes back to watching.
      cutIn = false;
    }
    if (msg.type === 'transcript.final') spokeLast = true;
    // The core opens and closes the microphone; the page only ever follows (VOI-15d).
    if (msg.type === 'mic.start') {
      endHold();
      // The watch became the user's turn: the same microphone carries on as it.
      streamWatch = false;
      unwatchPending = false;
      if (overlapping) {
        // It was an interruption: the microphone is already sending, so it carries on.
        // Whatever of her is still playing stops, if nothing has stopped it yet.
        voice.stop();
        overlapping = false;
      } else if (micMode === 'watch' && mic.on) {
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
    // The user cut in, and the core could not listen after all: busy, or nothing to
    // hear or answer with. The microphone must not go on sending into nothing.
    const stuck =
      msg.type === 'error' &&
      (bargeHeld !== null || overlapping || cutIn) &&
      ['busy', 'no_model', 'no_listening', 'routine_building'].includes(String(msg['code']));
    if (stuck && overlapping) {
      voice.duck(false);
      overlapping = false;
    }
    const hold =
      msg.type === 'mic.stop' &&
      holdForReply({
        reason: msg['reason'],
        micOn: mic.on,
        bargeOn,
        linkReady: view.link === 'ready',
        listening: view.listening !== null,
        coreVoice: view.speech?.where === 'core',
        onboarding: active(ob),
        cutIn: cutIn || overlapping || bargeHeld !== null,
      });
    if (hold) {
      holdForHer();
    } else if (msg.type === 'mic.stop' || (msg.type === 'error' && msg['code'] === 'listening') || stuck) {
      endHold();
      mic.stop();
      micMode = 'send';
      bargeHeld = null;
      streamWatch = false;
      unwatchPending = false;
    }
    if (msg.type === 'mic.start' || msg.type === 'mic.stop' || stuck) cutIn = false;
    // Whether a turn was running before this message, for an error that ends Home's build.
    const busyBefore = view.busy;
    if (msg.type === 'session' || msg.type.startsWith('spend.')) spendCards.handle(msg);
    // At her voice's cap the app's line may be said in the computer's own voice.
    speakCapped(msg, view.speech === null);
    if (msg.type === 'spend.resolved' || (msg.type === 'error' && msg['code'] === 'capped')) void refreshUsage();
    apply(reduce(view, msg));
    // + New helper, asked from a helper's chat: said in her conversation once it is open.
    if (msg.type === 'history') askQueue.opened();
    // A new helper (her card, or an offer's Create): its chat opens once she is done.
    createdChat.handle(msg);
    // A routine finished somewhere else: its conversation is marked until opened.
    const built = unreadFor(msg, view);
    if (built) threads.markUnread(built);
    if (msg.type === 'board') {
      seen.ready();
      if (!homeTakesBoard()) onBoard();
      seen.sync();
      day.board(view.board);
      if (!homeChecked) {
        homeChecked = true;
        openHomeOnce();
      }
    }
    // The build Home asked for ended without a board (refused, failed, or finished empty): its button rests again.
    if (endsBuild({ type: msg.type, code: msg['code'] }, homeDay, view.conversationId, busyBefore)) {
      homeDay = null;
      home.built();
      void home.refresh();
    }
    // Home's layout changed (here, in another tab, or by her): it reads again. Arranged by her, it
    // also opens to show the change, unless something else has the workspace.
    if (msg.type === 'home') {
      void home.changed();
      const shown = herOpensHome({
        by: msg['by'],
        open: workspace.isOpen,
        boardWidgets: view.board?.open === true && view.board.widgets.length > 0,
        approvals: approvals.holds(),
        folders: folderPanel.isOpen,
        onboarding: obDue || active(ob),
        kit: kit !== null,
        showcase: workspace.showing === 'showcase',
      });
      if (shown) showBoard();
    }
    // Her folder browser moved (or a click here did): the panel shows where it is now.
    if (msg.type === 'folders') {
      const frame = parseFrame(msg);
      if (frame) folderPanel.take(frame);
    }
    if (msg.type === 'board.refreshing') boardView.refreshing(String(msg['id'] ?? ''), msg['on'] === true);
    // The turn is over and she never made a sound (no words heard, a reply only
    // written, an error): the microphone held for her closes as after a reply.
    const over = msg.type === 'turn.complete' || (msg.type === 'state' && (view.orb === 'idle' || view.orb === 'error'));
    if (over && awaitingHer && !voice.active) {
      endHold();
      stopWatchingSoon();
    }
    if (view.draft !== null) takeDraft(view.draft);
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
    quiet();
    if (view.link === 'unauthenticated' || view.link === 'replaced') return;
    apply({ ...view, link: 'offline', busy: false, orb: 'idle' });
    retryTimer = setTimeout(connect, retryMs);
    retryMs = Math.min(retryMs * 2, 10_000);
  });
}

// ── Input ────────────────────────────────────────────────────────────────

/** A message the core refused as busy goes back in the box, unless something new is there. */
function takeDraft(text: string): void {
  if (!input.value.trim()) {
    input.value = text;
    input.dispatchEvent(new Event('input'));
  }
  apply({ ...view, draft: null });
  focusInput();
}

function submit(): void {
  const text = input.value.trim();
  if (ob && active(ob)) {
    if (text && view.link === 'ready') reply(text);
    return;
  }
  sendTurn(text, undefined, () => {
    input.value = '';
    input.style.height = '';
  });
}

/**
 * A typed turn, from the ask bar or an ask tapped on her board (`from: "ask"`, which the core
 * never lets approve anything). False when it cannot go now: nothing to say, she is busy, or the
 * link is down.
 */
function sendTurn(text: string, from?: 'ask', sending?: () => void): boolean {
  if (!text || view.busy || view.link !== 'ready') return false;
  voice.unlock(); // sending is a click or a key press, which is what lets a browser play sound
  voice.stop();
  spokeLast = false; // a typed conversation: the microphone stays closed while she answers
  stopWatching();
  send(from ? { type: 'turn.text', text, from } : { type: 'turn.text', text });
  sending?.();
  apply(sent(view, text));
  return true;
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
/** Everything of hers on this page goes still: her voice, the microphone, any cut-in under way. */
function quiet(): void {
  voice.stop();
  endHold();
  mic.stop();
  micMode = 'send';
  bargeHeld = null;
  overlapping = false;
  cutIn = false;
  streamWatch = false;
  unwatchPending = false;
}
const interrupt = (): void => {
  quiet();
  send({ type: 'interrupt' });
};
stopBtn.addEventListener('click', interrupt);
/**
 * Start listening, or, while listening, send what she heard. The Speak button and Space.
 * `held`: the talk key is being held, so only letting go ends the turn, never a pause.
 */
function talk(held = false): void {
  // The core is hearing them over her already, and would ignore a new start.
  if (overlapping) return;
  if (!view.listening) {
    void listeningSettings.open();
  } else if (view.mic) {
    // Done speaking: what she heard so far is the message.
    send({ type: 'listen.stop' });
  } else if (!view.busy && view.link === 'ready') {
    voice.unlock(); // a click is what lets a browser play her answer
    voice.stop();
    send(held ? { type: 'listen.start', held: true } : { type: 'listen.start' });
    // Opened here, inside the click or key press, so the permission prompt belongs to it.
    void mic.start();
  }
}
micBtn.addEventListener('click', () => talk());
/** What the talk shortcut can see of one key event. */
function keyOf(ev: KeyboardEvent): Key {
  const target = ev.target instanceof HTMLElement ? ev.target : null;
  return {
    code: ev.code,
    ctrl: ev.ctrlKey,
    alt: ev.altKey,
    shift: ev.shiftKey,
    meta: ev.metaKey,
    repeat: ev.repeat,
    ...contextOf(target, input, panel.isOpen),
  };
}

/** The desktop app has the talk shortcut: the page leaves that key to it, so it never counts twice. */
const shellHas = (k: Pick<Key, 'code' | 'ctrl' | 'alt' | 'shift' | 'meta'>): boolean =>
  talkGlobal === true && talkPrefs.hotkey !== null && matches(talkPrefs.hotkey, k);

/** Do what the talk shortcut asked. */
function onTalk(action: ReturnType<TalkKeys['down']>, ev?: KeyboardEvent): void {
  if (action === null || view.link !== 'ready' || active(ob)) return;
  ev?.preventDefault();
  if (action === 'toggle') talk();
  else if (action === 'start' && !view.mic) talk(true);
  else if (action === 'stop' && view.mic) send({ type: 'listen.stop' });
  else if (action === 'cancel' && view.mic) interrupt();
}

addEventListener('keydown', (ev) => {
  // Enter in a field (a limit, a raise, a search) is that field's own: never a page shortcut.
  if (ev.key === 'Enter' && enterInField(ev.target, input)) return;
  // One order for Escape (escapeAction). The thread list keeps only the Escapes it uses itself.
  if (ev.key === 'Escape') {
    const action = escapeAction({
      busy: view.busy,
      speaking: voice.active,
      listening: view.mic,
      listOpen: threads.isOpen,
      foldersOpen: folderPanel.isOpen,
      workspaceOpen: workspace.isOpen,
    });
    if (action === 'interrupt') interrupt();
    else if (action === 'list') {
      ev.preventDefault();
      threads.close();
    } else if (action === 'folders') {
      ev.preventDefault();
      folderPanel.escape();
    } else if (action === 'workspace') {
      ev.preventDefault();
      workspace.close();
    }
  }
  // The sidebar can come and go with Settings open; the workspace's left edge follows it.
  if (ev.key === '\\' && primary(ev)) {
    ev.preventDefault();
    sidebar.toggle();
    return;
  }
  // The usual way to Settings: ⌘, on a Mac, Ctrl+, elsewhere. Not the other one.
  if (ev.key === ',' && primary(ev) && !panel.isOpen) {
    ev.preventDefault();
    openSettings();
    return;
  }
  // ⌘⇧K: the list of conversations. Before the talk keys and plain ⌘K, so it does only this.
  if (ev.key.toLowerCase() === 'k' && primary(ev) && ev.shiftKey) {
    ev.preventDefault();
    toggleThreads();
    return;
  }
  const key = keyOf(ev);
  if (!shellHas(key)) onTalk(talkKeys.down(key), ev);
  if (ev.key.toLowerCase() === 'k' && primary(ev)) {
    ev.preventDefault();
    focusInput();
  }
});
addEventListener('keyup', (ev) => {
  if (talkGlobal === true && ev.code === talkPrefs.hotkey?.code) return; // the shell reports its release
  onTalk(talkKeys.up({ code: ev.code }), ev);
});
// A key let go in another window never arrives; start fresh when the tab comes back. Not while the
// desktop app has the shortcut: it reports the release wherever focus is, and a hold must end.
addEventListener('blur', () => {
  if (talkGlobal !== true) talkKeys.use(talkPrefs);
});
// The desktop app's global shortcut: the same logic as a key here. With this window in front a
// field or Settings with focus still keeps it, as it would the key; from another app nothing here does.
desktop.onTalk((state) => {
  const target = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const ctx = document.hasFocus()
    ? contextOf(target, input, panel.isOpen)
    : { inMessageBox: false, messageEmpty: true, inOtherControl: false };
  onTalk(shellKeys.event(talkPrefs.hotkey, state, ctx));
});
setInterval(() => apply(expire(view, Date.now())), 15_000);

// Pasting the launch link into a tab that is already open changes only the fragment.
addEventListener('hashchange', () => {
  if (view.link === 'unauthenticated' && takeToken()) {
    apply({ ...view, link: 'connecting' });
    connect();
  }
  const scene = showcaseScene('', location.hash);
  if (scene) openShowcase(workspace, scene);
  showKit(isKit(location.hash));
});

// The widget style guide, over the galaxy. Its theme switch turns the galaxy too; leaving puts hers back.
let kit: Kit | null = null;
function showKit(on: boolean): void {
  if (on && !kit) {
    kit = createKit({
      setTheme: (t) => {
        document.documentElement.dataset['theme'] = t;
        galaxy.setTheme(t);
      },
      restoreTheme: () => applyTheme(themeChoice),
    });
    document.body.append(kit.el);
    kit.el.focus({ preventScroll: true });
  } else if (!on && kit) {
    kit.dispose();
    kit = null;
  }
}

render();
connect();
if (showcaseAt) openShowcase(workspace, showcaseAt);
if (kitAt) showKit(true);

// Her board (her visual channel) in the workspace. It opens when she puts something up, unless
// Settings is open: then it waits behind a chip in the sidebar.
const boardChip = $<HTMLButtonElement>('board-chip');
const boardView = createBoardView({
  // The empty workspace is Home, read afresh each time it arrives.
  home: () => {
    void home.show();
    return home.el;
  },
  onRemove: (id) => send({ type: 'board.remove', id }),
  onShowcase: () => openShowcase(workspace),
  onRefresh: (id) => send({ type: 'board.refresh', id }),
  approvals: approvals.strip.el,
  connected: () => connected.tiles(),
  // Exactly the ask's words, as the user's message. Hidden while it could not go (asksOff).
  onAsk: (text) => {
    // Onboarding's conversation is the page's own: a tap never cuts into it.
    if (ob && active(ob)) return;
    sendTurn(text, 'ask');
  },
});
// Whether her board is on a screen: live widgets refresh only then.
const seen = createSeen(
  (on, opened) => send(opened ? { type: 'board.seen', on, opened } : { type: 'board.seen', on }),
  () => workspace.isOpen && workspace.showing === 'board',
);
// From a connection's start until its first board: nothing is said.
workspace.onClose(() => seen.sync());
function showBoard(): void {
  boardDraws++;
  boardChip.hidden = true;
  const pinned = approvals.pinned();
  approvals.strip.draw(pinned.items);
  // Nothing to draw, so Home is drawn instead (board.ts's empty state).
  homeShown = !(view.board?.open && view.board.widgets.length) && pinned.items.length === 0;
  // A board she put away is not drawn under an approval that opened it.
  boardView.draw(view.board?.open ? view.board : null, {
    any: pinned.items.length > 0,
    waiting: pinned.waiting,
    from: view.agentName,
    asksOff: asksOff || pinned.waiting,
  });
  // Already showing: this only changes the title; the cards were redrawn in place.
  workspace.open(homeShown ? 'Home' : boardTitle(view.board), boardView.el, 'board');
  showWorkspaceState();
  // Shown now: the board was opened (a redraw of a board already shown changes nothing).
  seen.sync(true);
}
/** The user asked for the board: a saved one she had put away is open again, and she is told.
 * One with nothing on it shows the empty state and tells her nothing (the core would refuse). */
function openBoard(): void {
  if (buttonAction(view.board) === 'reopen') {
    send({ type: 'board.open' });
    apply(boardOpen(view, true));
  }
  showBoard();
}
function onBoard(): void {
  const board = view.board;
  if (workspace.showing === 'board') {
    // The Folders panel keeps the workspace up, over an empty board if need be, and Home stays
    // up until a board opens: a reconnect or a switch does not take it away.
    if (frameKeeps({ boardOpen: board?.open === true, approvals: approvals.holds(), folders: folderPanel.isOpen, homeShown })) showBoard();
    else workspace.close();
    return;
  }
  if (!board?.open) boardChip.hidden = true;
  else if (workspace.showing === 'settings') boardChip.hidden = false;
  else showBoard();
}
boardChip.addEventListener('click', openBoard);
function openHomeOnce(): void {
  const onboarding = obDue || active(ob);
  // The introduction comes first; Home waits for the next load.
  if (onboarding) return;
  const today = localDate(new Date());
  const on = savedHomeDaily();
  const open = shouldOpenHome(lastHomeDate(), today, on, onboarding, view.board?.open === true);
  if (on) saveHomeDate(today);
  // Nothing else has the stage: not a board, Settings, the sample board or the kit.
  if (open && !workspace.isOpen && !showcaseAt && !kit) showBoard();
}
workspace.onClose((id, replaced) => {
  // Settings opened from Home closed by itself: Home comes back.
  const back = returnsToHome(backToHome, id, replaced);
  if (id !== 'board') backToHome = false;
  // Put away: a build Home was waiting on no longer opens anything by itself.
  if (!replaced && !back && homeDay !== null) {
    homeDay = null;
    home.built();
  }
  if (back) {
    queueMicrotask(() => {
      if (!workspace.isOpen) showBoard();
    });
  }
  if (id === 'board') {
    boardView.dispose();
    approvals.closed(replaced);
  }
  // The user put it away (its close, Escape, the docked orb): she is told. Settings opened over it is not that.
  if (id === 'board' && !replaced && view.board?.open) {
    send({ type: 'board.close' });
    apply(boardOpen(view, false));
  }
  // Whatever was opened over a board that is still up has closed: the board comes back. After this
  // close has finished, so the workspace hands focus back before opening again.
  if (id !== 'board' && !replaced && (view.board?.open || approvals.holds())) {
    queueMicrotask(() => {
      if (!workspace.isOpen && (view.board?.open || approvals.holds())) showBoard();
    });
  }
});

// Her helpers (the helpers panel spec): the panel opens in the workspace, from the
// rail's menus and from a helper's name in the chat.
const helpers = createHelpers({
  api,
  // The page's one polite region: what needs you, and what failed.
  announce: (text) => {
    announceEl.textContent = text;
  },
  open: (id) => openConversation(id),
  openStep: (stepId) => openStep(stepId),
  now: () => Date.now(),
  phone: () => isPhone(innerWidth),
  capName: () => capNameOf(usage?.providers),
  newHelper: (text) => askForHelper(text),
});
// The rail (the your helpers spec): her picture, one per helper of the user's, and +.
const rail = createRail($('rail'), {
  api,
  announce: (text) => {
    announceEl.textContent = text;
  },
  open: (id) => openConversation(id),
  her: () => backToHer(),
  newHelper: (text) => askForHelper(text),
  details: (id) => showHelper(id),
  panel: () => showHelpers(),
  update: (list) => helpers.update(list),
  now: () => Date.now(),
  capName: () => capNameOf(usage?.providers),
});
// A helper's chat may only now be known for one: its head is drawn, and its hello asked.
helpers.onCount(() => {
  rail.update(helpers.list);
  render();
});
function helpersShowing(): boolean {
  return workspace.isOpen && workspace.showing === 'helpers';
}
/** Her picture's menu: the panel, with her routines, timed asks and watchers. */
function showHelpers(): void {
  if (!helpersShowing()) workspace.open('Helpers', helpers.element, 'helpers');
  void helpers.open().then(() => helpers.focus());
}
workspace.onClose((id) => {
  if (id === 'helpers') helpers.closed();
});
/** A helper's name in the chat, or Details in its menu: the panel opens at its details. */
function showHelper(id: string): void {
  if (!helpersShowing()) workspace.open('Helpers', helpers.element, 'helpers');
  void helpers.showHelper(id);
}
/** Her picture: back to her last conversation that is not a helper's, or a new one. */
function backToHer(): void {
  const here = view.conversationId;
  if (here !== null && helpers.ownFor(here) === null) {
    openConversation(here);
    return;
  }
  if (here === null) {
    // Already hers, not yet begun: only the sidebar comes forward.
    workspace.close();
    threads.close();
    sidebar.setHidden(false);
    if (isPhone(innerWidth)) sidebar.raise('full');
    focusInput();
    return;
  }
  const helperChats = new Set(visited.filter((id) => helpers.ownFor(id) !== null));
  openConversation(askTarget(visited, helperChats));
}
/** The top of a helper's own chat: its picture and name. Its hello is asked once it is open. */
const chatHeadEl = document.createElement('div');
chatHeadEl.className = 'helper-head-slot';
chatHeadEl.hidden = true;
transcriptEl.before(chatHeadEl);
let chatHeadKey = '';
const hello = helloOnce(send);
chatHeadOn = true;
drawChatHead();
/** The user's own helper whose chat is open, once the panel exists to say. */
function ownHere(): Helper | null {
  return chatHeadOn && view.link === 'ready' ? helpers.ownFor(view.conversationId) : null;
}
function drawChatHead(): void {
  if (!chatHeadOn) return;
  const h = ownHere();
  const look = h ? lookOf(h.icon, h.color) : null;
  const conns = h?.connections ?? [];
  const key = h && look ? [h.id, h.name, h.job, look.icon, look.color, ...conns.map((c) => `${c.id}:${c.connected}`)].join('|') : '';
  if (key !== chatHeadKey) {
    chatHeadKey = key;
    chatHeadEl.replaceChildren(...(h && look ? [chatHead(h.name, look, h.job, conns, (id) => openStep(`connect:${id}`))] : []));
    chatHeadEl.hidden = !key;
  }
  if (h) hello(h.id);
}

/**
 * + New helper: the sentence goes to her as the user's message, in her conversation, the
 * same as typing it, and the panel closes so her card is seen. From a helper's chat, her
 * last conversation opens first (a new one when there is none).
 */
function askForHelper(text: string): void {
  const words = `New helper: ${text}`;
  const helperChats = new Set(visited.filter((id) => helpers.ownFor(id) !== null));
  if (view.conversationId && helperChats.has(view.conversationId)) {
    openConversation(askTarget(visited, helperChats));
    askQueue.queue(words);
    return;
  }
  workspace.close();
  threads.close();
  sidebar.setHidden(false);
  if (isPhone(innerWidth)) sidebar.raise('full');
  if (!sendTurn(words)) keepDraft(words);
}

/** Straight to the connection a step needs, as Home's Connect goes; else Connected tools. */
function openStep(stepId: string): void {
  workspace.close();
  openSettings('connected');
  const target = stepTarget(stepId);
  if (target) void connected.start(target);
}

// A new helper's chat opens once she is idle, never cutting her off.
const createdChat = createdOpener({
  idle: () => !view.busy && !voice.active,
  open: (id) => openConversation(id),
});

/** Words that could not go now wait in the message box, never lost. */
function keepDraft(words: string): void {
  input.value = words;
  focusInput();
}

/** Open a conversation the way History does: the sidebar shows it, the panel gives way. */
function openConversation(id: string | null): void {
  workspace.close();
  threads.close();
  sidebar.setHidden(false);
  if (isPhone(innerWidth)) sidebar.raise('full');
  if (id === null || id !== view.conversationId) switchTo(id);
  else focusInput();
}

// One click to the workspace and its board, and one to close it again.
const workspaceBtn = $<HTMLButtonElement>('workspace-btn');
workspaceBtn.replaceChildren(icon('workspace', 18));
function showWorkspaceState(): void {
  const on = workspace.isOpen && (workspace.showing === 'board' || workspace.showing === 'showcase');
  workspaceBtn.toggleAttribute('data-on', on);
  workspaceBtn.setAttribute('aria-pressed', String(on));
}
workspaceBtn.addEventListener('click', () => {
  if (workspace.isOpen && (workspace.showing === 'board' || workspace.showing === 'showcase')) workspace.close();
  else openBoard();
  showWorkspaceState();
});
workspace.onClose(showWorkspaceState);
