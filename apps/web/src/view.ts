/**
 * What the screen shows, as a pure function of the messages the core sent.
 * No DOM and no socket here, so every transition can be tested.
 */

export type Orb = 'idle' | 'arming' | 'listening' | 'processing' | 'speaking' | 'error';

export type CardStatus = 'waiting' | 'working' | 'done' | 'declined' | 'failed' | 'unknown' | 'closed';

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
  | { readonly kind: 'notice'; readonly text: string };

export type Link = 'connecting' | 'ready' | 'unauthenticated' | 'offline';

export interface View {
  readonly link: Link;
  readonly conversationId: string | null;
  readonly agentName: string;
  readonly model: string | null;
  readonly grantedRoots: readonly string[];
  readonly orb: Orb;
  readonly busy: boolean;
  /** True while a reply is streaming: the next piece of text belongs to it. */
  readonly replyOpen: boolean;
  readonly entries: readonly Entry[];
  readonly banner: string | null;
}

export const INITIAL: View = {
  link: 'connecting',
  conversationId: null,
  agentName: 'Trillion',
  model: null,
  grantedRoots: [],
  orb: 'idle',
  busy: false,
  replyOpen: false,
  entries: [],
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

const STOPS: Readonly<Record<string, string>> = {
  empty: 'The model sent back nothing. Try again, or rephrase.',
  refusal: 'The model declined to answer that.',
  max_tokens: 'The reply was cut off at the length limit.',
  rounds: 'Stopped: too many tool rounds in one turn.',
  wall_clock: 'Stopped: the turn ran too long.',
  cost: 'Stopped: the turn reached its cost ceiling.',
};

const str = (v: unknown, fallback = ''): string => (typeof v === 'string' ? v : fallback);

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
      return { status: 'closed', note: REFUSALS[reason] ?? (detail || 'This could not be approved.') };
  }
}

/** A user message the page is about to send: shown at once, before the core echoes anything. */
export function sent(view: View, text: string): View {
  return { ...view, busy: true, replyOpen: false, banner: null, entries: [...view.entries, { kind: 'user', text }] };
}

export function reduce(view: View, msg: ServerMsg): View {
  switch (msg.type) {
    case 'session': {
      const model = msg['model'] as { provider?: unknown; model?: unknown } | null;
      return {
        ...view,
        link: 'ready',
        conversationId: str(msg['id']) || null,
        agentName: str(msg['agent_name'], view.agentName),
        model: model ? str(model.model) : null,
        grantedRoots: Array.isArray(msg['granted_roots']) ? msg['granted_roots'].map((r) => String(r)) : [],
        busy: false,
        banner: null,
      };
    }
    case 'model': {
      const model = msg['model'] as { model?: unknown } | null;
      return { ...view, model: model ? str(model.model) : null, banner: null };
    }
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
      return { ...view, orb, busy: orb === 'processing' || orb === 'speaking' };
    }
    case 'reply.delta': {
      const text = str(msg['text']);
      const last = view.entries[view.entries.length - 1];
      if (view.replyOpen && last?.kind === 'assistant') {
        return { ...view, entries: [...view.entries.slice(0, -1), { ...last, text: last.text + text }] };
      }
      return { ...view, replyOpen: true, entries: [...view.entries, { kind: 'assistant', text, interrupted: false }] };
    }
    case 'turn.complete':
      return view.replyOpen ? { ...view, replyOpen: false } : view;
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
      return {
        ...view,
        replyOpen: false,
        entries: replaceLast(
          view.entries,
          (e) => e.kind === 'assistant',
          (e) => ({ ...e, interrupted: true }) as Entry,
        ),
      };
    case 'notice':
      return {
        ...view,
        entries: [...view.entries, { kind: 'notice', text: STOPS[str(msg['reason'])] ?? 'Stopped early: a limit was reached.' }],
      };
    case 'error': {
      const code = str(msg['code']);
      if (code === 'unauthenticated') return { ...view, link: 'unauthenticated', busy: false };
      const text =
        code === 'provider'
          ? (PROVIDER_ERRORS[str(msg['kind'])] ?? 'The model provider failed.')
          : (ERRORS[code] ?? 'Something went wrong.');
      return { ...view, banner: text, busy: code === 'busy' ? view.busy : false };
    }
    default:
      return view;
  }
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
