/**
 * What the screen shows, as a pure function of the messages the core sent.
 * No DOM and no socket here, so every transition can be tested.
 */

export type Orb = 'idle' | 'arming' | 'listening' | 'processing' | 'speaking' | 'error';

export type CardStatus = 'waiting' | 'working' | 'done' | 'declined' | 'failed' | 'unknown' | 'closed';

/** What a reminder or timed ask left: her words, or a missed or failed one with none. */
export type NoticeKind = 'reminder' | 'ask' | 'missed' | 'failed';

export type Entry =
  | { readonly kind: 'user'; readonly text: string }
  | { readonly kind: 'assistant'; readonly text: string; readonly interrupted: boolean }
  | { readonly kind: 'tool'; readonly callId: string; readonly name: string; readonly outcome: string | null; readonly ok: boolean }
  | {
      readonly kind: 'card';
      readonly pendingId: string;
      readonly summary: string;
      readonly expiresAt: number;
      readonly tapOnly: boolean;
      readonly status: CardStatus;
      readonly note: string;
    }
  | { readonly kind: 'notice'; readonly text: string }
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
    };

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
}

export const INITIAL: View = {
  link: 'connecting',
  conversationId: null,
  agentName: 'Trillion',
  model: null,
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
};

export type ServerMsg = { readonly type: string } & Readonly<Record<string, unknown>>;

const ORBS: readonly Orb[] = ['idle', 'arming', 'listening', 'processing', 'speaking', 'error'];

const REFUSALS: Readonly<Record<string, string>> = {
  expired: 'This expired before it was answered. Ask again.',
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
  bad_message: 'That message could not be sent.',
  internal: 'Something failed inside Trillion. The log has the details.',
};

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
  silent: 'Your microphone is sending silence. Choose another in Settings, under Hearing, or check its input and gain.',
};

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

const NOTICE_KINDS: readonly NoticeKind[] = ['reminder', 'ask', 'missed', 'failed'];

function scheduledOf(msg: ServerMsg): Entry | null {
  const what = NOTICE_KINDS.find((k) => k === msg['kind']);
  const id = str(msg['id']);
  if (!what || !id) return null;
  // Only she writes words; a missed or failed one never carries any (D7).
  const words = what === 'reminder' || what === 'ask' ? str(msg['text']) : '';
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
  };
}

/** A reply is over: what waited for it joins the conversation, in order. */
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
};

/** The chip for one tool call: what it is doing, then how it ended. */
export function toolLabel(name: string, outcome: string | null, ok: boolean): string {
  // She confirmed a card that was already settled: nothing ran, so no second success.
  if (outcome === 'already_resolved') return 'Already settled';
  const words = TOOL_LABELS[name];
  if (outcome === null) return words ? `${words[0]} …` : `${name} …`;
  if (words && ok && outcome === 'succeeded') return words[1];
  return `${name}: ${outcome.replaceAll('_', ' ')}`;
}

/** A user message the page is about to send: shown at once, before the core echoes anything. */
export function sent(view: View, text: string): View {
  return { ...view, busy: true, replyOpen: false, banner: null, pending: text, draft: null, entries: [...view.entries, { kind: 'user', text }] };
}

/** Drop the last user entry with this text: the one `sent` added. */
function unsent(entries: readonly Entry[], text: string): Entry[] {
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i];
    if (e?.kind === 'user' && e.text === text) return [...entries.slice(0, i), ...entries.slice(i + 1)];
  }
  return entries.slice();
}

export function reduce(view: View, msg: ServerMsg): View {
  const next = step(view, msg);
  // Whatever waited for a reply joins the conversation once none is under way.
  return next.held.length && !next.busy && !next.replyOpen ? release(next) : next;
}

function step(view: View, msg: ServerMsg): View {
  switch (msg.type) {
    case 'session': {
      const model = msg['model'] as { provider?: unknown; model?: unknown } | null;
      return {
        ...view,
        link: 'ready',
        conversationId: str(msg['id']) || null,
        agentName: str(msg['agent_name'], view.agentName),
        model: model ? str(model.model) : null,
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
      };
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
      const entries: Entry[] = rows.map((r) =>
        r['role'] === 'user'
          ? { kind: 'user', text: str(r['text']) }
          : { kind: 'assistant', text: str(r['text']), interrupted: false },
      );
      return { ...view, entries };
    }
    case 'state': {
      const orb = ORBS.find((o) => o === msg['orb']) ?? view.orb;
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
      return { ...view, replyOpen: true, pending: null, entries: [...view.entries, { kind: 'assistant', text, interrupted: false }] };
    }
    case 'turn.complete':
      return release({ ...view, replyOpen: false, busy: false, hearing: '', pending: null });
    case 'tool.start':
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
      return {
        ...view,
        entries: [
          ...view.entries,
          {
            kind: 'card',
            pendingId,
            summary: str(msg['summary']),
            expiresAt: typeof msg['expires_at'] === 'number' ? msg['expires_at'] : 0,
            tapOnly: msg['tap_only'] === true,
            status: 'waiting',
            note: '',
          },
        ],
      };
    }
    case 'confirm.resolved': {
      const outcome = cardOutcome(str(msg['status']), str(msg['reason']), str(msg['detail']));
      return {
        ...view,
        entries: replaceLast(
          view.entries,
          (e) => e.kind === 'card' && e.pendingId === msg['pending_id'],
          (e) => ({ ...e, ...outcome }) as Entry,
        ),
      };
    }
    case 'interrupted':
      return release({
        ...view,
        replyOpen: false,
        busy: false,
        hearing: '',
        pending: null,
        entries: replaceLast(
          view.entries,
          (e) => e.kind === 'assistant',
          // With `kept`, only what the user heard of a spoken reply is shown (VOI-9).
          (e) => ({ ...e, interrupted: true, ...(typeof msg['kept'] === 'string' ? { text: msg['kept'] } : {}) }) as Entry,
        ),
      });
    case 'notice': {
      if (msg['code'] === 'schedule') {
        const entry = scheduledOf(msg);
        const seen = (e: Entry): boolean => e.kind === 'scheduled' && entry?.kind === 'scheduled' && e.id === entry.id;
        if (!entry || view.entries.some(seen) || view.held.some(seen)) return view;
        // Nothing lands inside a reply that is still under way (VOI-17): it waits for the end.
        const during = view.replyOpen || view.busy;
        return during ? { ...view, held: [...view.held, entry] } : { ...view, entries: [...view.entries, entry] };
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
      if (code !== 'busy') {
        // Any error but busy ends what was sent: a later busy must not pull it back.
        view = { ...view, pending: null };
      }
      // Her own sentence for a leg that is down (VOI-14, SUR-8), when the core sends one.
      const sentence = str(msg['sentence']);
      if (code === 'unauthenticated') return { ...view, link: 'unauthenticated', busy: false };
      if (code === 'replaced') return { ...view, link: 'replaced', busy: false };
      if (code === 'listening') {
        // One leg down: she cannot hear right now, and typing still works (VOI-14).
        const why = LISTEN_ERRORS[str(msg['kind'])] ?? 'Listening failed.';
        return { ...view, mic: false, hearing: '', busy: false, banner: sentence || `${why} You can still type.` };
      }
      if (code === 'speech') {
        // Her voice failed; the reply itself carries on as text (VOI-14).
        const why = PROVIDER_ERRORS[str(msg['kind'])]?.replace('The model provider', 'The voice provider');
        return { ...view, banner: sentence || `${why ?? 'The voice provider failed.'} She is answering in text.` };
      }
      if (code === 'busy' && view.pending !== null) {
        // The message was refused, not taken: back in the box, and off the screen.
        return {
          ...view,
          entries: unsent(view.entries, view.pending),
          draft: view.pending,
          pending: null,
          busy: busyOf(view.orb, view.noticeSpeaking),
          banner: ERRORS['busy'] ?? null,
        };
      }
      const text =
        code === 'provider'
          ? sentence || (PROVIDER_ERRORS[str(msg['kind'])] ?? 'The model provider failed.')
          : (ERRORS[code] ?? 'Something went wrong.');
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
        ? { ...e, status: 'closed', note: REFUSALS['expired'] ?? '' }
        : e,
    ),
  };
}

export const STATE_TEXT: Readonly<Record<Orb, string>> = {
  idle: 'Ready',
  arming: 'Getting ready',
  listening: 'Listening',
  processing: 'Thinking',
  speaking: 'Answering',
  error: 'Something failed',
};
