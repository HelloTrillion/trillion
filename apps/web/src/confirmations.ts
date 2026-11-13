/**
 * Settings → Confirmations (SUR-7): every action that asked for your yes, newest first,
 * read from the decision record (SPN-17). When its card was shown, who answered and
 * how, and what happened, so "did that ever reach me?" has a page that answers it.
 *
 * It only reads. Nothing here approves, declines or retries anything: a card is
 * answered on the card, in the conversation that raised it (SPN-14). A row still
 * waiting can take you there. The names and summaries are the host's (SPN-2a); they
 * and the slim arguments are only ever set as text.
 */

import type { Host } from './panel';
import { dueWords } from './reminders';
import type { Api } from './settings';

// ── What the server sends ────────────────────────────────────────────────

export const LIFECYCLE = ['pending', 'approved', 'executing', 'succeeded', 'failed', 'unknown', 'rejected', 'expired', 'cancelled'] as const;
export type Lifecycle = (typeof LIFECYCLE)[number];
export type Filter = Lifecycle | 'all';

export interface HistoryEntry {
  readonly event: string;
  readonly at: number;
  readonly channel: string;
  readonly by: 'asking_conversation' | 'system' | 'another_session' | 'own_words';
}

export interface Confirmation {
  readonly id: string;
  readonly tool: string;
  /** The host's friendly name for the tool. */
  readonly tool_label: string;
  /** The host's frozen summary: what the card said. */
  readonly summary: string;
  readonly reason: 'irreversible' | 'spend' | 'write' | 'read';
  readonly status: Lifecycle;
  readonly created_at: number;
  readonly expires_at: number;
  /** When its card was on a screen; null if it never was. */
  readonly delivered_at: number | null;
  readonly surface: string | null;
  /** The decision that moved it on from waiting. */
  readonly decided_at: number | null;
  readonly decided_by: string | null;
  readonly decided_by_kind: HistoryEntry['by'] | null;
  readonly decision: string | null;
  /** How that decision arrived: tap, voice, text, channel… */
  readonly channel: string | null;
  readonly finished_at: number | null;
  readonly effect_status: string | null;
  /** The recorded reason a failed, unknown or cancelled action gave. */
  readonly detail: string;
  readonly cancelled_reason: 'superseded' | 'changed' | 'not_sent' | null;
  /** Where its card is, for one still waiting. */
  readonly conversation_id: string | null;
  readonly args_hash: string;
  /** The frozen slim copy: long values are their SHA-256 and length. */
  readonly args: unknown;
  readonly history: readonly HistoryEntry[];
}

export type Counts = Readonly<Record<Filter, number>>;

export interface ConfirmationsState {
  readonly now_ms: number;
  readonly days: number;
  readonly status: Filter;
  readonly counts: Counts;
  readonly actions: readonly Confirmation[];
}

// ── Words, tested on their own ───────────────────────────────────────────

const STATUS: Readonly<Record<Lifecycle, string>> = {
  pending: 'Waiting',
  approved: 'Approved',
  executing: 'Running',
  succeeded: 'Done',
  failed: 'Failed',
  unknown: 'Unknown',
  rejected: 'Declined',
  expired: 'Expired',
  cancelled: 'Cancelled',
};

export function statusText(s: Lifecycle): string {
  return STATUS[s] ?? String(s);
}

const CHANNEL: Readonly<Record<string, string>> = {
  tap: 'a tap on the card',
  voice: 'spoken',
  text: 'typed',
  channel: 'a message',
  ask: 'a tap on a widget',
};

/** How an answer arrived, as the user would say it. */
export function channelText(via: string): string {
  return CHANNEL[via] ?? via;
}

/** Who answered: the conversation that asked, the app on its own, or somewhere else. */
export function byText(kind: HistoryEntry['by'] | null): string {
  if (kind === 'asking_conversation') return 'in the conversation that asked';
  if (kind === 'system') return 'automatically';
  if (kind === 'another_session') return 'from another session';
  // SPN-20: what the user said was itself the yes, so there was no second turn.
  if (kind === 'own_words') return 'by your words';
  return '';
}

type When = (ms: number) => string;

export function shownText(a: Pick<Confirmation, 'delivered_at'>, when: When = dueWords): string {
  return a.delivered_at === null ? 'Never shown on a screen' : when(a.delivered_at);
}

/** The answer: what it was, how it came and when; or that there was none. */
export function answeredText(a: Pick<Confirmation, 'status' | 'decision' | 'decided_at' | 'channel' | 'decided_by_kind'>, when: When = dueWords): string {
  if (a.decided_at === null || a.decision === null) return a.status === 'pending' ? 'Not answered yet' : 'No answer';
  const at = when(a.decided_at);
  const how = a.channel ? channelText(a.channel) : '';
  const who = byText(a.decided_by_kind);
  const said = (verb: string): string => [how ? `${verb} (${how})` : verb, who].filter(Boolean).join(' ') + `, ${at}`;
  switch (a.decision) {
    case 'approved':
      // What they said was itself the yes (SPN-20): spoken, in their voice, no card.
      if (a.decided_by_kind === 'own_words') return `Approved by your words, ${at}`;
      return said('Approved');
    case 'rejected':
      return said('Declined');
    case 'expired':
      return `No answer before it expired, ${at}`;
    case 'superseded':
      return `No answer: a newer request replaced it, ${at}`;
    default:
      return `No answer: withdrawn, ${at}`;
  }
}

const CANCELLED: Readonly<Record<NonNullable<Confirmation['cancelled_reason']>, string>> = {
  superseded: 'Nothing was done: a newer request replaced it.',
  changed: 'Nothing was done: what it would change had changed since you were asked.',
  not_sent: 'Nothing went out: a check after a restart proved it.',
};

/** What happened, from the recorded status and reason. */
export function outcomeText(a: Pick<Confirmation, 'status' | 'delivered_at' | 'detail' | 'cancelled_reason'>): string {
  const why = a.detail ? ` Recorded reason: ${a.detail}.` : '';
  switch (a.status) {
    case 'pending':
      // Never "waiting for your yes" about a card nobody has seen (SPN-17).
      return a.delivered_at === null ? 'Waiting, but its card has not been shown yet.' : 'Waiting for your answer, on its card.';
    case 'approved':
      return 'Approved, not started yet.';
    case 'executing':
      return 'Approved and running.';
    case 'succeeded':
      return 'Done.';
    case 'failed':
      return `Failed.${why}`;
    case 'unknown':
      return `Unknown: it may or may not have happened. Check before asking for it again.${why}`;
    case 'rejected':
    case 'expired':
      return 'Nothing was done.';
    case 'cancelled':
      return a.cancelled_reason ? CANCELLED[a.cancelled_reason] : 'Nothing was done.';
    default:
      return '';
  }
}

/** Worth a warning colour: it may or may not have happened. */
export function flagged(a: Pick<Confirmation, 'status'>): boolean {
  return a.status === 'unknown';
}

/** The line at the top while any outcome is unknown. */
export function unknownBanner(counts: Partial<Counts> | null): string {
  const n = counts?.unknown ?? 0;
  if (!n) return '';
  return n === 1
    ? '1 action has an unknown outcome: it may or may not have happened.'
    : `${n} actions have an unknown outcome: they may or may not have happened.`;
}

/** The chips to offer: the usual ones always, approved and running only while any are. */
export function chips(counts: Partial<Counts> | null, chosen: Filter): readonly Filter[] {
  const rare: readonly Lifecycle[] = ['approved', 'executing'];
  return ['all', ...LIFECYCLE.filter((s) => !rare.includes(s) || (counts?.[s] ?? 0) > 0 || s === chosen)];
}

export function chipText(f: Filter, counts: Partial<Counts> | null): string {
  const name = f === 'all' ? 'All' : statusText(f);
  const n = counts?.[f];
  return n ? `${name} ${n}` : name;
}

export const WINDOWS: readonly { readonly days: number; readonly label: string }[] = [
  { days: 1, label: 'Last 24 hours' },
  { days: 7, label: 'Last 7 days' },
  { days: 30, label: 'Last 30 days' },
  { days: 90, label: 'Last 90 days' },
  { days: 365, label: 'Last year' },
];

export function query(status: Filter, days: number): string {
  const p = new URLSearchParams({ days: String(days) });
  if (status !== 'all') p.set('status', status);
  return `/api/confirmations?${p.toString()}`;
}

/** The line under the section's name in the sidebar. */
export function confirmationsSummary(s: Pick<ConfirmationsState, 'counts'> | null): string {
  if (!s) return '';
  if (s.counts.unknown) return `${s.counts.unknown} unknown`;
  if (s.counts.pending) return `${s.counts.pending} waiting`;
  return s.counts.all ? 'None waiting' : 'None yet';
}

/** What the page says when the list is empty. */
export function emptyText(status: Filter, days: number): string {
  const found = WINDOWS.find((w) => w.days === days);
  const span = found ? `the ${found.label.toLowerCase()}` : `the last ${days} days`;
  if (status === 'all') return `Nothing asked for your yes in ${span}.`;
  return `None ${statusText(status).toLowerCase()} in ${span}.`;
}

// ── The screen ───────────────────────────────────────────────────────────

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

const RELOAD_MS = 15_000;

export function createConfirmations(
  host: Host,
  api: Api,
  changed: () => void,
  /** Go to the conversation where a waiting card is. Settings closes first. */
  openConversation: (id: string) => void = () => undefined,
  /** The Seamless measures' one line about asks this week (`seamless.ts`), if wanted. */
  asks?: () => Promise<string>,
): { open(): Promise<void>; summary(): string } {
  let last: ConfirmationsState | null = null;
  let loadError = '';
  let status: Filter = 'all';
  let days = 30;

  const form = el('form', 'settings connected confirmations');
  form.addEventListener('submit', (ev) => ev.preventDefault());
  host.setAttribute('aria-labelledby', 'confirmations-title');
  const title = el('h2', '', 'Confirmations');
  title.id = 'confirmations-title';
  const note = el(
    'p',
    'settings-note',
    'Everything she asked your yes for, read from the record: when its card was shown, who answered and how, and what happened. To answer one, use its card in the conversation that asked.',
  );
  const asksEl = el('p', 'settings-hint confirmations-asks');
  asksEl.hidden = true;
  let asksText = '';
  const banner = el('p', 'settings-warn confirmations-unknown');
  banner.setAttribute('role', 'status');
  banner.hidden = true;
  const controls = el('div', 'connected-filter');
  const chipRow = el('div', 'connected-chips');
  chipRow.setAttribute('role', 'group');
  chipRow.setAttribute('aria-label', 'Show by status');
  const windowLabel = el('label', 'confirmations-window');
  const windowPick = el('select');
  for (const w of WINDOWS) {
    const o = el('option', '', w.label);
    o.value = String(w.days);
    windowPick.append(o);
  }
  windowPick.value = String(days);
  windowPick.addEventListener('change', () => {
    days = Number(windowPick.value) || 30;
    void load().then(draw);
  });
  windowLabel.append(el('span', '', 'When'), windowPick);
  controls.append(chipRow, windowLabel);
  const body = el('div', 'confirmations-body');
  form.append(title, note, asksEl, banner, controls, body);
  host.replaceChildren(form);

  async function load(): Promise<void> {
    const counting = asks?.().then((t) => {
      asksText = t;
    });
    try {
      const r = await api('GET', query(status, days));
      if (!r.ok) throw new Error(String(r.status));
      last = (await r.json()) as ConfirmationsState;
      loadError = '';
    } catch {
      loadError = last ? '' : 'Confirmations could not be loaded.';
    }
    await counting;
    changed();
  }

  function line(label: string, text: string, cls = ''): HTMLElement {
    const row = el('div', `confirmations-line${cls ? ` ${cls}` : ''}`);
    row.append(el('dt', '', label), el('dd', '', text));
    return row;
  }

  function card(a: Confirmation): HTMLElement {
    const box = el('article', `connected-card confirmations-card${flagged(a) ? ' is-unknown' : ''}`);
    box.dataset.id = a.id;
    const head = el('div', 'connected-card-head');
    const words = el('span', 'connected-card-words');
    words.append(el('strong', '', a.tool_label), el('span', 'confirmations-summary', a.summary));
    const pill = el('span', `confirmations-status is-${a.status}`, statusText(a.status));
    head.append(words, pill);
    const facts = el('dl', 'confirmations-facts');
    facts.append(
      line('Asked', dueWords(a.created_at)),
      line('Shown', shownText(a), a.delivered_at === null ? 'is-missing' : ''),
      line('Answered', answeredText(a)),
      line('Outcome', outcomeText(a), flagged(a) ? 'connected-warn' : a.status === 'failed' ? 'is-failed' : ''),
    );
    box.append(head, facts);
    if (a.status === 'pending' && a.conversation_id) {
      const id = a.conversation_id;
      // Goes to the card; it is answered there, never here (SPN-14).
      const go = button('Go to its card', 'quiet confirmations-go', () => {
        host.close();
        openConversation(id);
      });
      go.setAttribute('aria-label', `Go to the card for ${a.tool_label}`);
      box.append(go);
    }
    const more = el('details', 'confirmations-more');
    more.append(el('summary', '', 'Exact request'), el('pre', 'confirmations-args', JSON.stringify(a.args, null, 2)), el('small', 'connected-state', `Fingerprint ${a.args_hash}`));
    box.append(more);
    return box;
  }

  function drawChips(): void {
    const counts = last?.counts ?? null;
    chipRow.replaceChildren(
      ...chips(counts, status).map((f) => {
        const b = button(chipText(f, counts), `connected-chip${f === 'unknown' && (counts?.unknown ?? 0) > 0 ? ' is-unknown' : ''}`, () => {
          status = f;
          void load().then(draw);
        });
        b.setAttribute('aria-pressed', String(f === status));
        b.dataset.filter = f;
        return b;
      }),
    );
  }

  function draw(): void {
    if (asksEl.textContent !== asksText) asksEl.textContent = asksText;
    asksEl.hidden = !asksText;
    const warn = unknownBanner(last?.counts ?? null);
    if (banner.textContent !== warn) banner.textContent = warn;
    banner.hidden = !warn;
    drawChips();
    if (!last) {
      body.replaceChildren(el('p', 'settings-hint', loadError || 'Loading…'));
      return;
    }
    if (!last.actions.length) {
      body.replaceChildren(el('p', 'settings-hint', emptyText(status, days)));
      return;
    }
    const list = el('div', 'connected-list');
    list.append(...last.actions.map(card));
    // An open "Exact request" stays open across a reload.
    const open = new Set([...body.querySelectorAll<HTMLElement>('.confirmations-card')].filter((c) => c.querySelector('details')?.open).map((c) => c.dataset.id));
    for (const c of list.querySelectorAll<HTMLElement>('.confirmations-card')) {
      if (open.has(c.dataset.id)) c.querySelector('details')?.setAttribute('open', '');
    }
    body.replaceChildren(list);
  }

  // Once at the start, so the sidebar can say what is waiting; after this turn, so the
  // page has taken its session token from the address first.
  window.setTimeout(() => void load(), 0);
  let timer = 0;
  function stop(): void {
    if (timer) window.clearInterval(timer);
    timer = 0;
  }
  host.addEventListener('close', stop);

  return {
    async open() {
      host.showModal();
      stop();
      timer = window.setInterval(() => {
        if (!host.open) stop();
        else void load().then(draw);
      }, RELOAD_MS);
      draw();
      await load();
      draw();
    },
    summary: () => confirmationsSummary(last),
  };
}
