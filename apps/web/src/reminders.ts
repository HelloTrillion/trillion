/**
 * Settings → Reminders (AGT-4, AGT-9): what is coming up, what happened recently, and
 * when the scheduler last checked. A check that is too old is a warning, because a
 * scheduler that stopped looks just like one with nothing to do (TESTING §4.5).
 *
 * Reminders and timed asks are set by asking her. Here they can only be cancelled.
 * Routines (AGT-3) are set by asking too; here they are listed in a group of their own,
 * with where each lands, and can be paused, resumed or deleted.
 * Titles come from the model: they are only ever set as text.
 */

import type { Host } from './panel';
import type { Api } from './settings';

// ── What the server sends ────────────────────────────────────────────────

export type RunStatus = 'delivered' | 'late' | 'missed' | 'blocked' | 'failed' | 'interrupted' | 'cancelled';

export interface Upcoming {
  readonly id: string;
  readonly kind: 'reminder' | 'ask';
  readonly title: string;
  readonly next_ms: number | null;
  /** "Mon 26 Oct, 9:00 am", in the user's own zone; empty when nothing more is due. */
  readonly next: string;
  /** "Every weekday at 9:00 am". */
  readonly when: string;
  /** "every weekday", or "once". */
  readonly repeat: string;
  readonly last: RunStatus | null;
}

export interface RunResult {
  readonly id: number;
  readonly schedule_id: string;
  readonly kind: 'reminder' | 'ask';
  readonly title: string;
  readonly status: RunStatus;
  readonly due_ms: number;
  /** "Sat 24 Oct, 4:00 pm", in the schedule's own zone; empty if the core couldn't say. */
  readonly due: string;
  readonly finished_ms: number | null;
  readonly detail: string;
}

export interface RemindersState {
  readonly active: readonly Upcoming[];
  readonly recent: readonly RunResult[];
  readonly checked_ms: number | null;
  readonly tick_s: number;
  /** The server's clock when it answered: the page's own may differ. */
  readonly now_ms: number;
}

/** A routine: a timed ask that builds a board, in today's day or a conversation of its own. */
export interface Routine {
  readonly id: string;
  readonly name: string;
  /** "Every weekday at 8:00 am". */
  readonly when: string;
  readonly next_ms: number | null;
  /** "Mon 26 Oct, 8:00 am", in the user's own zone; empty when nothing more is due. */
  readonly next: string;
  readonly target: 'day' | 'own';
  /** Its own conversation, once it has run; null for a day routine or one not run yet. */
  readonly conversation_id: string | null;
  readonly paused: boolean;
  readonly notes: string;
}

type Clock = Pick<RemindersState, 'checked_ms' | 'tick_s' | 'now_ms'>;

// ── Words, tested on their own ───────────────────────────────────────────

/** Past three checks and this much more, the scheduler is not checking. */
export const MARGIN_MS = 10_000;

export function stale(s: Clock): boolean {
  return s.checked_ms === null || s.now_ms - s.checked_ms > 3 * s.tick_s * 1000 + MARGIN_MS;
}

/** The server's clock now: what it said, moved on by how long ago it said it. */
export function serverNow(s: RemindersState, sinceMs: number): RemindersState {
  return sinceMs > 0 ? { ...s, now_ms: s.now_ms + sinceMs } : s;
}

function ago(ms: number): string {
  const secs = Math.max(0, Math.round(ms / 1000));
  if (secs < 120) return `${secs} s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 120) return `${mins} min ago`;
  return `${Math.round(mins / 60)} h ago`;
}

/** What a stale check means, said once: in the live region, so it is announced. */
export const STOPPED = 'The scheduler has stopped, so nothing will arrive until Trillion is started again.';

export function checkedText(s: Clock): string {
  if (s.checked_ms === null) return 'The scheduler has not checked yet.';
  const when = ago(s.now_ms - s.checked_ms);
  if (!stale(s)) return `Checked ${when}`;
  return `Last checked ${when}. ${STOPPED}`;
}

/** How long since the last check, without the warning: the line that keeps moving. */
export function sinceText(s: Clock): string {
  return s.checked_ms === null ? 'It has not checked since Trillion started.' : `Last checked ${ago(s.now_ms - s.checked_ms)}.`;
}

const RESULTS: Readonly<Record<RunStatus, string>> = {
  delivered: 'Delivered',
  late: 'Delivered late',
  missed: 'Missed',
  blocked: 'Waiting for a model',
  failed: "Didn't finish",
  interrupted: 'Interrupted',
  cancelled: 'Cancelled',
};

export function resultText(status: RunStatus): string {
  return RESULTS[status] ?? String(status);
}

export function kindText(kind: Upcoming['kind']): string {
  return kind === 'ask' ? 'Timed ask' : 'Reminder';
}

/** "Repeats every weekday", or "Once". */
export function repeatText(repeat: string): string {
  const r = repeat.trim();
  if (!r || r === 'once') return 'Once';
  return `Repeats ${r}`;
}

/** When it comes next, in words. A run with nothing more due is happening now. */
export function nextText(u: Pick<Upcoming, 'next'>): string {
  return u.next ? `Next: ${u.next}` : 'Due now';
}

/** A routine's next run, or that it is paused: a paused one's due time is not moving. */
export function routineNextText(r: Pick<Routine, 'next' | 'paused'>): string {
  return r.paused ? 'Paused' : nextText(r);
}

/** Where a routine's board lands. */
export function landsText(r: Pick<Routine, 'target' | 'conversation_id'>): string {
  if (r.target === 'day') return 'Your day';
  return r.conversation_id ? 'Its own conversation' : 'Its own conversation, made on its first run';
}

/** "Sat 24 Oct, 4:00 pm", in this browser's time unless a zone is given. */
export function dueWords(ms: number, timeZone?: string): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
    ...(timeZone ? { timeZone } : {}),
  }).formatToParts(new Date(ms));
  const get = (type: Intl.DateTimeFormatPartTypes): string => parts.find((p) => p.type === type)?.value ?? '';
  return `${get('weekday')} ${get('day')} ${get('month')}, ${get('hour')}:${get('minute')} ${get('dayPeriod').toLowerCase()}`;
}

/** One recent result: "Email: Missed (due Sat 24 Oct, 4:00 pm)". A reason only when it went
 * wrong. The due time is the core's, in the schedule's zone; this browser's only if none. */
export function resultLine(r: Pick<RunResult, 'title' | 'status' | 'due_ms' | 'due' | 'detail'>, timeZone?: string): string {
  // Waiting for a model already says why; a delivered or cancelled one needs none.
  const why = r.detail && (r.status === 'missed' || r.status === 'failed' || r.status === 'interrupted') ? `: ${r.detail}` : '';
  return `${r.title}: ${resultText(r.status)}${why} (due ${r.due || dueWords(r.due_ms, timeZone)})`;
}

/** Worth a warning colour: it went wrong, or is waiting. */
export function troubled(status: RunStatus): boolean {
  return status === 'failed' || status === 'blocked' || status === 'missed' || status === 'interrupted';
}

/** The line under the section's name in the sidebar. */
export function remindersSummary(s: RemindersState | null): string {
  if (!s) return '';
  if (stale(s)) return 'Not checking';
  const n = s.active.length;
  return n ? `${n} coming up` : 'None set';
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

// While the section is open, "Checked 12 s ago" keeps up, and the list is read again.
const REDRAW_MS = 5_000;
const RELOAD_MS = 15_000;

export function createReminders(
  host: Host,
  api: Api,
  changed: () => void,
  /** Go to a routine's conversation. Settings closes first. */
  openConversation: (id: string) => void = () => undefined,
): { open(): Promise<void>; summary(): string } {
  let last: RemindersState | null = null;
  // Read beside the rest; null until it first answers.
  let routines: readonly Routine[] | null = null;
  // When `last` arrived, by this page's clock: the server's clock moves on from it.
  let fetchedAt = 0;
  let loadError = '';
  let error = '';
  let confirming = '';
  let busy = false;
  let warned = '';

  const form = el('form', 'settings connected reminders');
  form.addEventListener('submit', (ev) => ev.preventDefault());
  host.setAttribute('aria-labelledby', 'reminders-title');
  const title = el('h2', '', 'Reminders');
  title.id = 'reminders-title';
  const note = el(
    'p',
    'settings-note',
    'Ask her to remind you of something, or to do something at a time. They arrive here when a page is open, and as a notification when none is. They run only while Trillion is running.',
  );
  // Kept across redraws and only changed when the warning does, so it is announced once.
  const warning = el('p', 'settings-warn reminders-stale');
  warning.setAttribute('role', 'status');
  warning.hidden = true;
  const body = el('div', 'reminders-body');
  form.append(title, note, warning, body);
  host.replaceChildren(form);

  const now = (): RemindersState | null => (last ? serverNow(last, Date.now() - fetchedAt) : null);

  async function loadRoutines(): Promise<void> {
    try {
      const r = await api('GET', '/api/routines');
      if (!r.ok) throw new Error(String(r.status));
      routines = ((await r.json()) as { routines: Routine[] }).routines;
    } catch {
      // What was shown stays; with nothing shown yet, the group says it could not load.
    }
  }

  async function load(): Promise<void> {
    const reading = loadRoutines();
    try {
      const r = await api('GET', '/api/schedules');
      if (!r.ok) throw new Error(String(r.status));
      last = (await r.json()) as RemindersState;
      fetchedAt = Date.now();
      loadError = '';
    } catch {
      // Unreachable: what was shown stays, and goes stale by itself.
      loadError = last ? '' : 'Reminders could not be loaded.';
    }
    await reading;
    changed();
  }

  /** Pause, resume or delete a routine: the user's own tap, answered with the routines as they now stand. */
  async function act(r: Routine, what: 'pause' | 'resume' | 'delete'): Promise<void> {
    busy = true;
    error = '';
    const path = `/api/routines/${encodeURIComponent(r.id)}`;
    try {
      // A body, so the request is JSON like every other change (SPN-11).
      const res = what === 'delete' ? await api('DELETE', path, {}) : await api('POST', `${path}/${what}`, {});
      if (res.ok) routines = ((await res.json()) as { routines: Routine[] }).routines;
      else error = res.status === 404 ? `“${r.name}” was already gone.` : `“${r.name}” could not be changed.`;
    } catch {
      error = 'Trillion could not be reached.';
    }
    if (error) await loadRoutines();
    confirming = '';
    busy = false;
    changed();
    draw();
    // A deleted one's card is gone: focus goes to the group it was in.
    if (what === 'delete') (form.querySelector('[data-focus="routines"]') as HTMLElement | null)?.focus();
  }

  function routine(r: Routine): HTMLElement {
    const box = el('div', 'connected-card reminders-card');
    const head = el('div', 'connected-card-head');
    const words = el('span', 'connected-card-words');
    words.append(
      el('strong', '', r.name),
      el('small', 'connected-state', r.when),
      el('small', 'connected-state', routineNextText(r)),
    );
    const lands = el('small', 'connected-state reminders-lands', landsText(r));
    if (r.target === 'own' && r.conversation_id) {
      const id = r.conversation_id;
      const go = button('Open', 'quiet reminders-open', () => {
        host.close();
        openConversation(id);
      });
      go.setAttribute('aria-label', `Open ${r.name}`);
      lands.append(' ', go);
    }
    words.append(lands);
    head.append(words);
    box.append(head);
    if (confirming === `routine:${r.id}`) {
      const confirm = el('div', 'connected-confirm');
      const keep = button('Keep it', 'quiet', () => {
        confirming = '';
        draw();
        (form.querySelector(`[data-focus="${CSS.escape(`delete:${r.id}`)}"]`) as HTMLElement | null)?.focus();
      });
      keep.setAttribute('aria-label', `Keep ${r.name}`);
      keep.dataset.focus = `keep:${r.id}`;
      const drop = button('Delete it', 'connected-danger-solid', () => {
        if (!busy) void act(r, 'delete');
      });
      drop.setAttribute('aria-label', `Delete ${r.name} for good`);
      drop.dataset.focus = `drop:${r.id}`;
      confirm.append(el('span', '', `Delete “${r.name}”? It won't run again.`), keep, drop);
      box.append(confirm);
      return box;
    }
    const actions = el('span', 'reminders-actions');
    const toggle = button(r.paused ? 'Resume' : 'Pause', 'quiet', () => {
      if (!busy) void act(r, r.paused ? 'resume' : 'pause');
    });
    toggle.setAttribute('aria-label', `${r.paused ? 'Resume' : 'Pause'} ${r.name}`);
    // The same place either way, so focus stays on it across the redraw.
    toggle.dataset.focus = `toggle:${r.id}`;
    const remove = button('Delete', 'quiet connected-danger', () => {
      confirming = `routine:${r.id}`;
      draw();
      (form.querySelector('.connected-confirm button') as HTMLButtonElement | null)?.focus();
    });
    remove.setAttribute('aria-label', `Delete ${r.name}`);
    remove.dataset.focus = `delete:${r.id}`;
    actions.append(toggle, remove);
    head.append(actions);
    return box;
  }

  async function cancel(u: Upcoming): Promise<void> {
    busy = true;
    error = '';
    try {
      // A body, so the request is JSON like every other change (SPN-11).
      const r = await api('DELETE', `/api/schedules/${encodeURIComponent(u.id)}`, {});
      if (r.ok) {
        last = (await r.json()) as RemindersState;
        fetchedAt = Date.now();
      } else error = r.status === 404 ? `“${u.title}” was already gone.` : `“${u.title}” could not be cancelled.`;
    } catch {
      error = 'Trillion could not be reached.';
    }
    if (error) await load();
    confirming = '';
    busy = false;
    changed();
    draw();
    // Its card is gone: focus goes to the list it was in.
    (form.querySelector('[data-focus="upcoming"]') as HTMLElement | null)?.focus();
  }

  function upcoming(u: Upcoming): HTMLElement {
    const box = el('div', 'connected-card reminders-card');
    const head = el('div', 'connected-card-head');
    const words = el('span', 'connected-card-words');
    words.append(
      el('strong', '', u.title),
      el('small', 'connected-state', `${kindText(u.kind)} · ${repeatText(u.repeat)}`),
      el('small', 'connected-state', nextText(u)),
    );
    if (u.last) words.append(el('small', `connected-state${troubled(u.last) ? ' connected-warn' : ''}`, `Last time: ${resultText(u.last)}`));
    head.append(words);
    box.append(head);
    if (confirming === u.id) {
      const confirm = el('div', 'connected-confirm');
      const keep = button('Keep it', 'quiet', () => {
        confirming = '';
        draw();
        (form.querySelector(`[data-focus="${CSS.escape(`cancel:${u.id}`)}"]`) as HTMLElement | null)?.focus();
      });
      keep.setAttribute('aria-label', `Keep ${u.title}`);
      keep.dataset.focus = `keep:${u.id}`;
      const drop = button('Cancel it', 'connected-danger-solid', () => {
        if (!busy) void cancel(u);
      });
      drop.setAttribute('aria-label', `Cancel ${u.title} for good`);
      drop.dataset.focus = `drop:${u.id}`;
      confirm.append(el('span', '', `Cancel “${u.title}”? It won't come back.`), keep, drop);
      box.append(confirm);
    } else {
      const remove = button('Cancel', 'quiet connected-danger', () => {
        confirming = u.id;
        draw();
        (form.querySelector('.connected-confirm button') as HTMLButtonElement | null)?.focus();
      });
      remove.setAttribute('aria-label', `Cancel ${u.title}`);
      remove.dataset.focus = `cancel:${u.id}`;
      head.append(remove);
    }
    return box;
  }

  function draw(): void {
    const s = now();
    const parts: HTMLElement[] = [];
    // The warning sits in the live region and changes only when it comes or goes, so it
    // is announced once. The time since the last check moves on in a line of its own.
    const warn = s !== null && stale(s) ? STOPPED : '';
    if (warn !== warned) {
      warned = warn;
      warning.textContent = warn;
      warning.hidden = !warn;
    }
    if (!s) {
      parts.push(el('p', 'settings-hint', loadError || 'Loading…'));
      body.replaceChildren(...parts);
      return;
    }
    parts.push(el('p', `settings-hint reminders-checked${warn ? ' connected-warn' : ''}`, warn ? sinceText(s) : checkedText(s)));
    if (error) {
      const p = el('p', 'settings-hint connected-warn', error);
      p.setAttribute('role', 'alert');
      parts.push(p);
    }
    const heading = el('h3', 'connected-heading', 'Coming up');
    heading.tabIndex = -1;
    heading.dataset.focus = 'upcoming';
    parts.push(heading);
    if (!s.active.length) parts.push(el('p', 'settings-hint', 'Nothing is set. Try “remind me at four to call the dentist”.'));
    else {
      const list = el('div', 'connected-list');
      list.append(...s.active.map(upcoming));
      parts.push(list);
    }
    const group = el('h3', 'connected-heading', 'Routines');
    group.tabIndex = -1;
    group.dataset.focus = 'routines';
    parts.push(group);
    if (routines === null) parts.push(el('p', 'settings-hint', 'Routines could not be loaded.'));
    else if (!routines.length) parts.push(el('p', 'settings-hint', 'None yet. Try “every weekday at 8, build my day”.'));
    else {
      const list = el('div', 'connected-list');
      list.append(...routines.map(routine));
      parts.push(list);
    }
    parts.push(el('h3', 'connected-heading', 'Recent'));
    if (!s.recent.length) parts.push(el('p', 'settings-hint', 'Nothing has run yet.'));
    else {
      const ul = el('ul', 'reminders-recent');
      for (const r of s.recent) ul.append(el('li', troubled(r.status) ? 'connected-warn' : '', resultLine(r)));
      parts.push(ul);
    }
    const active = document.activeElement instanceof HTMLElement && body.contains(document.activeElement) ? document.activeElement.dataset.focus : undefined;
    body.replaceChildren(...parts);
    if (active) (body.querySelector(`[data-focus="${CSS.escape(active)}"]`) as HTMLElement | null)?.focus();
  }

  // Once at the start, so the sidebar can say how many are coming up; after this turn,
  // so the page has taken its session token from the address first.
  window.setTimeout(() => void load(), 0);
  // Only while the section is open. Never under a confirm: a redraw would take its focus.
  let timer = 0;
  let since = 0;
  function tick(): void {
    if (!host.open) {
      stop();
      return;
    }
    since += REDRAW_MS;
    const reload = since >= RELOAD_MS;
    if (reload) since = 0;
    if (confirming || busy) return;
    if (reload) void load().then(() => (confirming || busy ? undefined : draw()));
    else draw();
  }
  function stop(): void {
    if (timer) window.clearInterval(timer);
    timer = 0;
  }
  host.addEventListener('close', stop);

  return {
    async open() {
      host.showModal();
      stop();
      since = 0;
      timer = window.setInterval(tick, REDRAW_MS);
      draw();
      await load();
      draw();
    },
    // As last read: it is read again when the section opens, not while it is shut.
    summary: () => remindersSummary(last),
  };
}
