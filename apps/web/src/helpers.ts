/**
 * Her helpers (the helpers panel spec): her routines, timed asks and watchers, in a
 * panel beside the chat. She heads it; her helpers sit under her by what they are
 * doing: Needs you, Working, Idle.
 *
 * The panel only displays. It starts no run, and answers no card: an approval or a
 * question waiting on you opens the conversation it was raised in, where the card waits,
 * so the tap and what she is told of it belong there (SPN-14). Rows are kept by id and
 * updated in place on each `helpers.changed`, so focus and scroll survive a push. Every
 * piece of text is set with textContent, never parsed as markup.
 */

import { icon } from './icons';
import { type Look, type Picker, createPicker, drawPicture, lookOf, pictureLabel } from './look';
import type { Api } from './settings';
import type { ServerMsg } from './view';

/** `own`: a helper the user made (the your helpers spec). */
export type HelperKind = 'routine' | 'ask' | 'watcher' | 'own';
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
  /** A routine the user paused: Resume, not Pause. A capped one is not paused. */
  readonly paused?: boolean;
  /** A watcher switched on, and its source connected; null for any other kind. */
  readonly switched_on?: boolean | null;
  readonly connected?: boolean | null;
  /** One of the user's own: what it has learned, its routines, and its picture. */
  readonly notes?: string | null;
  readonly routines?: readonly RoutineLine[];
  readonly icon?: string | null;
  readonly color?: string | null;
}

/** One of a helper's own routines, listed under it. */
export interface RoutineLine {
  readonly id: string;
  readonly name: string;
  /** Null while it, or its helper, is paused. */
  readonly next_ms: number | null;
  readonly paused: boolean;
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
  if (h.state === 'paused') {
    if (h.kind !== 'watcher') return 'paused';
    // Switched on and waiting for its source is not the same as turned off.
    const source = SOURCES[h.id.slice('watcher:'.length)];
    return h.switched_on !== false && h.connected === false && source ? `off: ${source} isn't connected` : 'off';
  }
  const last = h.last_run;
  if (last?.outcome === 'failed') return `failed at ${timeText(last.at_ms, now)}${last.reason ? `: ${last.reason}` : ''}`;
  // One of the user's own says what it is for; its routines' next runs are in its details.
  if (h.kind === 'own') return h.job;
  if (h.next_ms !== null) return `next ${timeText(h.next_ms, now)}`;
  return h.job;
}

// What each watcher reads through, to say what it waits for.
const SOURCES: Readonly<Record<string, string>> = { meetings: 'Google', email: 'Google', payments: 'Stripe' };

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

export interface Run extends LastRun {
  readonly conversation_id: string | null;
}

/** How a run ended, as its history says it. */
export function outcomeText(run: LastRun): string {
  switch (run.outcome) {
    case 'succeeded':
      return 'Done';
    case 'nothing':
      return 'Nothing for you';
    case 'skipped':
      return run.reason ? `Skipped: ${run.reason}` : 'Skipped';
    default:
      return run.reason ? `Failed: ${run.reason}` : 'Failed';
  }
}

// What each watcher reads, as Connected tools' catalog names it.
const STEP_TARGETS: Readonly<Record<string, string>> = {
  'connect:email': 'gmail',
  'connect:meetings': 'google-calendar',
  'connect:payments': 'stripe',
};

/**
 * The provider background work waits on at its cap, from `/api/usage`'s providers, or
 * null. The first model listed is the usual one, which background work always runs on.
 */
export function capNameOf(providers: readonly { readonly leg: string; readonly name: string; readonly state: string }[] | undefined): string | null {
  const usual = providers?.find((p) => p.leg === 'model');
  return usual?.state === 'capped' ? usual.name : null;
}

/** Keep the Helpers button's name, title and count with the list. */
export function bindButton(button: HTMLElement, dot: HTMLElement, panel: Pick<HelpersPanel, 'needsYou' | 'onCount'>): void {
  const draw = (n: number): void => {
    const label = buttonLabel(n);
    button.setAttribute('aria-label', label);
    button.title = label;
    dot.hidden = n <= 0;
    dot.textContent = n > 0 ? String(n) : '';
  };
  draw(panel.needsYou);
  panel.onCount(draw);
}

/** The Connected tools entry a step opens at (connect or sign in again), or null for the section. */
export function stepTarget(stepId: string): string | null {
  return STEP_TARGETS[stepId] ?? null;
}

/** The panel's id for a helper the core names by its own id alone. */
const ownId = (id: string): string => (id.startsWith('own:') ? id : `own:${id}`);

/** A new helper, from `helper.created`: the panel's helper and the chat to open. */
export function createdOf(msg: ServerMsg): { helperId: string; conversationId: string } | null {
  if (msg.type !== 'helper.created') return null;
  const id = msg['helper_id'];
  const conversation = msg['conversation_id'];
  if (typeof id !== 'string' || !id || typeof conversation !== 'string' || !conversation) return null;
  return { helperId: ownId(id), conversationId: conversation };
}

/**
 * Its first hello (the your helpers spec): asked once per page load per helper, when the
 * page opens its chat. The core decides whether it is the first time ever. One that did
 * not go (the socket was not open) is asked again next time.
 */
export function helloOnce(send: (body: Record<string, unknown>) => boolean): (helperId: string) => void {
  const asked = new Set<string>();
  return (helperId) => {
    if (asked.has(helperId)) return;
    if (send({ type: 'helper.hello' })) asked.add(helperId);
  };
}

/**
 * Where + New helper asks her: the conversation last open that is not a helper's own
 * chat, or null for a new one.
 */
export function askTarget(visited: readonly string[], helperChats: ReadonlySet<string>): string | null {
  for (let i = visited.length - 1; i >= 0; i--) {
    const id = visited[i];
    if (id !== undefined && !helperChats.has(id)) return id;
  }
  return null;
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
  /** Say something through the page's one polite region (`#announce`). */
  readonly announce: (text: string) => void;
  /** Open a conversation, the way History opens one. */
  readonly open: (conversationId: string) => void;
  /** Open the Settings that do a step ("connect:email"). */
  readonly openStep: (stepId: string) => void;
  readonly now: () => number;
  readonly phone: () => boolean;
  /** The provider at its cap that background work waits on, when there is one. */
  readonly capName: () => string | null;
  /** + New helper: ask her for one in these words. Without it, there is no button. */
  readonly newHelper?: (text: string) => void;
}

export interface HelpersPanel {
  readonly element: HTMLElement;
  /** Read the list and draw it: the panel was opened. */
  open(): Promise<void>;
  /** The panel was closed: its clock stops. */
  closed(): void;
  /** Put focus in the panel: on its first helper, else on the list itself. */
  focus(): void;
  /** Read the list and show one helper's details (a tap on its name in the chat); the list when it is gone. */
  showHelper(id: string): Promise<void>;
  /** A list from the core (`helpers.changed`, or a read). */
  update(list: HelperList): void;
  /** A message from the core: `helpers.changed` is drawn, others are ignored. */
  handle(msg: ServerMsg): void;
  readonly needsYou: number;
  /** Told how many need you, on every update. */
  onCount(listener: (needsYou: number) => void): void;
  /** The user's own helper whose chat this conversation is, from the last list. */
  ownFor(conversationId: string | null): Helper | null;
}

interface Row {
  readonly li: HTMLLIElement;
  readonly dot: HTMLElement;
  /** One of the user's own: its picture, which the dot sits on. */
  readonly pic: HTMLElement | null;
  look: string;
  readonly name: HTMLElement;
  readonly status: HTMLElement;
  readonly inline: HTMLElement;
  waits: string;
  helper: Helper;
}

const NO_LIST = "Couldn't read her helpers. Try again in a moment.";
const NOT_DONE = "That didn't work. It may have just finished or been removed.";
const NOT_REACHED = "Trillion didn't answer. Is it still running?";

/** The schedule id in a routine's or a timed ask's helper id. */
const scheduleOf = (id: string): string => id.slice(id.indexOf(':') + 1);

export function createHelpers(d: HelpersDeps): HelpersPanel {
  const element = el('div', 'hp');
  const listView = el('div', 'hp-list');
  const her = el('div', 'hp-her');
  const herName = el('p', 'hp-her-name', 'Trillion');
  // Not a live region: what changes is said through the page's own (`announce`).
  const summary = el('p', 'hp-summary', 'All quiet');
  her.append(herName, summary);
  const problem = el('p', 'hp-problem');
  problem.hidden = true;
  const empty = el('p', 'hp-empty', 'No helpers yet. Routines, timed asks and watchers show up here.');
  empty.hidden = true;
  // + New helper: one field, and her card does the rest.
  const addBtn = el('button', 'quiet hp-add');
  addBtn.type = 'button';
  addBtn.append(icon('plus', 16), 'New helper');
  addBtn.setAttribute('aria-expanded', 'false');
  const addForm = el('form', 'hp-new');
  addForm.hidden = true;
  addBtn.setAttribute('aria-controls', (addForm.id = 'hp-new'));
  const addField = el('input');
  addField.id = 'hp-new-text';
  addField.type = 'text';
  addField.maxLength = 300;
  addField.autocomplete = 'off';
  const addLabel = el('label', '', 'What should it do?');
  addLabel.htmlFor = addField.id;
  const addSend = el('button', 'approve', 'Ask Trillion');
  addSend.type = 'submit';
  const addRow = el('div', 'hp-new-row');
  addRow.append(addField, addSend);
  addForm.append(addLabel, addRow);
  addBtn.hidden = !d.newHelper;
  listView.append(her, addBtn, addForm, problem, empty);
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

  // ── Details: one helper, its runs, and what can be done with it ──
  const detailsView = el('div', 'hp-details');
  detailsView.tabIndex = -1;
  detailsView.hidden = true;
  const backBtn = el('button', 'quiet hp-back', 'Back to helpers');
  backBtn.type = 'button';
  // One of the user's own: its picture, which opens the picker.
  const dHead = el('div', 'hp-d-head');
  const dPicWrap = el('div', 'hp-d-picwrap');
  const dPic = el('button', 'hp-d-pic');
  dPic.type = 'button';
  dPic.setAttribute('aria-haspopup', 'dialog');
  dPic.setAttribute('aria-expanded', 'false');
  dPic.title = 'Change its picture';
  dPicWrap.append(dPic);
  const dName = el('h3', 'hp-d-name');
  dHead.append(dPicWrap, dName);
  const dJob = el('p', 'hp-d-job');
  const dStatus = el('p', 'hp-d-status');
  const dNext = el('p', 'hp-d-next');
  const dActions = el('div', 'hp-d-actions');
  const toggle = el('button', '');
  toggle.type = 'button';
  const talk = el('button', 'approve', 'Talk to it');
  talk.type = 'button';
  const del = el('button', 'hp-delete', 'Delete');
  del.type = 'button';
  dActions.append(toggle, talk, del);
  const confirm = el('div', 'hp-d-confirm');
  const confirmText = el('p');
  const confirmYes = el('button', '', 'Cancel it');
  confirmYes.type = 'button';
  const confirmNo = el('button', 'quiet', 'Keep it');
  confirmNo.type = 'button';
  confirm.append(confirmText, confirmYes, confirmNo);
  confirm.hidden = true;
  // What the confirm asks: to cancel a timed ask, or to delete one of the user's own.
  let confirming: 'cancel' | 'delete' = 'cancel';
  const dNote = el('p', 'hp-d-note');
  // One of the user's own: what it knows, and its routines.
  const dOwn = el('div', 'hp-d-own');
  const dKnows = el('p', 'hp-d-knows');
  const dRoutines = el('ul', 'hp-routines');
  const dNoRoutines = el('p', 'hp-d-note', 'No routines yet.');
  dOwn.append(el('h4', '', 'What it knows'), dKnows, el('h4', '', 'Its routines'), dRoutines, dNoRoutines);
  const runsHead = el('h4', '', 'Recent runs');
  const runsList = el('ul', 'hp-runs');
  const runsNote = el('p', 'hp-d-note');
  detailsView.append(backBtn, dHead, dJob, dStatus, dNext, dActions, confirm, dNote, dOwn, runsHead, runsList, runsNote);
  element.append(detailsView);
  // The helper whose details are shown, or null on the list.
  let showing: string | null = null;
  // The picker, while open, and a tapped picture the core has not yet answered for.
  let picker: Picker | null = null;
  let tapped: { readonly id: string; readonly look: Look; readonly n: number } | null = null;
  let taps = 0;

  const rows = new Map<string, Row>();
  const listeners: ((n: number) => void)[] = [];
  let current: HelperList = { helpers: [], needs_you: 0 };
  // Bumped by every list drawn: a read that a push overtook is thrown away.
  let seq = 0;
  // How many needed you when last said; null before the first list.
  let saidNeeds: number | null = null;
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
    let pic: HTMLElement | null = null;
    if (h.kind === 'own') {
      // Its picture, with the state dot on its edge.
      const wrap = el('span', 'hp-pic');
      pic = el('span');
      pic.setAttribute('role', 'img');
      wrap.append(pic, dot);
      main.append(wrap, words);
    } else main.append(dot, words);
    const inline = el('div', 'hp-inline');
    main.addEventListener('click', () => void showDetails(h.id));
    li.append(main, inline);
    return { li, dot, pic, look: '', name, status, inline, waits: '', helper: h };
  }

  // What opens the conversation a card or a question waits in.
  const OPEN_WORDS: Readonly<Record<'approval' | 'question', string>> = { approval: 'Open to approve', question: 'Open to reply' };

  function drawWaiting(r: Row): void {
    const h = r.helper;
    const opens = [...new Set(h.waiting.map((w) => w.type).filter((t): t is 'approval' | 'question' => t !== 'step'))];
    const steps = h.waiting.filter((w) => w.type === 'step');
    const key = [...opens, ...steps.map((w) => `${w.id}|${w.text}`)].join('\n');
    if (key === r.waits) return;
    r.waits = key;
    const rowNote = el('p', 'hp-row-note');
    const parts: HTMLElement[] = opens.map((type) => {
      const go = el('button', 'quiet hp-open', OPEN_WORDS[type]);
      go.type = 'button';
      go.addEventListener('click', () => {
        go.disabled = true;
        void talkTo(h.id, r.helper.conversation_id).then((wrong) => {
          go.disabled = false;
          note(rowNote, wrong ?? '');
        });
      });
      return go;
    });
    for (const w of steps) {
      const step = el('p', 'hp-step');
      const go = el('button', 'quiet', 'Open Settings');
      go.type = 'button';
      go.addEventListener('click', () => d.openStep(w.id));
      step.append(el('span', '', `${w.text}. `), go);
      parts.push(step);
    }
    r.inline.replaceChildren(...parts, ...(parts.length ? [rowNote] : []));
  }

  /**
   * Open a helper's conversation, the way History opens one: `known` when the list
   * gives it, else the one the core picks (made if need be). What went wrong, or null.
   */
  async function talkTo(id: string, known: string | null): Promise<string | null> {
    if (known) {
      d.open(known);
      return null;
    }
    try {
      const r = await d.api('POST', `/api/helpers/${encodeURIComponent(id)}/conversation`, {});
      const body = r.ok ? ((await r.json()) as { conversation_id?: unknown }) : null;
      if (typeof body?.conversation_id === 'string') {
        d.open(body.conversation_id);
        return null;
      }
      return r.status === 404 ? 'There is no conversation for it yet.' : NOT_DONE;
    } catch {
      return NOT_REACHED;
    }
  }

  function drawRow(r: Row): void {
    const h = r.helper;
    r.li.dataset['state'] = h.state;
    r.dot.dataset['state'] = h.state;
    if (r.name.textContent !== h.name) r.name.textContent = h.name;
    if (r.pic) {
      const look = lookOf(h.icon, h.color);
      const label = pictureLabel(h.name, look);
      if (r.look !== label) {
        r.look = label;
        drawPicture(r.pic, look, 28);
        r.pic.setAttribute('aria-label', label);
      }
    }
    const status = statusLine(h, d.now(), d.capName());
    if (r.status.textContent !== status) r.status.textContent = status;
    drawWaiting(r);
  }

  function tick(): void {
    for (const r of rows.values()) if (r.helper.state === 'working') drawRow(r);
    if (showing !== null && helperOf(showing)?.state === 'working') drawDetails();
  }

  function clock(): void {
    const want = isOpen && current.helpers.some((h) => h.state === 'working');
    if (want && ticking === null) ticking = setInterval(tick, 1000);
    else if (!want && ticking !== null) {
      clearInterval(ticking);
      ticking = null;
    }
  }

  /** A line in the panel, said through the page too when it is a failure. */
  function note(where: HTMLElement, text: string): void {
    where.textContent = text;
    if (text) d.announce(text);
  }

  function needsWords(n: number): string {
    if (n <= 0) return 'No helper needs you now';
    return n === 1 ? '1 helper needs you' : `${n} helpers need you`;
  }

  function update(next: HelperList): void {
    seq += 1;
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
      r.li.remove();
      rows.delete(id);
    }
    // In place: a row already where it belongs is never moved. One that moves takes its
    // focus with it.
    const active = document.activeElement;
    const focused = active instanceof HTMLElement && listView.contains(active) ? active : null;
    for (const [g, { section, ul }] of groupEls) {
      const want = order.get(g) ?? [];
      want.forEach((li, i) => {
        const there = ul.children[i] ?? null;
        if (there !== li) ul.insertBefore(li, there);
      });
      for (const extra of [...ul.children].slice(want.length)) extra.remove();
      section.hidden = want.length === 0;
    }
    if (focused?.isConnected && document.activeElement !== focused) focused.focus({ preventScroll: true });
    empty.hidden = next.helpers.length > 0;
    const words = summaryLine(next);
    if (summary.textContent !== words) summary.textContent = words;
    // Said when it changes, never on the first quiet list.
    if (next.needs_you !== saidNeeds && (saidNeeds !== null || next.needs_you > 0)) d.announce(needsWords(next.needs_you));
    saidNeeds = next.needs_you;
    for (const l of listeners) l(next.needs_you);
    if (showing !== null) {
      // Its helper left the list (a fired ask, a removed routine): back to the list,
      // with focus there if it was on the details.
      if (rows.has(showing)) drawDetails();
      else {
        const had = detailsView.contains(document.activeElement);
        back(false);
        if (had) listView.focus();
      }
    }
    clock();
  }

  function helperOf(id: string | null): Helper | null {
    return id === null ? null : (rows.get(id)?.helper ?? null);
  }

  /** The picture its details show: one just tapped, until the core answers. */
  function shownLook(h: Helper): Look {
    return tapped?.id === h.id ? tapped.look : lookOf(h.icon, h.color);
  }

  function drawDetails(): void {
    const h = helperOf(showing);
    if (!h) return;
    const now = d.now();
    const mine = h.kind === 'own';
    if (dName.textContent !== h.name) dName.textContent = h.name;
    dPicWrap.hidden = !mine;
    dOwn.hidden = !mine;
    del.hidden = !mine;
    if (mine) {
      const look = shownLook(h);
      drawPicture(dPic, look, 44);
      dPic.setAttribute('aria-label', pictureLabel(h.name, look));
      if (picker && !tapped) picker.set(look);
      dKnows.textContent = h.notes?.trim() || 'Nothing yet. Tell it in its chat.';
      const routines = h.routines ?? [];
      dRoutines.replaceChildren(
        ...routines.map((r) => {
          const li = el('li', 'hp-routine');
          const when = r.paused || r.next_ms === null ? 'paused' : `next ${timeText(r.next_ms, now)}`;
          li.append(el('span', 'hp-routine-name', r.name), el('span', 'hp-routine-when', when));
          return li;
        }),
      );
      dRoutines.hidden = routines.length === 0;
      dNoRoutines.hidden = routines.length > 0;
    } else closePicker(false);
    dJob.textContent = h.job;
    const status = statusLine(h, now, d.capName());
    const when = h.next_ms === null ? '' : timeText(h.next_ms, now);
    // The next run is said once, below, not also as the status.
    dStatus.textContent = status === h.job || (when && status === `next ${when}`) ? '' : status;
    dStatus.hidden = !dStatus.textContent;
    // One of the user's own lists its routines' next runs instead.
    const next = when && !mine ? `${h.kind === 'watcher' ? 'Next check' : 'Next run'}: ${when}` : '';
    dNext.textContent = next;
    dNext.hidden = !next;
    const words = toggleWords(h);
    if (toggle.textContent !== words) toggle.textContent = words;
  }

  /**
   * What can be done with it, from what the core says outright: a timed ask can't be
   * paused, only cancelled; a routine is paused or resumed; a watcher is turned on or
   * off, or, switched on and waiting for its source, connected.
   */
  function toggleWords(h: Helper): 'Cancel' | 'Pause' | 'Resume' | 'Turn on' | 'Turn off' | 'Connect' {
    if (h.kind === 'ask') return 'Cancel';
    if (h.kind === 'own') return h.paused ? 'Resume' : 'Pause';
    if (h.kind === 'routine') return (h.paused ?? h.state === 'paused') ? 'Resume' : 'Pause';
    const on = h.switched_on ?? h.state !== 'paused';
    if (!on) return 'Turn on';
    return h.connected === false ? 'Connect' : 'Turn off';
  }

  function drawRuns(runs: readonly Run[] | null): void {
    runsList.replaceChildren(
      ...(runs ?? []).map((run) => {
        const li = el('li', 'hp-run');
        li.append(el('span', 'hp-run-at', timeText(run.at_ms, d.now())), el('span', 'hp-run-what', outcomeText(run)));
        if (run.conversation_id) {
          const open = el('button', 'quiet', 'Open');
          open.type = 'button';
          const id = run.conversation_id;
          open.addEventListener('click', () => d.open(id));
          li.append(open);
        }
        return li;
      }),
    );
    if (runs === null) note(runsNote, "Couldn't read its runs.");
    else runsNote.textContent = runs.length ? '' : 'No runs yet.';
    runsNote.hidden = !runsNote.textContent;
  }

  async function loadRuns(id: string): Promise<void> {
    let runs: Run[] | null = null;
    try {
      const r = await d.api('GET', `/api/helpers/${encodeURIComponent(id)}/runs`);
      const body = r.ok ? ((await r.json()) as { runs?: unknown }) : null;
      runs = Array.isArray(body?.runs) ? (body.runs as Run[]) : null;
    } catch {
      runs = null;
    }
    if (showing === id) drawRuns(runs);
  }

  async function showDetails(id: string): Promise<void> {
    if (!rows.has(id)) return;
    closePicker(false);
    showing = id;
    dNote.textContent = '';
    confirm.hidden = true;
    drawRuns([]);
    runsNote.hidden = true;
    drawDetails();
    listView.hidden = true;
    detailsView.hidden = false;
    backBtn.focus();
    await loadRuns(id);
  }

  /** Back to the list, with focus on the helper's row when `focus`. */
  function back(focus = true): void {
    const was = showing;
    closePicker(false);
    showing = null;
    detailsView.hidden = true;
    listView.hidden = false;
    if (focus) (was ? rows.get(was)?.li.querySelector<HTMLButtonElement>('.hp-main') : null)?.focus();
  }

  /**
   * One of the existing routes; the push that follows redraws the panel. A route that
   * answers with the list (a helper of the user's) is drawn at once. True when it went.
   */
  async function act(method: 'POST' | 'PUT' | 'DELETE', path: string, body: unknown): Promise<boolean> {
    dNote.textContent = '';
    toggle.disabled = del.disabled = true;
    try {
      const r = await d.api(method, path, body);
      if (!r.ok) {
        note(dNote, NOT_DONE);
        return false;
      }
      const found = parseList(await r.json().catch(() => null));
      if (found) update(found);
      return true;
    } catch {
      note(dNote, NOT_REACHED);
      return false;
    } finally {
      toggle.disabled = del.disabled = false;
    }
  }

  const ownPath = (id: string, what = ''): string => `/api/helpers/${encodeURIComponent(id)}${what}`;

  // ── The picker ──
  function onOutside(ev: Event): void {
    if (ev.target instanceof Node && !dPicWrap.contains(ev.target)) closePicker(false);
  }

  /** Close the picker: focus back on the picture when `focus`. */
  function closePicker(focus = true): void {
    if (!picker) return;
    picker.element.remove();
    picker = null;
    dPic.setAttribute('aria-expanded', 'false');
    document.removeEventListener('pointerdown', onOutside, true);
    if (focus) dPic.focus();
  }

  function openPicker(h: Helper): void {
    const id = h.id;
    picker = createPicker({
      name: h.name,
      look: shownLook(h),
      onPick: (look) => {
        const n = (taps += 1);
        tapped = { id, look, n };
        drawDetails();
        void act('PUT', ownPath(id, '/look'), { icon: look.icon, color: look.color }).then((went) => {
          // Only the last tap's answer settles it: an earlier one is overtaken.
          if (tapped?.n !== n) return;
          tapped = null;
          if (!went) {
            // Not saved: the picture saved before shows again.
            const saved = helperOf(id);
            if (saved && showing === id) {
              picker?.set(lookOf(saved.icon, saved.color));
              const words = dNote.textContent;
              drawDetails();
              dNote.textContent = words;
            }
          }
        });
      },
      onClose: () => closePicker(),
    });
    dPicWrap.append(picker.element);
    dPic.setAttribute('aria-expanded', 'true');
    document.addEventListener('pointerdown', onOutside, true);
    picker.focus();
  }

  dPic.addEventListener('click', () => {
    const h = helperOf(showing);
    if (!h || h.kind !== 'own') return;
    if (picker) closePicker();
    else openPicker(h);
  });

  // ── + New helper ──
  function closeAdd(focus: boolean): void {
    addForm.hidden = true;
    addBtn.setAttribute('aria-expanded', 'false');
    if (focus) addBtn.focus();
  }
  addBtn.addEventListener('click', () => {
    if (!addForm.hidden) {
      closeAdd(true);
      return;
    }
    addForm.hidden = false;
    addBtn.setAttribute('aria-expanded', 'true');
    addField.focus();
  });
  addForm.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const text = addField.value.trim();
    if (!text) {
      addField.focus();
      return;
    }
    addField.value = '';
    closeAdd(false);
    d.newHelper?.(text);
  });
  // Escape in the field closes it, and no further: the panel stays open.
  addForm.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape') return;
    ev.preventDefault();
    ev.stopPropagation();
    closeAdd(true);
  });

  backBtn.addEventListener('click', () => back());
  // Escape in the details goes back to the list, and no further: the panel stays open.
  detailsView.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape') return;
    ev.preventDefault();
    ev.stopPropagation();
    back();
  });
  toggle.addEventListener('click', () => {
    const h = helperOf(showing);
    if (!h) return;
    if (h.kind === 'ask') {
      askConfirm('cancel', h);
      return;
    }
    const words = toggleWords(h);
    if (h.kind === 'own') {
      void act('POST', ownPath(h.id, words === 'Resume' ? '/resume' : '/pause'), {});
      return;
    }
    if (h.kind === 'watcher') {
      const name = h.id.slice('watcher:'.length);
      // Waiting for its source: connecting it is what turns it on.
      if (words === 'Connect') d.openStep(`connect:${name}`);
      else void act('PUT', '/api/attention/settings', { watchers: { [name]: words === 'Turn on' } });
      return;
    }
    const what = words === 'Resume' ? 'resume' : 'pause';
    void act('POST', `/api/routines/${encodeURIComponent(scheduleOf(h.id))}/${what}`, {});
  });
  /** The second, plain button: to cancel a timed ask, or delete a helper of the user's. */
  function askConfirm(what: 'cancel' | 'delete', h: Helper): void {
    confirming = what;
    confirmText.textContent =
      what === 'delete' ? `Delete ${h.name}? Its routines stop. Its chat stays in History.` : "Cancel this timed ask? It won't happen.";
    confirmYes.textContent = what === 'delete' ? 'Delete it' : 'Cancel it';
    confirm.hidden = false;
    confirmNo.focus();
  }
  del.addEventListener('click', () => {
    const h = helperOf(showing);
    if (h?.kind === 'own') askConfirm('delete', h);
  });
  confirmNo.addEventListener('click', () => {
    confirm.hidden = true;
    (confirming === 'delete' ? del : toggle).focus();
  });
  confirmYes.addEventListener('click', () => {
    const h = helperOf(showing);
    confirm.hidden = true;
    if (!h) return;
    if (confirming === 'delete') void act('DELETE', ownPath(h.id), {});
    else void act('DELETE', `/api/schedules/${encodeURIComponent(scheduleOf(h.id))}`, {});
  });
  talk.addEventListener('click', () => {
    const id = showing;
    if (!id) return;
    dNote.textContent = '';
    talk.disabled = true;
    // One of the user's own has its chat already.
    const h = helperOf(id);
    void talkTo(id, h?.kind === 'own' ? h.conversation_id : null).then((wrong) => {
      talk.disabled = false;
      note(dNote, wrong ?? '');
    });
  });

  async function load(): Promise<void> {
    const asked = seq;
    try {
      const r = await d.api('GET', '/api/helpers');
      const found = r.ok ? parseList(await r.json()) : null;
      // A push came while it was read: that list is the newer one.
      if (seq !== asked) return;
      if (found) {
        update(found);
        return;
      }
    } catch {
      // Said below.
    }
    if (seq !== asked) return;
    note(problem, NO_LIST);
    problem.hidden = false;
  }

  // A window that crosses to phone width while open takes the sheet's layout.
  addEventListener('resize', () => {
    if (isOpen) element.classList.toggle('hp-phone', d.phone());
  });

  return {
    element,
    async open() {
      isOpen = true;
      // Each opening starts on the list.
      if (showing !== null) back(false);
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
    async showHelper(id) {
      isOpen = true;
      await load();
      clock();
      if (rows.has(id)) await showDetails(id);
      else {
        if (showing !== null) back(false);
        listView.focus();
      }
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
    ownFor(conversationId) {
      if (!conversationId) return null;
      return current.helpers.find((h) => h.kind === 'own' && h.conversation_id === conversationId) ?? null;
    },
  };
}
