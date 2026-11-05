/**
 * Her helpers (the helpers panel spec): her routines, timed asks and watchers, in a
 * panel beside the chat. She heads it; her helpers sit under her by what they are
 * doing: Needs you, Working, Idle.
 *
 * The panel only displays. It starts no run. An approval waiting on you is the same card
 * as anywhere else, and a tap on it goes the usual way (SPN-14). Rows are kept by id and
 * updated in place on each `helpers.changed`, so focus and scroll survive a push. Every
 * piece of text is set with textContent, never parsed as markup.
 */

import { createApprovalStrip, type Card, type Strip } from './approvals';
import type { Api } from './settings';
import type { ServerMsg } from './view';

export type HelperKind = 'routine' | 'ask' | 'watcher';
export type HelperState = 'needs_you' | 'working' | 'idle' | 'paused' | 'capped';
export type Outcome = 'succeeded' | 'skipped' | 'failed' | 'nothing';

export interface LastRun {
  readonly at_ms: number;
  readonly outcome: Outcome;
  readonly reason: string | null;
}

export interface Waiting {
  readonly type: 'approval' | 'question' | 'step';
  readonly id: string;
  readonly text: string;
  readonly at_ms: number | null;
}

export interface Helper {
  readonly id: string;
  readonly kind: HelperKind;
  readonly name: string;
  readonly job: string;
  readonly state: HelperState;
  readonly doing: string | null;
  readonly since_ms: number | null;
  readonly last_run: LastRun | null;
  readonly next_ms: number | null;
  readonly waiting: readonly Waiting[];
  readonly conversation_id: string | null;
}

export interface HelperList {
  readonly helpers: readonly Helper[];
  readonly needs_you: number;
}

export type Group = 'needs_you' | 'working' | 'idle';

/** The groups, in the order the panel shows them. */
export const GROUPS: readonly (readonly [Group, string])[] = [
  ['needs_you', 'Needs you'],
  ['working', 'Working'],
  ['idle', 'Idle'],
];

/** Paused and capped helpers sit in Idle, saying so. */
export function groupOf(h: Pick<Helper, 'state'>): Group {
  return h.state === 'needs_you' || h.state === 'working' ? h.state : 'idle';
}

// ── Words ────────────────────────────────────────────────────────────────

const pad = (n: number): string => String(n).padStart(2, '0');

/** A time as a row says it: "9:55" today, "Thu 7:00" on another day. */
export function timeText(ms: number, now: number): string {
  const d = new Date(ms);
  const clock = `${d.getHours()}:${pad(d.getMinutes())}`;
  if (d.toDateString() === new Date(now).toDateString()) return clock;
  return `${d.toLocaleString('en', { weekday: 'short' })} ${clock}`;
}

/** How long a run has gone: "0:12", "1:02:05". */
export function elapsedText(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`;
}

const WAITING_WORDS: Readonly<Record<Waiting['type'], string>> = {
  approval: 'Approve a change',
  question: 'A question for you',
  step: '',
};

/**
 * A row's one short status: what needs you; what it is doing and for how long; or, at
 * rest, paused, a failed last run, or the next run. `capName` names the provider at its
 * cap when a helper is paused there.
 */
export function statusLine(h: Helper, now: number, capName: string | null = null): string {
  if (h.state === 'needs_you') {
    const first = h.waiting[0];
    if (!first) return 'Needs you';
    return WAITING_WORDS[first.type] || first.text;
  }
  if (h.state === 'working') {
    const doing = h.doing || 'Working';
    return h.since_ms === null ? doing : `${doing} · ${elapsedText(now - h.since_ms)}`;
  }
  if (h.state === 'capped') return `paused: ${capName ?? 'her model'} is at its cap`;
  if (h.state === 'paused') return 'paused';
  const last = h.last_run;
  if (last?.outcome === 'failed') return `failed at ${timeText(last.at_ms, now)}${last.reason ? `: ${last.reason}` : ''}`;
  if (h.next_ms !== null) return `next ${timeText(h.next_ms, now)}`;
  return h.job;
}

/** Her line at the top: "2 working, 1 needs you", or "All quiet". */
export function summaryLine(l: HelperList): string {
  const working = l.helpers.filter((h) => h.state === 'working').length;
  const needs = l.helpers.filter((h) => h.state === 'needs_you').length;
  const parts = [working ? `${working} working` : '', needs ? `${needs} ${needs === 1 ? 'needs' : 'need'} you` : ''].filter(Boolean);
  return parts.length ? parts.join(', ') : 'All quiet';
}

/** The Helpers button's accessible name. */
export function buttonLabel(needsYou: number): string {
  if (needsYou <= 0) return 'Helpers';
  return `Helpers, ${needsYou} ${needsYou === 1 ? 'needs' : 'need'} you`;
}

function parseList(raw: unknown): HelperList | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as { helpers?: unknown; needs_you?: unknown };
  if (!Array.isArray(r.helpers)) return null;
  const helpers = r.helpers.filter((h): h is Helper => !!h && typeof h === 'object' && typeof (h as Helper).id === 'string');
  const needs = typeof r.needs_you === 'number' ? r.needs_you : helpers.filter((h) => h.state === 'needs_you').length;
  return { helpers, needs_you: needs };
}

// ── The panel ────────────────────────────────────────────────────────────

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

export interface HelpersDeps {
  readonly api: Api;
  /** Write to the core on this page's socket: an approval's tap goes this way. */
  readonly send: (body: Record<string, unknown>) => void;
  /** Open a conversation, the way History opens one. */
  readonly open: (conversationId: string) => void;
  /** Open the Settings that do a step ("connect:email"). */
  readonly openStep: (stepId: string) => void;
  readonly now: () => number;
  readonly phone: () => boolean;
  /** The provider at its cap that background work waits on, when there is one. */
  readonly capName: () => string | null;
}

export interface HelpersPanel {
  readonly element: HTMLElement;
  /** Read the list and draw it: the panel was opened. */
  open(): Promise<void>;
  /** The panel was closed: its clock stops. */
  closed(): void;
  /** Put focus in the panel: on its first helper, else on the list itself. */
  focus(): void;
  /** A list from the core (`helpers.changed`, or a read). */
  update(list: HelperList): void;
  /** A message from the core: `helpers.changed` is drawn, others are ignored. */
  handle(msg: ServerMsg): void;
  readonly needsYou: number;
  /** Told how many need you, on every update. */
  onCount(listener: (needsYou: number) => void): void;
}

interface Row {
  readonly li: HTMLLIElement;
  readonly dot: HTMLElement;
  readonly name: HTMLElement;
  readonly status: HTMLElement;
  readonly inline: HTMLElement;
  strip: Strip | null;
  steps: string;
  helper: Helper;
}

const NO_LIST = "Couldn't read her helpers. Try again in a moment.";

export function createHelpers(d: HelpersDeps): HelpersPanel {
  const element = el('div', 'hp');
  const listView = el('div', 'hp-list');
  const her = el('div', 'hp-her');
  const herName = el('p', 'hp-her-name', 'Trillion');
  const summary = el('p', 'hp-summary', 'All quiet');
  summary.setAttribute('aria-live', 'polite');
  her.append(herName, summary);
  const problem = el('p', 'hp-problem');
  problem.hidden = true;
  const empty = el('p', 'hp-empty', 'No helpers yet. Routines, timed asks and watchers show up here.');
  empty.hidden = true;
  listView.append(her, problem, empty);
  const groupEls = new Map<Group, { section: HTMLElement; ul: HTMLUListElement }>();
  for (const [g, title] of GROUPS) {
    const section = el('section', 'hp-group');
    const heading = el('h3', '', title);
    heading.id = `hp-group-${g}`;
    section.setAttribute('aria-labelledby', heading.id);
    const ul = el('ul');
    section.append(heading, ul);
    section.hidden = true;
    groupEls.set(g, { section, ul });
    listView.append(section);
  }
  listView.tabIndex = -1;
  element.append(listView);

  const rows = new Map<string, Row>();
  const listeners: ((n: number) => void)[] = [];
  let current: HelperList = { helpers: [], needs_you: 0 };
  let ticking: ReturnType<typeof setInterval> | null = null;
  let isOpen = false;

  function makeRow(h: Helper): Row {
    const li = el('li', 'hp-row');
    li.dataset['id'] = h.id;
    const main = el('button', 'hp-main');
    main.type = 'button';
    const dot = el('span', 'hp-dot');
    dot.setAttribute('aria-hidden', 'true');
    const words = el('span', 'hp-words');
    const name = el('span', 'hp-name');
    const status = el('span', 'hp-status');
    words.append(name, status);
    main.append(dot, words);
    const inline = el('div', 'hp-inline');
    li.append(main, inline);
    return { li, dot, name, status, inline, strip: null, steps: '', helper: h };
  }

  function drawWaiting(r: Row): void {
    const h = r.helper;
    const cards = h.waiting.filter((w) => w.type === 'approval');
    if (cards.length && !r.strip) {
      r.strip = createApprovalStrip({
        onApprove: (id) => d.send({ type: 'confirm', pending_id: id }),
        onDecline: (id) => d.send({ type: 'cancel', pending_id: id }),
        onDismiss: () => undefined,
      });
      // The summary is the panel's one live region: the card says nothing by itself here.
      r.strip.el.querySelector('[aria-live]')?.removeAttribute('aria-live');
      r.strip.el.removeAttribute('role');
      r.inline.prepend(r.strip.el);
    }
    if (r.strip) {
      r.strip.draw(
        cards.map((w) => ({
          card: {
            kind: 'card',
            pendingId: w.id,
            summary: '',
            expiresAt: 0,
            tapOnly: true,
            status: 'waiting',
            note: '',
            toolLabel: w.text,
            reason: null,
          } satisfies Card,
          mode: 'ask' as const,
          askedAt: w.at_ms ?? d.now(),
        })),
      );
      if (!cards.length) {
        r.strip.dispose();
        r.strip.el.remove();
        r.strip = null;
      }
    }
    const steps = h.waiting.filter((w) => w.type === 'step');
    const key = steps.map((w) => `${w.id}|${w.text}`).join('\n');
    if (key !== r.steps) {
      r.steps = key;
      for (const old of [...r.inline.querySelectorAll('.hp-step')]) old.remove();
      for (const w of steps) {
        const step = el('p', 'hp-step');
        const go = el('button', 'quiet', 'Open Settings');
        go.type = 'button';
        go.addEventListener('click', () => d.openStep(w.id));
        step.append(el('span', '', `${w.text}. `), go);
        r.inline.append(step);
      }
    }
  }

  function drawRow(r: Row): void {
    const h = r.helper;
    r.li.dataset['state'] = h.state;
    r.dot.dataset['state'] = h.state;
    if (r.name.textContent !== h.name) r.name.textContent = h.name;
    const status = statusLine(h, d.now(), d.capName());
    if (r.status.textContent !== status) r.status.textContent = status;
    drawWaiting(r);
  }

  function tick(): void {
    for (const r of rows.values()) if (r.helper.state === 'working') drawRow(r);
  }

  function clock(): void {
    const want = isOpen && current.helpers.some((h) => h.state === 'working');
    if (want && ticking === null) ticking = setInterval(tick, 1000);
    else if (!want && ticking !== null) {
      clearInterval(ticking);
      ticking = null;
    }
  }

  function update(next: HelperList): void {
    current = next;
    problem.hidden = true;
    element.classList.toggle('hp-phone', d.phone());
    const seen = new Set<string>();
    const order = new Map<Group, HTMLLIElement[]>(GROUPS.map(([g]) => [g, []]));
    for (const h of next.helpers) {
      seen.add(h.id);
      let r = rows.get(h.id);
      if (!r) {
        r = makeRow(h);
        rows.set(h.id, r);
      }
      r.helper = h;
      drawRow(r);
      order.get(groupOf(h))?.push(r.li);
    }
    for (const [id, r] of rows) {
      if (seen.has(id)) continue;
      r.strip?.dispose();
      r.li.remove();
      rows.delete(id);
    }
    // In place: a row already where it belongs is never moved, so focus stays on it.
    for (const [g, { section, ul }] of groupEls) {
      const want = order.get(g) ?? [];
      want.forEach((li, i) => {
        const there = ul.children[i] ?? null;
        if (there !== li) ul.insertBefore(li, there);
      });
      for (const extra of [...ul.children].slice(want.length)) extra.remove();
      section.hidden = want.length === 0;
    }
    empty.hidden = next.helpers.length > 0;
    const words = summaryLine(next);
    if (summary.textContent !== words) summary.textContent = words;
    for (const l of listeners) l(next.needs_you);
    clock();
  }

  async function load(): Promise<void> {
    try {
      const r = await d.api('GET', '/api/helpers');
      const found = r.ok ? parseList(await r.json()) : null;
      if (found) {
        update(found);
        return;
      }
    } catch {
      // Said below.
    }
    problem.textContent = NO_LIST;
    problem.hidden = false;
  }

  return {
    element,
    async open() {
      isOpen = true;
      element.classList.toggle('hp-phone', d.phone());
      await load();
      clock();
    },
    closed() {
      isOpen = false;
      clock();
    },
    focus() {
      (listView.querySelector<HTMLButtonElement>('.hp-main') ?? listView).focus();
    },
    update,
    handle(msg) {
      if (msg.type !== 'helpers.changed') return;
      const found = parseList(msg);
      if (found) update(found);
    },
    get needsYou() {
      return current.needs_you;
    },
    onCount(listener) {
      listeners.push(listener);
    },
  };
}
