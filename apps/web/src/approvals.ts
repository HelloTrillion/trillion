/**
 * Approvals on her board (SPN-2a, SPN-17): a pending action's card, pinned above her widgets.
 * These are not widgets: they are drawn from the core's `confirm.*` messages only, and show only
 * what the host composed. Every piece of text is set with textContent, never parsed as HTML.
 */
import { icon } from './icons';
import { clock } from './showcase';
import { type CardStatus, type Entry, EXPIRED_NOTE, type Reason } from './view';

export type Card = Extract<Entry, { kind: 'card' }>;

// ── Pure helpers ────────────────────────────────────────────────────────

/** The cards on the board, newest first: the ones placed there and not dismissed. */
export function approvalsOf(entries: readonly Entry[], onBoard: ReadonlySet<string>, dismissed: ReadonlySet<string>): Card[] {
  const out: Card[] = [];
  for (const e of entries) if (e.kind === 'card' && onBoard.has(e.pendingId) && !dismissed.has(e.pendingId)) out.unshift(e);
  return out;
}

/** How long an outcome stays on its card before it collapses to a row. */
export const OUTCOME_MS = 4000;

export function collapseAt(resolvedAt: number): number {
  return resolvedAt + OUTCOME_MS;
}

export type Mode = 'ask' | 'outcome' | 'row';

/** What a card shows: the ask, its outcome, or (once that has been seen) a one-line row. */
export function modeOf(status: CardStatus, resolvedAt: number | null, now: number): Mode {
  if (status === 'waiting') return 'ask';
  if (status === 'working' || resolvedAt === null) return 'outcome';
  return now < collapseAt(resolvedAt) ? 'outcome' : 'row';
}

// A read needs no line: it changes nothing. A write may be a file, an email or a post.
const REASON_WORDS: Readonly<Record<Reason, string>> = {
  irreversible: "This can't be undone.",
  spend: 'This spends money.',
  write: 'This makes a change.',
  read: '',
};

export function reasonWords(reason: Reason | null): string {
  return reason ? REASON_WORDS[reason] : '';
}

export function spokenHint(tapOnly: boolean): string {
  return tapOnly ? "Tap to approve: a spoken yes isn't enough for this" : 'Or just say yes';
}

const lowerFirst = (s: string): string => (s ? s[0]!.toLowerCase() + s.slice(1) : s);

/** A settled card's outcome in a line. */
export function outcomeWords(status: CardStatus, note: string): string {
  switch (status) {
    case 'done':
      return 'Approved · done';
    case 'working':
      return 'Approved · working on it';
    case 'declined':
      return 'Declined';
    case 'unknown':
      return 'Approved · the result could not be confirmed';
    case 'waiting':
      return '';
    default:
      if (note === EXPIRED_NOTE) return "Expired · she'll ask again";
      return `Didn't go through: ${lowerFirst(note) || 'nothing ran.'}`;
  }
}

export type Placement = 'board' | 'conversation';

/** Where a new approval's full card goes: the board, unless it cannot open there. */
export function placement(at: { readonly phone: boolean; readonly settingsShowing: boolean }): Placement {
  return at.phone || at.settingsShowing ? 'conversation' : 'board';
}

/** The cards to tell the core are delivered: waiting, drawn in full somewhere, and not told before. */
export function toStamp(cards: readonly Entry[], drawnFull: ReadonlySet<string>, stamped: ReadonlySet<string>): string[] {
  return cards.flatMap((e) =>
    e.kind === 'card' && e.status === 'waiting' && drawnFull.has(e.pendingId) && !stamped.has(e.pendingId) ? [e.pendingId] : [],
  );
}

/** The share of its time a card has left (1 when asked, 0 when it runs out). */
export function ringLeft(askedAt: number, expiresAt: number, now: number): number {
  const total = expiresAt - askedAt;
  if (total <= 0) return 0;
  return Math.min(1, Math.max(0, (expiresAt - now) / total));
}

// ── DOM: the strip ──────────────────────────────────────────────────────

function h<K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, text?: string): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}

const SVG_NS = 'http://www.w3.org/2000/svg';
function s(tag: string, attrs: Record<string, string | number>): SVGElement {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}

export interface StripItem {
  readonly card: Card;
  readonly mode: Mode;
  /** When the page first heard of it: where its countdown ring starts. */
  readonly askedAt: number;
}

export interface StripOptions {
  readonly onApprove: (id: string) => void;
  readonly onDecline: (id: string) => void;
  readonly onDismiss: (id: string) => void;
}

export interface Strip {
  readonly el: HTMLElement;
  draw(items: readonly StripItem[]): void;
  /** The ids drawn as a full card with its buttons, on screen. */
  drawnFull(): Set<string>;
  /** Move focus to a card; false when it is not drawn. */
  focus(id: string): boolean;
  dispose(): void;
}

const R = 18;
const C = 2 * Math.PI * R;

interface Drawn {
  readonly key: string;
  readonly el: HTMLElement;
  readonly mode: Mode;
  /** Brings its countdown up to date. */
  readonly tick: (now: number) => void;
}

/** What a drawn card depends on: the same key keeps its element. */
const keyOf = (it: StripItem): string => `${it.mode}|${it.card.status}|${it.card.note}`;

function askCard(it: StripItem, o: StripOptions): Drawn {
  const c = it.card;
  const el = h('section', 'ap-card');
  el.tabIndex = -1;
  el.dataset['status'] = c.status;
  const titleId = `ap-title-${c.pendingId}`;
  el.setAttribute('aria-labelledby', titleId);

  const head = h('header', 'ap-head');
  const titles = h('div', 'ap-titles');
  const title = h('h3', 'ap-title', c.toolLabel);
  title.id = titleId;
  titles.append(h('p', 'ap-eyebrow', it.mode === 'ask' ? 'Needs your OK' : 'Your OK'), title);
  head.append(titles);

  let tick = (_now: number): void => {};
  if (it.mode === 'ask' && c.expiresAt > 0) {
    const arc = s('circle', { cx: 22, cy: 22, r: R, class: 'ap-ring-arc', 'stroke-dasharray': C.toFixed(2) });
    const ring = s('svg', { class: 'ap-ring', viewBox: '0 0 44 44', 'aria-hidden': 'true' });
    ring.append(s('circle', { cx: 22, cy: 22, r: R, class: 'ap-ring-bg' }), arc);
    const timer = h('div', 'ap-timer');
    const text = h('span', 'ap-timer-text');
    timer.append(ring, text);
    timer.setAttribute('role', 'timer');
    head.append(timer);
    tick = (now) => {
      const left = (c.expiresAt - now) / 1000;
      text.textContent = clock(left);
      timer.setAttribute('aria-label', `${clock(left)} left to answer`);
      arc.setAttribute('stroke-dashoffset', ((1 - ringLeft(it.askedAt, c.expiresAt, now)) * C).toFixed(2));
      timer.toggleAttribute('data-low', left <= 60);
    };
  }

  el.append(head, h('p', 'ap-summary', c.summary));
  const reason = reasonWords(c.reason);
  if (reason) el.append(h('p', 'ap-reason', reason));

  if (it.mode === 'ask') {
    const row = h('div', 'ap-actions');
    const approve = h('button', 'ap-approve', 'Approve');
    approve.type = 'button';
    approve.addEventListener('click', () => o.onApprove(c.pendingId));
    const decline = h('button', 'ap-decline', 'Decline');
    decline.type = 'button';
    decline.addEventListener('click', () => o.onDecline(c.pendingId));
    row.append(approve, decline, h('span', 'ap-hint', spokenHint(c.tapOnly)));
    el.append(row);
  } else {
    const out = h('p', 'ap-outcome', outcomeWords(c.status, c.note));
    out.setAttribute('role', 'status');
    el.append(out);
  }
  tick(Date.now());
  return { key: keyOf(it), el, mode: it.mode, tick };
}

function rowCard(it: StripItem, o: StripOptions): Drawn {
  const c = it.card;
  const el = h('div', 'ap-row');
  el.tabIndex = -1;
  el.dataset['status'] = c.status;
  el.append(h('span', 'ap-row-label', c.toolLabel), h('span', 'ap-row-outcome', outcomeWords(c.status, c.note)));
  const x = h('button', 'ap-x');
  x.type = 'button';
  x.setAttribute('aria-label', `Dismiss “${c.toolLabel}”`);
  x.title = 'Dismiss';
  x.append(icon('close', 13));
  x.addEventListener('click', () => o.onDismiss(c.pendingId));
  el.append(x);
  return { key: keyOf(it), el, mode: 'row', tick: () => {} };
}

export function createApprovalStrip(o: StripOptions): Strip {
  const el = h('div', 'ap-strip');
  el.setAttribute('aria-label', 'Waiting for your OK');
  el.setAttribute('role', 'region');
  let drawn = new Map<string, Drawn>();
  let timer: ReturnType<typeof setInterval> | null = null;

  function ticking(on: boolean): void {
    if (on && timer === null) {
      timer = setInterval(() => {
        const now = Date.now();
        for (const d of drawn.values()) d.tick(now);
      }, 1000);
    } else if (!on && timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  }

  return {
    el,
    draw(items) {
      // A card that went from its ask to its outcome keeps focus, not lost to the page.
      const active = document.activeElement;
      const focusedId = [...drawn].find(([, d]) => active instanceof Node && d.el.contains(active))?.[0] ?? null;
      const next = new Map<string, Drawn>();
      const els: HTMLElement[] = [];
      for (const it of items) {
        const old = drawn.get(it.card.pendingId);
        const d = old && old.key === keyOf(it) ? old : (it.mode === 'row' ? rowCard : askCard)(it, o);
        next.set(it.card.pendingId, d);
        els.push(d.el);
      }
      // In place, as the board does: an unchanged card is never detached.
      const keep = new Set(els);
      for (const child of [...el.children]) if (!keep.has(child as HTMLElement)) child.remove();
      els.forEach((c, i) => {
        const at = el.children[i] ?? null;
        if (at !== c) el.insertBefore(c, at);
      });
      drawn = next;
      ticking([...next.values()].some((d) => d.mode === 'ask'));
      if (focusedId !== null && !(active instanceof Node && el.contains(active))) {
        (next.get(focusedId)?.el ?? els[0])?.focus({ preventScroll: true });
      }
    },
    drawnFull() {
      const ids = new Set<string>();
      if (!el.isConnected) return ids;
      for (const [id, d] of drawn) if (d.mode === 'ask') ids.add(id);
      return ids;
    },
    focus(id) {
      const d = drawn.get(id);
      if (!d || !d.el.isConnected) return false;
      d.el.scrollIntoView({ block: 'nearest' });
      d.el.focus({ preventScroll: true });
      return true;
    },
    dispose() {
      ticking(false);
      drawn = new Map();
      el.replaceChildren();
    },
  };
}

// ── Wiring: where each card goes, when the board opens and closes, delivery ──

export interface ApprovalsDeps {
  readonly send: (body: Record<string, unknown>) => void;
  /** The conversation's entries from the core (never onboarding's example). */
  readonly entries: () => readonly Entry[];
  readonly where: () => { readonly phone: boolean; readonly settingsShowing: boolean };
  readonly boardShowing: () => boolean;
  /** Open the board, or redraw it in place. */
  readonly showBoard: () => void;
  readonly closeBoard: () => void;
  /** Her widgets are up: the board stays for them. */
  readonly hasWidgets: () => boolean;
}

export interface Approvals {
  readonly strip: Strip;
  /** A `confirm.request` arrived (before it is applied): decide where its full card goes. */
  request(id: string): Placement;
  /** Its full card is on the board, so the conversation shows a chip. */
  onBoard(id: string): boolean;
  /** A card on the board still asks or shows its outcome: the board stays up for it. */
  holds(): boolean;
  /** What the board pins now (the board calls this as it draws). */
  pinned(): { readonly items: readonly StripItem[]; readonly waiting: boolean };
  /** The view changed: note what settled, open, redraw or close the board, and stamp delivery. */
  sync(): void;
  /** The conversation's chip: open the board at that card. */
  view(id: string): void;
  /** The board closed; `replaced` when something else (Settings) took its place for now. */
  closed(replaced: boolean): void;
  /** Another conversation: start afresh. */
  reset(): void;
}

const sigOf = (its: readonly StripItem[]): string => its.map((it) => `${it.card.pendingId}:${keyOf(it)}`).join('\n');

export function createApprovals(d: ApprovalsDeps): Approvals {
  let placed = new Set<string>();
  let dismissed = new Set<string>();
  let askedAt = new Map<string, number>();
  let resolvedAt = new Map<string, number>();
  let stamped = new Set<string>();
  // The board was opened for approvals alone: it closes again once they have all settled.
  let openedFor = false;
  let opening = false;
  let wake: ReturnType<typeof setTimeout> | null = null;
  // What the board last drew of the strip: it is redrawn only when that changes.
  let drawnSig = '';

  const strip = createApprovalStrip({
    onApprove: (id) => d.send({ type: 'confirm', pending_id: id }),
    onDecline: (id) => d.send({ type: 'cancel', pending_id: id }),
    onDismiss: (id) => {
      dismissed.add(id);
      sync();
    },
  });

  function items(now: number): StripItem[] {
    return approvalsOf(d.entries(), placed, dismissed).map((card) => ({
      card,
      askedAt: askedAt.get(card.pendingId) ?? now,
      mode: modeOf(card.status, resolvedAt.get(card.pendingId) ?? null, now),
    }));
  }

  function open(): void {
    if (!d.boardShowing()) openedFor = !d.hasWidgets();
    d.showBoard();
  }

  function stamp(): void {
    const full = d.boardShowing() ? strip.drawnFull() : new Set<string>();
    // The conversation draws every card not on the board in full (as it always has).
    for (const e of d.entries()) if (e.kind === 'card' && !placed.has(e.pendingId)) full.add(e.pendingId);
    for (const id of toStamp(d.entries(), full, stamped)) {
      stamped.add(id);
      d.send({ type: 'confirm.shown', pending_id: id });
    }
  }

  function sync(): void {
    const now = Date.now();
    for (const e of d.entries()) {
      if (e.kind !== 'card' || !placed.has(e.pendingId)) continue;
      const settled = e.status !== 'waiting' && e.status !== 'working';
      if (settled && !resolvedAt.has(e.pendingId)) resolvedAt.set(e.pendingId, now);
      // Back to waiting (it was never on screen): it asks again.
      if (!settled) resolvedAt.delete(e.pendingId);
    }
    const its = items(now);
    const visible = its.some((it) => it.mode !== 'row');
    if (opening) {
      opening = false;
      if (its.some((it) => it.mode === 'ask') && !d.where().settingsShowing) open();
    } else if (d.boardShowing()) {
      if (openedFor && !visible && !d.hasWidgets()) {
        openedFor = false;
        d.closeBoard();
      } else if (sigOf(its) !== drawnSig) d.showBoard();
    }
    // Wake when the next outcome collapses to a row.
    if (wake !== null) clearTimeout(wake);
    wake = null;
    const next = Math.min(...its.flatMap((it) => (it.mode === 'outcome' && resolvedAt.has(it.card.pendingId) ? [collapseAt(resolvedAt.get(it.card.pendingId)!)] : [])));
    if (Number.isFinite(next)) wake = setTimeout(sync, Math.max(0, next - now) + 20);
    stamp();
  }

  return {
    strip,
    request(id) {
      // Announced again (a reconnect): it stays where it was first put.
      if (askedAt.has(id)) return placed.has(id) ? 'board' : 'conversation';
      askedAt.set(id, Date.now());
      const where = placement(d.where());
      if (where === 'board') {
        placed.add(id);
        opening = true;
      }
      return where;
    },
    onBoard: (id) => placed.has(id),
    pinned() {
      const its = items(Date.now());
      drawnSig = sigOf(its);
      return { items: its, waiting: its.some((it) => it.mode === 'ask') };
    },
    holds() {
      return items(Date.now()).some((it) => it.mode !== 'row');
    },
    sync,
    view(id) {
      if (!d.boardShowing()) open();
      strip.focus(id);
      stamp();
    },
    closed(replaced) {
      if (!replaced) openedFor = false;
      strip.dispose();
      drawnSig = '';
    },
    reset() {
      placed = new Set();
      dismissed = new Set();
      askedAt = new Map();
      resolvedAt = new Map();
      stamped = new Set();
      openedFor = false;
      opening = false;
      drawnSig = '';
    },
  };
}
