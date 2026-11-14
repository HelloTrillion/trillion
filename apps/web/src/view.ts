/**
 * What the screen shows, as a pure function of the messages the core sent.
 * No DOM and no socket here, so every transition can be tested.
 */

import { type Look, lookOf } from './look';
import { isRoastTurn } from './roast';

/** What the core saves for a reply cut off before a word of it was heard (VOI-9). */
const INTERRUPTED_MARK = '[interrupted]';

export type Orb = 'idle' | 'arming' | 'listening' | 'processing' | 'speaking' | 'error';

/** Why an action needs the user's OK, as the core classes it. */
export type Reason = 'irreversible' | 'spend' | 'write' | 'read';
const REASONS: readonly Reason[] = ['irreversible', 'spend', 'write', 'read'];

/** The host's name for a tool, or its own name made readable when the core sent none. */
export function labelOf(label: unknown, tool: unknown): string {
  if (typeof label === 'string' && label.trim()) return label.trim();
  const words = typeof tool === 'string' ? tool.replaceAll('_', ' ').trim() : '';
  return words ? words[0]!.toUpperCase() + words.slice(1) : 'An action';
}

export type CardStatus = 'waiting' | 'working' | 'done' | 'declined' | 'failed' | 'unknown' | 'closed';

/** What a reminder or timed ask left: her words, or a missed or failed one with none. */
export type NoticeKind = 'reminder' | 'ask' | 'missed' | 'failed' | 'ready' | 'heads_up';

export type Entry =
  // `seen`: she chose to say nothing to it, as to a sign-off (VOI-7).
  | { readonly kind: 'user'; readonly text: string; readonly seen?: boolean }
  /** `roast`: a reply to Roast my week, being written, then done and ready to share. */
  | { readonly kind: 'assistant'; readonly text: string; readonly interrupted: boolean; readonly roast?: 'writing' | 'done' }
  /** `label`: the host's friendly name for a carded tool, from its card, once the card has come. */
  | { readonly kind: 'tool'; readonly callId: string; readonly name: string; readonly outcome: string | null; readonly ok: boolean; readonly label?: string }
  | {
      readonly kind: 'card';
      readonly pendingId: string;
      readonly summary: string;
      /** Everything it sends or writes, exactly as it will go (SPN-2a); absent when the summary says it all. */
      readonly detail?: string;
      readonly expiresAt: number;
      readonly tapOnly: boolean;
      readonly status: CardStatus;
      readonly note: string;
      /** The host's friendly name for the tool ("Send a message"). */
      readonly toolLabel: string;
      /** What it says when its time runs out, when the core says (a browsing helper's
       * card: nothing is sent and she doesn't ask again). */
      readonly expiredNote?: string;
      /** Why it needs the user, from the action's risk; null when the core did not say. */
      readonly reason: Reason | null;
      /** A new helper's picture, on its create card; absent on any other. */
      readonly look?: Look | null;
      /** A new helper's draft, on its create card: pinned above the message box. */
      readonly draft?: HelperDraft;
    }
  /** The app's line; her browsing helper's carries what it waits on, to answer here. */
  | { readonly kind: 'notice'; readonly text: string; readonly ask?: BrowserAsk }
  /**
   * Her offer of a helper (the your helpers spec, Suggestions): Create or No thanks, then
   * what came of it. This page's only: never saved, never drawn again from history.
   */
  | {
      readonly kind: 'offer';
      readonly area: string;
      readonly name: string;
      readonly job: string;
      readonly status: OfferStatus;
      /** Why it could not be made, in plain words; empty otherwise. */
      readonly note: string;
    }
  /** A line the app wrote ("Building your day"): never the user's words, never hers. */
  /** A routine's ready line, kept as the app's, carries the conversation it opens. */
  | { readonly kind: 'host'; readonly text: string; readonly conversationId?: string }
  | {
      readonly kind: 'scheduled';
      readonly id: string;
      readonly what: NoticeKind;
      readonly title: string;
      /** Her words. Null for missed and failed: those are shown, never spoken. */
      readonly text: string | null;
      readonly dueMs: number;
      /** When it was due, in words, in the schedule's own zone: "4:00 pm", "Fri 4:00 pm". */
      readonly due: string;
      readonly late: boolean;
      /** The conversation a routine's ready line opens; null for any other notice. */
      readonly conversationId: string | null;
      /** An https link it opens, such as a meeting's Join; null when none. */
      readonly link: string | null;
      /** The host's own short line, for the page only: never spoken (speaking up D3). */
      readonly detail: string | null;
      /** Why she held her voice ("quiet hours"); null when she didn't. */
      readonly quiet: string | null;
      readonly spoken: boolean;
      /** The helper it came from, named as the Helpers panel names it; null when no helper sent it. */
      readonly helper?: { readonly id: string; readonly name: string; readonly look?: Look } | null;
      /** A security alert (SEC-11): marked as one. */
      readonly critical?: boolean;
    };

/** A Connected tools entry a helper's draft suggests, and whether it is set up now. */
export interface DraftConnection {
  readonly id: string;
  readonly title: string;
  readonly connected: boolean;
}

/** What a helper's create card drafted (the your helpers spec, Creating one). */
export interface HelperDraft {
  readonly name: string;
  readonly job: string;
  readonly notes: string;
  readonly connectors: readonly DraftConnection[];
  readonly asks: readonly string[];
}

/**
 * A browsing helper's chip, sign-in or step, on its line in the conversation (the her
 * browser spec): answered here as in her browser's window, through the same route, and
 * settled here whichever place answered. Never saved, never drawn again from history.
 */
export interface BrowserAsk {
  readonly task: string;
  readonly id: string;
  readonly kind: 'chip' | 'signin' | 'step';
  /** `sending`: an answer is on its way, so no second one goes. */
  readonly status: 'open' | 'sending' | 'settled';
  /** How it ended: the core's word ("allowed", "always", "expired"…), or "stale". */
  readonly answer: string;
  /** Why an answer didn't go, in plain words; empty otherwise. */
  readonly note: string;
}

const ASK_KINDS: readonly BrowserAsk['kind'][] = ['chip', 'signin', 'step'];

function askIn(raw: unknown): BrowserAsk | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const r = raw as Record<string, unknown>;
  const kind = ASK_KINDS.find((k) => k === r['kind']);
  const id = typeof r['id'] === 'string' ? r['id'] : '';
  const task = typeof r['task'] === 'string' ? r['task'] : '';
  // Only ids the routes take: nothing else is ever put in a path.
  if (!kind || !/^[a-f0-9]{1,32}$/.test(id) || !/^[a-f0-9]{8,32}$/.test(task)) return undefined;
  return { task, id, kind, status: 'open', answer: '', note: '' };
}

/** The browsing helper's line for ask `id`, changed by `change`; the view as it was if none. */
function settleAsk(view: View, id: string, change: (a: BrowserAsk) => BrowserAsk): View {
  let changed = false;
  const entries = view.entries.map((e) => {
    if (e.kind !== 'notice' || e.ask?.id !== id) return e;
    const ask = change(e.ask);
    if (ask === e.ask) return e;
    changed = true;
    return { ...e, ask };
  });
  return changed ? { ...view, entries } : view;
}

/** The user pressed an answer on the line: it is on its way. */
export function askSending(view: View, id: string): View {
  return settleAsk(view, id, (a) => (a.status === 'open' ? { ...a, status: 'sending', note: '' } : a));
}

/**
 * What came of the answer the page sent: `answer` once taken, "stale" when it was no
 * longer waiting, or null when it didn't go (open again, with `note`). A line the core
 * already settled stays as the core said.
 */
export function askAnswered(view: View, id: string, answer: string | null, note = ''): View {
  return settleAsk(view, id, (a) => {
    if (a.status === 'settled') return a;
    return answer === null ? { ...a, status: 'open', note } : { ...a, status: 'settled', answer, note: '' };
  });
}

/** `past`: an older offer, once a newer one came: only the newest can be answered. */
export type OfferStatus = 'open' | 'past' | 'creating' | 'made' | 'declined' | 'failed';

export type Link = 'connecting' | 'ready' | 'unauthenticated' | 'offline' | 'replaced';

export interface Speech {
  readonly kind: string;
  readonly where: 'core' | 'client';
  readonly voice: string | null;
  readonly sample_rate: number | null;
}

/** How she hears, when she can. `streaming` means words appear while the user speaks;
 * `turns` means the hearing judges when they have finished, so a "yeah" over her
 * can be told from an interruption (VOI-9). */
export interface Listening {
  readonly kind: string;
  readonly streaming: boolean;
  readonly turns: boolean;
}

export type MicProblem = 'denied' | 'no_device' | 'unsupported' | 'failed' | 'silent';

export interface View {
  readonly link: Link;
  readonly conversationId: string | null;
  readonly agentName: string;
  readonly model: string | null;
  /** The conversation's title, once it has one. */
  readonly title: string | null;
  readonly speech: Speech | null;
  readonly listening: Listening | null;
  /** The core has the microphone open for this page. */
  readonly mic: boolean;
  /** What she has made out so far, while the user is still speaking. */
  readonly hearing: string;
  readonly grantedRoots: readonly string[];
  readonly orb: Orb;
  /** Her "speaking" is a notice's: the user may still send, and she gives way. */
  readonly noticeSpeaking: boolean;
  readonly busy: boolean;
  /** True while a reply is streaming: the next piece of text belongs to it. */
  readonly replyOpen: boolean;
  readonly entries: readonly Entry[];
  /** Notices that arrived during a reply. They join the conversation when it ends (VOI-17). */
  readonly held: readonly Entry[];
  /** A typed message sent and not yet taken up: put back if the core says it is busy. */
  readonly pending: string | null;
  /** Text for the message box, once: a refused message, never lost. The page clears it. */
  readonly draft: string | null;
  readonly banner: string | null;
  /** While a spend cap has her on something else until the 1st: "Using Gemma until Dec 1". */
  readonly switches: readonly { readonly provider_id: string; readonly line: string }[];
  /** Stopped at a spend limit the user set: not a failure, so the orb stays calm. */
  readonly capped: boolean;
  /** Her board for this conversation (the workspace's widgets), or null when she has made none. */
  readonly board: Board | null;
  /** Reads of this conversation still running in the background, as the core last counted them. */
  readonly jobs: number;
}

export const INITIAL: View = {
  link: 'connecting',
  conversationId: null,
  agentName: 'Trillion',
  model: null,
  title: null,
  speech: null,
  listening: null,
  mic: false,
  hearing: '',
  grantedRoots: [],
  orb: 'idle',
  noticeSpeaking: false,
  busy: false,
  replyOpen: false,
  entries: [],
  held: [],
  pending: null,
  draft: null,
  banner: null,
  switches: [],
  capped: false,
  board: null,
  jobs: 0,
};

export type WidgetSize = 'small' | 'medium' | 'wide' | 'full';

/** One widget as the core sent it. The core validated it; the page still reads every field defensively. */
export interface Widget {
  readonly id: string;
  readonly type: string;
  readonly size: WidgetSize;
  readonly [field: string]: unknown;
}

export interface Board {
  readonly title: string;
  readonly open: boolean;
  readonly widgets: readonly Widget[];
}

const SIZES: readonly WidgetSize[] = ['small', 'medium', 'wide', 'full'];

/** A board from a `board` message: null for none or for anything that is not one. */
export function boardOf(raw: unknown): Board | null {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null;
  const r = raw as Record<string, unknown>;
  const widgets: Widget[] = [];
  for (const w of Array.isArray(r['widgets']) ? (r['widgets'] as unknown[]) : []) {
    if (typeof w !== 'object' || w === null || Array.isArray(w)) continue;
    const f = w as Record<string, unknown>;
    if (typeof f['id'] !== 'string' || !f['id'] || typeof f['type'] !== 'string') continue;
    const size = SIZES.find((x) => x === f['size']) ?? 'medium';
    widgets.push({ ...f, id: f['id'], type: f['type'], size });
  }
  return { title: typeof r['title'] === 'string' ? r['title'] : '', open: r['open'] === true, widgets };
}

/** The user opened or put away the board here: the page follows at once, before the core echoes it. */
export function boardOpen(view: View, open: boolean): View {
  return view.board && view.board.open !== open ? { ...view, board: { ...view.board, open } } : view;
}

export type ServerMsg = { readonly type: string } & Readonly<Record<string, unknown>>;

const ORBS: readonly Orb[] = ['idle', 'arming', 'listening', 'processing', 'speaking', 'error'];

/** The note on a card whose time ran out, here or in the core. */
export const EXPIRED_NOTE = 'This expired before it was answered. Ask again.';

const REFUSALS: Readonly<Record<string, string>> = {
  expired: EXPIRED_NOTE,
  precondition_changed: 'The target changed since you were asked, so nothing ran.',
  superseded: 'A newer request replaced this one.',
  wrong_session: 'This belongs to another session.',
  not_found: 'This request no longer exists.',
  denied_by_floor: 'That location is protected.',
};

const ERRORS: Readonly<Record<string, string>> = {
  no_model: 'No model is set up yet, so there is nobody to answer. Open Model settings.',
  no_listening: 'She cannot hear yet. Open Settings, under Hearing, and choose how.',
  busy: 'Still answering the last message.',
  routine_building: "She's building this conversation's routine right now. Try again in a moment.",
  bad_message: 'That message could not be sent.',
  capped: 'A spending cap was reached, so nothing more was sent.',
  internal: 'Something failed inside Trillion. The log has the details.',
  unknown_type: 'This page and Trillion may be different versions: Trillion did not understand what the page sent. Restart Trillion, then reload the page.',
};

/** An error with no words for it: the app says so as the app, naming the code (PER-11). */
function unknownError(code: string): string {
  return `Trillion reported an error this app has no text for (${code || 'no code'}). The log has the details.`;
}

const PROVIDER_ERRORS: Readonly<Record<string, string>> = {
  unauthorized: 'The model provider rejected the key.',
  rate_limited: 'The model provider is rate-limiting requests.',
  unreachable: 'The model provider could not be reached.',
  timeout: 'The model provider took too long to answer.',
  not_found: 'The model provider does not know that model.',
  provider_unavailable: 'The model provider is having trouble.',
};

const LISTEN_ERRORS: Readonly<Record<string, string>> = {
  inaudible: 'I can\'t hear your microphone. Check it is not muted and that the right one is selected.',
  unauthorized: 'The listening provider rejected the key.',
  payment_required: 'The listening provider says the account is out of credit.',
  rate_limited: 'The listening provider is rate-limiting requests.',
  unreachable: 'The listening provider could not be reached.',
  timeout: 'The listening provider took too long to answer.',
  provider_unavailable: 'The listening provider is having trouble.',
  not_installed: 'Listening on this computer is not installed. Open Settings, under Hearing.',
  needs_download: 'The listening model has not been downloaded. Open Settings, under Hearing.',
};

const MIC_PROBLEMS: Readonly<Record<MicProblem, string>> = {
  denied: 'The browser did not allow the microphone. Allow it for this page, then try again.',
  no_device: 'No microphone was found.',
  unsupported: 'This browser cannot capture a microphone here.',
  failed: 'The microphone could not be opened.',
  silent: 'Your microphone isn’t sending any sound. Check it in Settings, under Hearing.',
};

// Her tool for saying nothing (VOI-7): a silence she chose, never drawn as a tool.
export const QUIET_TOOL = 'stay_quiet';

const STOPS: Readonly<Record<string, string>> = {
  unheard: 'I heard something, but could not make out any words.',
  empty: 'The model sent back nothing. Try again, or rephrase.',
  refusal: 'The model declined to answer that.',
  max_tokens: 'The reply was cut off at the length limit.',
  rounds: 'Stopped: too many tool rounds in one turn.',
  wall_clock: 'Stopped: the turn ran too long.',
  cost: 'Stopped: the turn reached its cost ceiling.',
};

function listeningOf(raw: unknown): Listening | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const r = raw as Record<string, unknown>;
  return typeof r['kind'] === 'string'
    ? { kind: r['kind'], streaming: r['streaming'] === true, turns: r['turns'] === true }
    : null;
}

function speechOf(raw: unknown): Speech | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const r = raw as Record<string, unknown>;
  if (typeof r['kind'] !== 'string' || (r['where'] !== 'core' && r['where'] !== 'client')) return null;
  return {
    kind: r['kind'],
    where: r['where'],
    voice: typeof r['voice'] === 'string' ? r['voice'] : null,
    sample_rate: typeof r['sample_rate'] === 'number' ? r['sample_rate'] : null,
  };
}

const str = (v: unknown, fallback = ''): string => (typeof v === 'string' ? v : fallback);

const NOTICE_KINDS: readonly NoticeKind[] = ['reminder', 'ask', 'missed', 'failed', 'ready', 'heads_up'];

function scheduledOf(msg: ServerMsg): Entry | null {
  const what = NOTICE_KINDS.find((k) => k === msg['kind']);
  const id = str(msg['id']);
  if (!what || !id) return null;
  // Only she writes words; a missed or failed one never carries any (D7). A ready line's
  // words are the host's, kept so it reads as it was spoken.
  const words = what === 'missed' || what === 'failed' ? '' : str(msg['text']);
  const dueMs = typeof msg['due_ms'] === 'number' ? msg['due_ms'] : 0;
  return {
    kind: 'scheduled',
    id,
    what,
    title: str(msg['title']),
    text: words || null,
    dueMs,
    // The core's words, in the zone the user set it in. This browser's clock only if none.
    due: str(msg['due']) || localClock(dueMs),
    late: msg['late'] === true,
    conversationId: what === 'ready' ? str(msg['conversation_id']) || null : null,
    link: httpsLink(msg['link']),
    detail: str(msg['detail']) || null,
    quiet: str(msg['quiet']) || null,
    // A core from before speaking up never said: then she spoke it.
    spoken: msg['spoken'] !== false,
    ...(msg['critical'] === true ? { critical: true } : {}),
    // Only from a helper: a reminder, a spend notice or any other carries none.
    // One of the user's own carries its picture too.
    ...(str(msg['helper_id']) && str(msg['helper_name'])
      ? {
          helper: {
            id: str(msg['helper_id']),
            name: str(msg['helper_name']),
            ...(str(msg['helper_icon']) ? { look: lookOf(msg['helper_icon'], msg['helper_color']) } : {}),
          },
        }
      : {}),
  };
}

/** Only an https link is ever opened from a notice: the server says so too, and this page checks again. */
export function httpsLink(raw: unknown): string | null {
  if (typeof raw !== 'string' || !raw) return null;
  try {
    return new URL(raw).protocol === 'https:' ? raw : null;
  } catch {
    return null;
  }
}

function hostOf(r: Record<string, unknown>): Entry {
  const opens = str(r['conversation_id']);
  return opens ? { kind: 'host', text: str(r['text']), conversationId: opens } : { kind: 'host', text: str(r['text']) };
}

/** The conversation to mark unread for a ready notice: never the one open, which the user is looking at. */
export function unreadFor(msg: ServerMsg, view: Pick<View, 'conversationId'>): string | null {
  const id = readyConversation(msg);
  return id && id !== view.conversationId ? id : null;
}

/** The conversation a routine's ready notice says is ready; null for any other message. */
export function readyConversation(msg: ServerMsg): string | null {
  if (msg.type !== 'notice' || msg['code'] !== 'schedule' || msg['kind'] !== 'ready') return null;
  return str(msg['conversation_id']) || null;
}

// Refused, not taken: what was typed goes back in the box.
const REFUSED: ReadonlySet<string> = new Set(['busy', 'routine_building']);

/** A reply is over: what waited for it joins the conversation, in order. */
/** A roast still being written is done once her turn is over: only then can it be shared. */
function roastsDone(entries: readonly Entry[]): readonly Entry[] {
  return entries.some((e) => e.kind === 'assistant' && e.roast === 'writing')
    ? entries.map((e) => (e.kind === 'assistant' && e.roast === 'writing' ? { ...e, roast: 'done' } : e))
    : entries;
}

function release(view: View): View {
  return view.held.length ? { ...view, entries: [...view.entries, ...view.held], held: [] } : view;
}

/** Busy means she is making a reply. Saying a notice is not: the user may cut in. */
export function busyOf(orb: Orb, notice: boolean): boolean {
  return orb === 'processing' || (orb === 'speaking' && !notice);
}

/** "4:00 pm" by this browser's clock: only for a notice the core gave no words for. */
export function localClock(ms: number, timeZone?: string): string {
  const time = new Date(ms).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', ...(timeZone ? { timeZone } : {}) });
  return time.replace(/[\u00a0\u202f]/g, ' ').toLowerCase();
}

/** "due Fri 4:00 pm". */
export function dueLabel(due: string): string {
  return `due ${due}`;
}

/** The quiet line for a reminder or timed ask that has no words of hers. */
export function quietLine(e: { readonly what: NoticeKind; readonly title: string; readonly due: string }): string {
  const due = dueLabel(e.due);
  return e.what === 'missed' ? `Missed: ${e.title} (${due})` : `Didn't finish: ${e.title} (${due})`;
}

function replaceLast(entries: readonly Entry[], match: (e: Entry) => boolean, next: (e: Entry) => Entry): Entry[] {
  const out = entries.slice();
  for (let i = out.length - 1; i >= 0; i--) {
    const e = out[i];
    if (e !== undefined && match(e)) {
      out[i] = next(e);
      break;
    }
  }
  return out;
}

function cardOutcome(status: string, reason: string, detail: string): { status: CardStatus; note: string } {
  switch (status) {
    case 'succeeded':
      return { status: 'done', note: 'Done.' };
    case 'cancelled':
      return { status: 'declined', note: 'Declined. Nothing ran.' };
    case 'failed':
      return { status: 'failed', note: 'It failed. Nothing was changed.' };
    case 'unknown':
      return { status: 'unknown', note: 'It ran, but the result could not be confirmed.' };
    case 'in_progress':
      return { status: 'working', note: 'Working.' };
    default:
      // Never on screen, so not approved, and still open: it waits for a tap like any card.
      if (reason === 'not_shown') return { status: 'waiting', note: '' };
      return { status: 'closed', note: REFUSALS[reason] ?? (detail || 'This could not be approved.') };
  }
}

// What a memory tool did, in the user's words rather than the tool's name.
const TOOL_LABELS: Readonly<Record<string, readonly [working: string, done: string]>> = {
  memory_write: ['Remembering', 'Remembered'],
  memory_recall: ['Checking what she remembers', 'Checked what she remembers'],
  decision_record: ['Checking the record', 'Checked the record'],
  web_fetch: ['Reading the page', 'Read the page'],
  helper_create: ['Drafting a helper', 'Drafted a helper'],
  helper_ask: ['Asking a helper', 'Asked a helper'],
  helper_suggest: ['Thinking of a helper', 'Offered a helper'],
  helper_update: ['Changing a helper', 'Changed a helper'],
  helper_narrow: ['Noting that', 'Noted'],
  helper_widen: ['Drafting a change', 'Drafted a change'],
  week_roast: ['Reading your week', 'Read your week'],
};

const WAITING_OK = 'waiting for your OK';

/** The chip for one tool call: what it is doing, then how it ended. */
export function toolLabel(name: string, outcome: string | null, ok: boolean, label?: string): string {
  // She confirmed a card that was already settled: nothing ran, so no second success.
  if (outcome === 'already_resolved') return 'Already settled';
  // A card asks the user: said plainly, with the card's own name for it when known.
  if (outcome === 'confirmation_required') return label ? `${label}: ${WAITING_OK}` : 'Waiting for your OK';
  const words = TOOL_LABELS[name];
  if (outcome === null) return words ? `${words[0]} …` : `${name} …`;
  // Sent off to run while the user talks: it is not done, and nothing failed.
  if (outcome === 'background') return `${name}: working in background`;
  if (words && ok && outcome === 'succeeded') return words[1];
  return `${name}: ${outcome.replaceAll('_', ' ')}`;
}

/** The quiet line under the input while reads run in the background; empty at none. */
export function backgroundLine(count: number): string {
  if (count <= 0) return '';
  return count === 1 ? '1 read in background' : `${count} reads in background`;
}

/** A user message the page is about to send: shown at once, before the core echoes anything. */
export function sent(view: View, text: string): View {
  return { ...view, busy: true, replyOpen: false, banner: null, capped: false, pending: text, draft: null, entries: [...view.entries, { kind: 'user', text }] };
}

/** Drop the last user entry with this text: the one `sent` added. */
function unsent(entries: readonly Entry[], text: string): Entry[] {
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i];
    if (e?.kind === 'user' && e.text === text) return [...entries.slice(0, i), ...entries.slice(i + 1)];
  }
  return entries.slice();
}

/** A create card's draft as the core sent it, or null when it is not one. */
export function draftIn(raw: unknown): HelperDraft | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const name = str(r['name']);
  const job = str(r['job']);
  if (!name || !job) return null;
  const connectors = (Array.isArray(r['connectors']) ? r['connectors'] : []).flatMap((c): DraftConnection[] => {
    if (!c || typeof c !== 'object') return [];
    const x = c as Record<string, unknown>;
    const id = str(x['id']);
    return id ? [{ id, title: str(x['title']) || id, connected: x['connected'] === true }] : [];
  });
  const asks = (Array.isArray(r['asks']) ? r['asks'] : []).filter((a): a is string => typeof a === 'string' && a.trim() !== '');
  return { name, job, notes: str(r['notes']), connectors, asks };
}

/** The helper draft still waiting in a conversation: there is only ever one. */
export function pinnedDraft(entries: readonly Entry[]): Extract<Entry, { kind: 'card' }> | null {
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i];
    if (e?.kind === 'card' && e.draft && e.status === 'waiting') return e;
  }
  return null;
}

function lookIn(raw: unknown): Look | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as { icon?: unknown; color?: unknown };
  return lookOf(r.icon, r.color);
}

type OfferEntry = Extract<Entry, { kind: 'offer' }>;

/** Why an offer's Create made nothing, in a few plain words. */
export function offerFailed(reason: string, name: string): string {
  switch (reason) {
    case 'full':
      return 'You have 20 helpers, the most there can be. Delete one to make room.';
    case 'taken':
      return `There is already a helper called ${name}.`;
    case 'stale':
      return 'That offer has run out. Ask her again if you still want one.';
    case 'invalid':
      return "That name or job didn't work. Ask her to make it.";
    default:
      return "That didn't work. Try again in a moment.";
  }
}

function lastOffer(entries: readonly Entry[], match: (e: OfferEntry) => boolean): number {
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i];
    if (e?.kind === 'offer' && match(e)) return i;
  }
  return -1;
}

/** The offer of `area` whose Create was tapped, settled: made, or why not. Any other is left alone. */
function settleOffer(view: View, area: string, next: (e: OfferEntry) => OfferEntry): View {
  if (!area) return view;
  const at = lastOffer(view.entries, (e) => e.area === area && e.status === 'creating');
  if (at < 0) return view;
  const entries = view.entries.slice();
  entries[at] = next(entries[at] as OfferEntry);
  return { ...view, entries };
}

/** The user tapped Create or No thanks on the offer for `area`: No thanks is said at once. */
export function offerChosen(view: View, area: string, choice: 'create' | 'decline'): View {
  const at = lastOffer(view.entries, (e) => e.area === area && e.status === 'open');
  if (at < 0) return view;
  const entries = view.entries.slice();
  const e = entries[at] as OfferEntry;
  entries[at] = { ...e, status: choice === 'create' ? 'creating' : 'declined' };
  return { ...view, entries };
}

export function reduce(view: View, msg: ServerMsg): View {
  const next = step(view, msg);
  // Whatever waited for a reply joins the conversation once none is under way.
  return next.held.length && !next.busy && !next.replyOpen ? release(next) : next;
}

function step(view: View, msg: ServerMsg): View {
  switch (msg.type) {
    case 'conversation.title':
      return str(msg['id']) === view.conversationId ? { ...view, title: str(msg['title']) || null } : view;
    case 'session': {
      const model = msg['model'] as { provider?: unknown; model?: unknown } | null;
      return {
        ...view,
        link: 'ready',
        conversationId: str(msg['id']) || null,
        agentName: str(msg['agent_name'], view.agentName),
        model: model ? str(model.model) : null,
        title: typeof msg['title'] === 'string' ? msg['title'] : null,
        speech: speechOf(msg['speech']),
        listening: listeningOf(msg['listening']),
        mic: false,
        hearing: '',
        grantedRoots: Array.isArray(msg['granted_roots']) ? msg['granted_roots'].map((r) => String(r)) : [],
        busy: false,
        noticeSpeaking: false,
        held: [],
        pending: null,
        banner: null,
        // A switch in force is sent again after this, for any conversation.
        switches: [],
        capped: false,
        // Another conversation: its own board follows, if it has one, and its own count of reads.
        board: null,
        jobs: 0,
      };
    }
    case 'board':
      return { ...view, board: boardOf(msg['board']) };
    case 'spend.card':
      // A card standing for this conversation (after a reload too): the core sends one only
      // while its provider is still at the cap.
      return msg['status'] === 'open' || msg['status'] === 'stopped' ? { ...view, capped: true } : view;
    case 'spend.resolved':
      // Raised or switched: she can go on, so the calm "stopped" status goes.
      return msg['status'] === 'raised' || msg['status'] === 'switched' ? { ...view, capped: false } : view;
    case 'spend.switch': {
      // Each switch made at a spend cap, as the core words it; an empty list clears them.
      const list = Array.isArray(msg['switches']) ? (msg['switches'] as unknown[]) : [];
      const switches = list.flatMap((s) => {
        const o = s && typeof s === 'object' ? (s as Record<string, unknown>) : {};
        const line = str(o['line']);
        return line ? [{ provider_id: str(o['provider_id']), line }] : [];
      });
      return { ...view, switches };
    }
    case 'jobs': {
      const count = msg['count'];
      return { ...view, jobs: typeof count === 'number' && count > 0 ? Math.floor(count) : 0 };
    }
    case 'model': {
      const model = msg['model'] as { model?: unknown } | null;
      return { ...view, model: model ? str(model.model) : null, banner: null };
    }
    case 'identity':
      // The user renamed her. Every open page follows at once.
      return { ...view, agentName: str(msg['agent_name'], view.agentName) };
    case 'speech':
      return { ...view, speech: speechOf(msg['speech']) };
    case 'listening':
      return { ...view, listening: listeningOf(msg['listening']) };
    case 'mic.start':
      return { ...view, mic: true, hearing: '', banner: null };
    case 'mic.stop':
      // The words stay on screen until they become her message or are thrown away.
      return { ...view, mic: false, hearing: msg['reason'] === 'end' ? view.hearing : '' };
    case 'transcript.partial':
      return view.mic ? { ...view, hearing: str(msg['text']) } : view;
    case 'transcript.final':
      // What she heard is the user's message, shown exactly as she received it.
      return { ...sent(view, str(msg['text'])), hearing: '', pending: null };
    case 'history': {
      const rows = Array.isArray(msg['messages']) ? (msg['messages'] as Record<string, unknown>[]) : [];
      const entries: Entry[] = rows.flatMap((r): Entry[] => {
        if (r['role'] === 'user') {
          return [r['seen'] === true ? { kind: 'user', text: str(r['text']), seen: true } : { kind: 'user', text: str(r['text']) }];
        }
        if (r['role'] === 'host') return [hostOf(r)];
        // A heads-up kept by the app: drawn as it arrived, with Join, detail and reason.
        if (r['role'] === 'notice') {
          const e = scheduledOf({ ...r, type: 'notice', spoken: false });
          return e && e.kind === 'scheduled' ? [{ ...e, dueMs: 0, due: '', late: false }] : [];
        }
        return [{ kind: 'assistant', text: str(r['text']), interrupted: false }];
      });
      return { ...view, entries };
    }
    case 'host':
      // The app's own line, drawn just before the turn it starts.
      return str(msg['text']) ? { ...view, entries: [...view.entries, { kind: 'host', text: str(msg['text']) }] } : view;
    case 'state': {
      const said = ORBS.find((o) => o === msg['orb']) ?? view.orb;
      // A turn that ended at a spend limit is a choice the user made, not a failure.
      const orb = view.capped && said === 'error' ? 'idle' : said;
      if (orb === 'processing') view = { ...view, capped: false };
      const noticeSpeaking = orb === 'speaking' && msg['notice'] === true;
      const next = { ...view, orb, noticeSpeaking, busy: busyOf(orb, noticeSpeaking) };
      // Her turn has started: the message was taken up.
      if (orb === 'processing') return { ...next, pending: null };
      // At rest, nothing is under way: whatever waited may come.
      return orb === 'idle' || orb === 'error' ? release(next) : next;
    }
    case 'reply.delta': {
      const text = str(msg['text']);
      const last = view.entries[view.entries.length - 1];
      if (view.replyOpen && last?.kind === 'assistant') {
        return { ...view, entries: [...view.entries.slice(0, -1), { ...last, text: last.text + text }] };
      }
      const reply: Entry = isRoastTurn(view.entries)
        ? { kind: 'assistant', text, interrupted: false, roast: 'writing' }
        : { kind: 'assistant', text, interrupted: false };
      return { ...view, replyOpen: true, pending: null, entries: [...view.entries, reply] };
    }
    case 'turn.complete':
      return release({ ...view, replyOpen: false, busy: false, hearing: '', pending: null, entries: roastsDone(view.entries) });
    case 'seen':
      // She chose to say nothing to their last message: it was seen, not left unanswered.
      return { ...view, entries: replaceLast(view.entries, (e) => e.kind === 'user', (e) => ({ ...e, seen: true }) as Entry) };
    case 'tool.start':
      // Her silence is not work to show: no chip, and its end matches nothing.
      if (msg['name'] === QUIET_TOOL) return view;
      return {
        ...view,
        replyOpen: false,
        entries: [
          ...view.entries,
          { kind: 'tool', callId: str(msg['call_id']), name: str(msg['name']), outcome: null, ok: true },
        ],
      };
    case 'tool.end':
      return {
        ...view,
        entries: replaceLast(
          view.entries,
          (e) => e.kind === 'tool' && e.callId === msg['call_id'],
          (e) => ({ ...e, outcome: str(msg['summary']), ok: msg['ok'] === true }) as Entry,
        ),
      };
    case 'confirm.request': {
      const pendingId = str(msg['pending_id']);
      if (view.entries.some((e) => e.kind === 'card' && e.pendingId === pendingId)) return view;
      // The chip of the call that raised it takes the card's friendly name.
      const label = typeof msg['tool_label'] === 'string' ? msg['tool_label'].trim() : '';
      const named = label
        ? replaceLast(
            view.entries,
            (e) => e.kind === 'tool' && e.name === msg['tool'] && e.outcome === 'confirmation_required',
            (e) => ({ ...e, label }) as Entry,
          )
        : view.entries;
      const draft = draftIn(msg['draft']);
      const card: Entry = {
        kind: 'card',
        pendingId,
        summary: str(msg['summary']),
        ...(typeof msg['detail'] === 'string' && msg['detail'] ? { detail: msg['detail'] } : {}),
        expiresAt: typeof msg['expires_at'] === 'number' ? msg['expires_at'] : 0,
        tapOnly: msg['tap_only'] === true,
        status: 'waiting',
        note: '',
        toolLabel: labelOf(msg['tool_label'], msg['tool']),
        reason: REASONS.find((r) => r === msg['reason']) ?? null,
        ...(lookIn(msg['look']) ? { look: lookIn(msg['look']) } : {}),
        ...(draft ? { draft } : {}),
        ...(typeof msg['expired_note'] === 'string' && msg['expired_note'] ? { expiredNote: msg['expired_note'] } : {}),
      };
      // A new helper draft replaces the one still waiting, where it was: the core retired
      // that card when this one was made, so there is only ever one.
      const old = draft ? pinnedDraft(named) : null;
      if (old) return { ...view, entries: named.map((e) => (e === old ? card : e)) };
      return { ...view, entries: [...named, card] };
    }
    case 'helper.offer': {
      const name = str(msg['name']);
      const job = str(msg['job']);
      if (!name || !job) return view;
      const entry: Entry = { kind: 'offer', area: str(msg['area']), name, job, status: 'open', note: '' };
      // Only the newest offer can be answered: any older one still open is past.
      const past = (list: readonly Entry[]): Entry[] => list.map((e) => (e.kind === 'offer' && e.status === 'open' ? { ...e, status: 'past' } : e));
      const v = { ...view, entries: past(view.entries), held: past(view.held) };
      // Like a notice, it never lands inside a reply that is still under way.
      return v.replyOpen || v.busy ? { ...v, held: [...v.held, entry] } : { ...v, entries: [...v.entries, entry] };
    }
    case 'helper.offer.failed':
      // Only the offer of that area whose answer was sent: Create (why not), or No thanks (kept as said).
      return settleOffer(view, str(msg['area']), (e) =>
        e.status === 'creating' ? { ...e, status: 'failed', note: offerFailed(str(msg['reason']), e.name) } : e,
      );
    case 'helper.created':
      // Made from an offer: that offer's line, and no other. One from a card names no area.
      return settleOffer(view, str(msg['area']), (e) => (e.status === 'creating' ? { ...e, status: 'made' } : e));
    case 'confirm.resolved': {
      const outcome = cardOutcome(str(msg['status']), str(msg['reason']), str(msg['detail']));
      // Approved by what the user said, in their own voice, this turn (SPN-20).
      if (msg['by'] === 'own_words' && outcome.status === 'done') outcome.note = 'Done, on your word.';
      return {
        ...view,
        entries: replaceLast(
          view.entries,
          (e) => e.kind === 'card' && e.pendingId === msg['pending_id'],
          (e) => ({ ...e, ...outcome }) as Entry,
        ),
      };
    }
    case 'browser.ask.resolved':
      // Answered here or in her browser's window, run out or stopped: as the core says.
      return settleAsk(view, str(msg['ask_id']), (a) => (a.status === 'settled' && a.answer === str(msg['answer']) ? a : { ...a, status: 'settled', answer: str(msg['answer']), note: '' }));
    case 'interrupted':
      return release({
        ...view,
        replyOpen: false,
        busy: false,
        hearing: '',
        pending: null,
        entries: replaceLast(
          roastsDone(view.entries),
          (e) => e.kind === 'assistant',
          // With `kept`, only what the user heard of a spoken reply is shown (VOI-9).
          // Nothing heard is saved as the marker alone: shown as a reload shows it.
          (e) =>
            (msg['kept'] === ''
              ? { ...e, text: INTERRUPTED_MARK, interrupted: false }
              : { ...e, interrupted: true, ...(typeof msg['kept'] === 'string' ? { text: msg['kept'] } : {}) }) as Entry,
        ),
      });
    case 'notice': {
      // A chosen silence is never a failure (VOI-7); the core sends no notice for it.
      if (msg['code'] === 'stopped' && msg['reason'] === 'quiet') return view;
      if (msg['code'] === 'schedule') {
        const entry = scheduledOf(msg);
        const seen = (e: Entry): boolean => e.kind === 'scheduled' && entry?.kind === 'scheduled' && e.id === entry.id;
        if (!entry || view.entries.some(seen) || view.held.some(seen)) return view;
        // Nothing lands inside a reply that is still under way (VOI-17): it waits for the end.
        const during = view.replyOpen || view.busy;
        return during ? { ...view, held: [...view.held, entry] } : { ...view, entries: [...view.entries, entry] };
      }
      // Her browsing helper waits on the user: the host's own line (features/her-browser.ts),
      // with what it waits on when it can be answered here.
      if (msg['code'] === 'browser' && typeof msg['text'] === 'string') {
        const ask = askIn(msg['ask']);
        if (ask && view.entries.some((e) => e.kind === 'notice' && e.ask?.id === ask.id)) return view;
        return { ...view, entries: [...view.entries, { kind: 'notice', text: msg['text'], ...(ask ? { ask } : {}) }] };
      }
      // A long conversation was summarized for her. Nothing on screen goes away.
      const text =
        msg['code'] === 'compacted'
          ? 'She summarized the earlier messages to make room. They are still here, and her standing rules are kept whole.'
          : (STOPS[str(msg['reason'])] ?? 'Stopped early: a limit was reached.');
      return { ...view, entries: [...view.entries, { kind: 'notice', text }] };
    }
    case 'error': {
      const code = str(msg['code']);
      if (!REFUSED.has(code)) {
        // Any error but a refusal ends what was sent: a later busy must not pull it back.
        view = { ...view, pending: null };
      }
      // The app's line for a leg that is down (VOI-14, SUR-8), when the core sends one.
      const sentence = str(msg['sentence']);
      if (code === 'unauthenticated') return { ...view, link: 'unauthenticated', busy: false };
      if (code === 'replaced') return { ...view, link: 'replaced', busy: false };
      if (code === 'listening') {
        // One leg down: she cannot hear right now, and typing still works (VOI-14).
        const why = LISTEN_ERRORS[str(msg['kind'])] ?? 'Listening failed.';
        return { ...view, mic: false, hearing: '', busy: false, banner: sentence || `${why} You can still type.` };
      }
      if (code === 'capped') {
        // At a spend cap: the app's own line (BRN-8). The card follows as its own message.
        const text = sentence || ERRORS['capped'] || '';
        const ears = msg['leg'] === 'hearing' ? { mic: false, hearing: '' } : {};
        return { ...view, ...ears, busy: false, banner: text, capped: true, orb: view.orb === 'error' ? 'idle' : view.orb };
      }
      if (code === 'speech') {
        // Her voice failed; the reply itself carries on as text (VOI-14).
        const why = PROVIDER_ERRORS[str(msg['kind'])]?.replace('The model provider', 'The voice provider');
        return { ...view, banner: sentence || `${why ?? 'The voice provider failed.'} She is answering in text.` };
      }
      if (REFUSED.has(code) && view.pending === null && view.hearing) {
        // Spoken and refused: the words heard go in the box, so nothing said is lost.
        return { ...view, draft: view.hearing, hearing: '', busy: busyOf(view.orb, view.noticeSpeaking), banner: ERRORS[code] ?? null };
      }
      if (REFUSED.has(code) && view.pending !== null) {
        // The message was refused, not taken: back in the box, and off the screen.
        return {
          ...view,
          entries: unsent(view.entries, view.pending),
          draft: view.pending,
          pending: null,
          busy: busyOf(view.orb, view.noticeSpeaking),
          banner: ERRORS[code] ?? null,
        };
      }
      if (code === 'provider') {
        // Her model is down: the app says what broke, in the banner and as an app status
        // line in the conversation, never as her message (SUR-8, PER-11). Not saved.
        const down = sentence || (PROVIDER_ERRORS[str(msg['kind'])] ?? 'The model provider failed.');
        return { ...view, banner: down, busy: false, entries: [...view.entries, { kind: 'notice', text: down }] };
      }
      const text = sentence || (ERRORS[code] ?? unknownError(code));
      return { ...view, banner: text, busy: code === 'busy' ? view.busy : false };
    }
    default:
      return view;
  }
}

/** The page could not open the microphone at all. */
export function micFailed(view: View, problem: MicProblem): View {
  return { ...view, mic: false, hearing: '', banner: `${MIC_PROBLEMS[problem]} You can still type.` };
}

/**
 * True while she is working and nothing is arriving on screen: before the first
 * word, between tool calls, and for as long as a reasoning model thinks. The page
 * shows a line in the conversation so a long silence does not look like a hang.
 */
export function waiting(view: View): boolean {
  return view.link === 'ready' && view.busy && !view.replyOpen;
}

/** A card whose time ran out while it sat on screen. The core would refuse it anyway. */
export function expire(view: View, now: number): View {
  if (!view.entries.some((e) => e.kind === 'card' && e.status === 'waiting' && e.expiresAt <= now)) return view;
  return {
    ...view,
    entries: view.entries.map((e) =>
      e.kind === 'card' && e.status === 'waiting' && e.expiresAt <= now
        ? { ...e, status: 'closed', note: e.expiredNote ?? REFUSALS['expired'] ?? '' }
        : e,
    ),
  };
}

/** The status while stopped at a spend limit, in place of "Ready". */
export const CAPPED_TEXT = 'Stopped at your cap';

export const STATE_TEXT: Readonly<Record<Orb, string>> = {
  idle: 'Ready',
  arming: 'Getting ready',
  listening: 'Listening',
  processing: 'Thinking',
  speaking: 'Answering',
  error: 'Something failed',
};
