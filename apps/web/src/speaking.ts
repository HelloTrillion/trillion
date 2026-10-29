/**
 * Settings → Speaking up (the speaking up spec): which watchers she runs, and what each
 * reads and sends out; the meeting heads-up time, the payment limit, quiet hours; the
 * three other reasons she holds her voice, with whether this computer can check each;
 * each watcher's notes and last check; and what she found lately, with Dismiss.
 *
 * Every change is sent alone, as a partial PUT, so a tap never undoes another. Titles,
 * details and notes can carry someone else's words: only ever set as text.
 */

import type { Host } from './panel';
import type { Api } from './settings';

// ── What the server sends ────────────────────────────────────────────────

export type WatcherName = 'meetings' | 'email' | 'payments';
export const WATCHERS: readonly WatcherName[] = ['meetings', 'email', 'payments'];

export interface Limit {
  readonly amount_minor: number;
  readonly currency: string;
}

export interface AttentionSettings {
  readonly watchers: Readonly<Record<WatcherName, boolean>>;
  readonly meeting_lead_min: number;
  readonly payment_limit: Limit | null;
  readonly quiet_hours: { readonly on: boolean; readonly from: string; readonly to: string };
  /** Hold while a meeting is under way; a core from before it had no switch for it. */
  readonly meeting?: boolean;
  readonly mic: boolean;
  readonly focus: boolean;
}

export interface Probe {
  readonly status: 'works' | "can't tell" | "didn't answer";
  readonly why: string;
  readonly note?: string;
}

export interface WatcherStatus {
  readonly last_check_ms: number | null;
  readonly last_error: string | null;
  readonly notes: string;
}

export interface AttentionState {
  readonly settings: AttentionSettings;
  readonly status: {
    readonly watchers: Partial<Record<WatcherName, WatcherStatus>>;
    readonly probes: { readonly mic?: Probe; readonly focus?: Probe };
  };
}

export type FindStatus = 'claimed' | 'told' | 'silent' | 'dismissed' | 'lost';

export interface Find {
  readonly key: string;
  readonly watcher: string;
  readonly first_seen_ms: number;
  readonly status: FindStatus;
  readonly title: string | null;
  readonly detail: string | null;
}

// ── Words, tested on their own ───────────────────────────────────────────

const NAMES: Readonly<Record<WatcherName, string>> = { meetings: 'Meetings', email: 'Urgent email', payments: 'Payments' };

/** What each watcher reads, and what leaves this computer when it finds something. */
export const READS: Readonly<Record<WatcherName, string>> = {
  meetings:
    'Reads your Google Calendar every minute and tells you before each meeting you are going to. Its title, time and the guests’ names go to your model provider to word the heads-up.',
  email:
    "Reads your unread important Gmail every 10 minutes, never the body. Sends each important email's sender, subject and preview to your model provider.",
  payments:
    'Reads Stripe every 10 minutes for failed charges and new disputes, and, once you set a limit, payments at or above it. Amounts and customer names go to your model provider. Runs only while Stripe’s read tool runs without asking.',
};

export function probeText(p: Probe | undefined): string {
  if (!p) return '';
  if (p.status === 'works') return 'Works here';
  if (p.status === "can't tell") return p.why ? `Can't tell here: ${p.why}` : "Can't tell here";
  return "Didn't answer just now";
}

/** A sentence from the server's few words: capital first, full stop last. */
function sentence(s: string): string {
  const t = s.trim();
  if (!t) return '';
  const up = t[0]!.toUpperCase() + t.slice(1);
  return /[.!?]$/.test(up) ? up : `${up}.`;
}

export function ago(ms: number): string {
  const secs = Math.max(0, Math.round(ms / 1000));
  if (secs < 120) return `${secs} s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 120) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

// A check's failure arrives by its kind only (never a message, which could quote an
// email): a class name, or a Google call's own kind.
const ERROR_KINDS: Readonly<Record<string, string>> = {
  TimeoutError: 'it took too long',
  // Calendar or Gmail is not connected, or Google sign-in is not set up.
  NotConnected: 'the Google account isn’t connected',
  // The payments watcher: Stripe's read tool is set to ask first, so it can't run on its own.
  AsksFirst: 'Stripe is set to ask first, so it can’t check on its own',
  StripeError: 'Stripe did not answer',
  signed_out: 'the Google sign-in ended; connect it again',
  unreachable: 'Google could not be reached',
  not_allowed: 'Trillion was not given that access; connect it again',
  api_off: 'that Google API is switched off in your Google Cloud project',
  busy: 'Google said too many requests; it tries again later',
  refused: 'Google refused the request',
  not_found: 'Google found nothing there',
  failed: 'Google answered with an error',
};

export function errorKindText(kind: string): string {
  return ERROR_KINDS[kind] ?? `an error (${kind})`;
}

/** "Last checked 3 min ago. Last time: it took too long." */
export function checkText(s: WatcherStatus | undefined, now: number): string {
  if (!s || s.last_check_ms === null) return s?.last_error ? `Not checked yet. Last time: ${errorKindText(s.last_error)}.` : 'Not checked yet.';
  const checked = `Last checked ${ago(now - s.last_check_ms)}.`;
  return s.last_error ? `${checked} Last time: ${errorKindText(s.last_error)}.` : checked;
}

const FIND_STATUS: Readonly<Record<FindStatus, string>> = {
  claimed: 'Telling you now',
  told: 'Told you',
  silent: 'Not worth telling',
  dismissed: 'Dismissed',
  lost: 'Lost: stopped before telling you',
};

export function findStatusText(status: FindStatus): string {
  return FIND_STATUS[status] ?? String(status);
}

// What the server names in a refusal, in the user's words.
const FIELDS: readonly (readonly [RegExp, string])[] = [
  [/^quiet_hours from and to must differ\.?$/, 'Quiet hours must start and end at different times.'],
  [/^meeting_lead_min\b/, 'The heads-up time'],
  [/^amount_minor\b/, 'The amount'],
  [/^payment_limit\b/, 'The payment limit'],
  [/^currency\b/, 'The currency'],
  [/^quiet_hours\b/, 'Quiet hours'],
  [/^from\b/, 'The start of quiet hours'],
  [/^to\b/, 'The end of quiet hours'],
];

/** A 422's `why` in plain words. */
export function plainWhy(why: string): string {
  const w = why.trim();
  if (!w) return 'That could not be saved. Check it and try again.';
  for (const [pattern, words] of FIELDS) {
    if (!pattern.test(w)) continue;
    return words.endsWith('.') ? words : sentence(w.replace(pattern, words));
  }
  return sentence(w);
}

// Stripe's own lists: currencies with no minor unit, and those with three digits of it.
const ZERO_DECIMAL = new Set(['bif', 'clp', 'djf', 'gnf', 'jpy', 'kmf', 'krw', 'mga', 'pyg', 'rwf', 'ugx', 'vnd', 'vuv', 'xaf', 'xof', 'xpf']);
const THREE_DECIMAL = new Set(['bhd', 'jod', 'kwd', 'omr', 'tnd']);

export function decimals(currency: string): number {
  const c = currency.trim().toLowerCase();
  return ZERO_DECIMAL.has(c) ? 0 : THREE_DECIMAL.has(c) ? 3 : 2;
}

/** "500" or "1,250.50" in the currency's whole units, to its minor unit, without floats.
 * Empty is null (no limit); anything else that doesn't read is an error to show. */
export function toMinor(text: string, currency: string): number | null | { error: string } {
  const t = text.trim().replace(/[,\s]/g, '');
  if (!t) return null;
  const m = /^(\d+)(?:\.(\d*))?$/.exec(t);
  if (!m) return { error: 'Write the amount as a number, such as 500 or 1250.50.' };
  const d = decimals(currency);
  const fraction = m[2] ?? '';
  if (fraction.length > d) return { error: d === 0 ? 'This currency has no smaller unit: write a whole number.' : `Use at most ${d} digits after the point.` };
  const minor = Number(m[1]) * 10 ** d + Number(fraction.padEnd(d, '0') || '0');
  if (!Number.isSafeInteger(minor) || minor <= 0) return { error: 'Write an amount above zero.' };
  return minor;
}

export function fromMinor(limit: Limit | null): string {
  if (!limit) return '';
  const d = decimals(limit.currency);
  if (d === 0) return String(limit.amount_minor);
  const s = String(limit.amount_minor).padStart(d + 1, '0');
  const whole = s.slice(0, -d);
  const part = s.slice(-d);
  return /^0+$/.test(part) ? whole : `${whole}.${part}`;
}

/** The line under the section's name in the sidebar. */
export function speakingSummary(s: AttentionSettings | null): string {
  if (!s) return '';
  const on = WATCHERS.filter((w) => s.watchers[w]);
  return on.length ? on.map((w) => NAMES[w]).join(', ') : 'Off';
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

type Change = Partial<{
  watchers: Partial<Record<WatcherName, boolean>>;
  meeting_lead_min: number;
  payment_limit: Limit | null;
  quiet_hours: Partial<{ on: boolean; from: string; to: string }>;
  meeting: boolean;
  mic: boolean;
  focus: boolean;
}>;

// While the section is open, "Last checked" keeps up.
const RELOAD_MS = 30_000;

export function createSpeaking(host: Host, api: Api, changed: () => void): { open(): Promise<void>; summary(): string } {
  let state: AttentionState | null = null;
  let finds: readonly Find[] | null = null;
  let loadError = '';
  let busy = false;

  const form = el('form', 'settings connected speaking');
  form.addEventListener('submit', (ev) => ev.preventDefault());
  host.setAttribute('aria-labelledby', 'speaking-title');
  const title = el('h2', '', 'Speaking up');
  title.id = 'speaking-title';
  const note = el(
    'p',
    'settings-note',
    'She tells you first when something needs you, in one line. When she holds her voice, the line still arrives here and as a silent notification.',
  );
  // Kept across redraws, so a refusal is announced when it comes.
  const status = el('p', 'settings-hint connected-warn speaking-error');
  status.setAttribute('role', 'alert');
  status.hidden = true;
  const body = el('div', 'reminders-body');
  form.append(title, note, status, body);
  host.replaceChildren(form);

  function say(message: string): void {
    status.textContent = message;
    status.hidden = !message;
    // It sits under the heading: bring it into sight from wherever the change was made.
    if (message) status.scrollIntoView?.({ block: 'nearest' });
  }

  async function load(): Promise<void> {
    const reading = (async () => {
      try {
        const r = await api('GET', '/api/attention/finds');
        if (r.ok) finds = ((await r.json()) as { finds: Find[] }).finds;
      } catch {
        // What was shown stays.
      }
    })();
    try {
      const r = await api('GET', '/api/attention/settings');
      if (!r.ok) throw new Error(String(r.status));
      state = (await r.json()) as AttentionState;
      loadError = '';
    } catch {
      loadError = state ? '' : 'Speaking up could not be loaded. Is Trillion running?';
    }
    await reading;
    changed();
  }

  /** One change, alone: the answer is the settings as they now stand. */
  async function save(change: Change): Promise<void> {
    busy = true;
    say('');
    try {
      const r = await api('PUT', '/api/attention/settings', change);
      if (r.ok) state = (await r.json()) as AttentionState;
      else if (r.status === 422) {
        const why = ((await r.json().catch(() => ({}))) as { why?: unknown }).why;
        say(plainWhy(typeof why === 'string' ? why : ''));
      } else say('That could not be saved. Try again.');
    } catch {
      say('Trillion could not be reached.');
    }
    busy = false;
    changed();
    // Drawn from what was saved: a refused change goes back to what stands.
    draw();
  }

  async function clearNotes(w: WatcherName): Promise<void> {
    busy = true;
    say('');
    try {
      // A body, so the request is JSON like every other change (SPN-11).
      const r = await api('DELETE', `/api/attention/notes/${encodeURIComponent(w)}`, {});
      if (r.ok) state = (await r.json()) as AttentionState;
      else say(`${NAMES[w]} notes could not be cleared.`);
    } catch {
      say('Trillion could not be reached.');
    }
    busy = false;
    draw();
    (form.querySelector(`[data-focus="watcher:${w}"]`) as HTMLElement | null)?.focus();
  }

  async function dismiss(f: Find): Promise<void> {
    busy = true;
    say('');
    try {
      const r = await api('POST', `/api/attention/finds/${encodeURIComponent(f.key)}/dismiss`, {});
      if (r.ok) finds = ((await r.json()) as { finds: Find[] }).finds;
      else say(r.status === 404 ? 'That one was already gone.' : 'That could not be dismissed.');
    } catch {
      say('Trillion could not be reached.');
    }
    busy = false;
    draw();
    (form.querySelector('[data-focus="finds"]') as HTMLElement | null)?.focus();
  }

  /** An on/off setting, with what it means and what stands behind it beneath. */
  function toggle(id: string, on: boolean, label: string, lines: readonly string[], flip: (on: boolean) => Change): HTMLElement {
    const row = el('label', 'settings-check');
    const box = el('input');
    box.type = 'checkbox';
    box.setAttribute('role', 'switch');
    box.id = `speaking-${id}`;
    box.dataset.focus = id;
    box.checked = on;
    box.disabled = busy;
    box.addEventListener('change', () => void save(flip(box.checked)));
    const words = el('span');
    words.append(el('strong', '', label), ...lines.filter(Boolean).map((l) => el('small', '', l)));
    row.append(box, words);
    return row;
  }

  function field(id: string, label: string, input: HTMLInputElement, hint?: string): HTMLElement {
    const row = el('div', 'settings-row speaking-row');
    const l = el('label', '', label);
    input.id = `speaking-${id}`;
    input.dataset.focus = id;
    l.htmlFor = input.id;
    row.append(l, input);
    if (hint) {
      const h = el('small', 'settings-hint', hint);
      h.id = `speaking-${id}-hint`;
      input.setAttribute('aria-describedby', h.id);
      row.append(h);
    }
    return row;
  }

  function watcher(w: WatcherName, s: AttentionSettings, st: WatcherStatus | undefined, now: number): HTMLElement[] {
    const out: HTMLElement[] = [toggle(`watcher:${w}`, s.watchers[w], NAMES[w], [READS[w], checkText(st, now)], (on) => ({ watchers: { [w]: on } }))];
    if (st?.notes.trim()) {
      const box = el('div', 'connected-card speaking-notes');
      const head = el('div', 'connected-card-head');
      const words = el('span', 'connected-card-words');
      words.append(el('small', 'connected-state', `What you told her about ${NAMES[w].toLowerCase()}:`), el('span', 'speaking-notes-text', st.notes));
      const clear = button('Clear', 'quiet connected-danger', () => {
        if (!busy) void clearNotes(w);
      });
      clear.setAttribute('aria-label', `Clear the ${NAMES[w].toLowerCase()} notes`);
      clear.dataset.focus = `clear:${w}`;
      head.append(words, clear);
      box.append(head);
      out.push(box);
    }
    return out;
  }

  function find(f: Find): HTMLElement {
    const box = el('div', 'connected-card reminders-card');
    const head = el('div', 'connected-card-head');
    const words = el('span', 'connected-card-words');
    const name = WATCHERS.includes(f.watcher as WatcherName) ? NAMES[f.watcher as WatcherName] : f.watcher;
    words.append(el('strong', '', f.title || name));
    if (f.detail) words.append(el('small', 'connected-state', f.detail));
    const lost = f.status === 'lost';
    words.append(el('small', `connected-state${lost ? ' connected-warn' : ''}`, `${name} · ${findStatusText(f.status)} · ${ago(Date.now() - f.first_seen_ms)}`));
    head.append(words);
    if (f.status !== 'dismissed') {
      const b = button('Dismiss', 'quiet', () => {
        if (!busy) void dismiss(f);
      });
      b.setAttribute('aria-label', `Dismiss ${f.title || name}${f.detail ? `: ${f.detail}` : ''}`);
      b.dataset.focus = `dismiss:${f.key}`;
      head.append(b);
    }
    box.append(head);
    return box;
  }

  function draw(): void {
    if (!state) {
      body.replaceChildren(el('p', 'settings-hint', loadError || 'Loading…'));
      return;
    }
    const s = state.settings;
    const st = state.status;
    const now = Date.now();
    const parts: HTMLElement[] = [];

    parts.push(el('h3', 'connected-heading', 'What she watches'));
    for (const w of WATCHERS) parts.push(...watcher(w, s, st.watchers[w], now));

    parts.push(el('h3', 'connected-heading', 'When'));
    const lead = el('input');
    lead.type = 'number';
    lead.min = '2';
    lead.max = '30';
    lead.step = '1';
    lead.inputMode = 'numeric';
    lead.value = String(s.meeting_lead_min);
    lead.disabled = busy;
    lead.addEventListener('change', () => {
      const n = Number(lead.value);
      if (!lead.value.trim() || !Number.isInteger(n)) {
        say('The heads-up time must be a whole number of minutes.');
        return;
      }
      if (n !== s.meeting_lead_min) void save({ meeting_lead_min: n });
    });
    parts.push(field('lead', 'Minutes before a meeting', lead, 'How long before it starts she tells you.'));

    const currency = (s.payment_limit?.currency ?? 'usd').toLowerCase();
    const amount = el('input');
    amount.type = 'text';
    amount.inputMode = 'decimal';
    amount.autocomplete = 'off';
    amount.placeholder = 'No limit';
    amount.value = fromMinor(s.payment_limit);
    amount.disabled = busy;
    const code = el('input', 'speaking-currency');
    code.type = 'text';
    code.autocomplete = 'off';
    code.maxLength = 3;
    code.value = currency;
    code.setAttribute('aria-label', 'Currency');
    code.disabled = busy;
    const sendLimit = (): void => {
      const c = code.value.trim().toLowerCase();
      const minor = toMinor(amount.value, c);
      if (minor !== null && typeof minor === 'object') {
        say(minor.error);
        return;
      }
      const next = minor === null ? null : { amount_minor: minor, currency: c };
      const before = s.payment_limit;
      if (next === null ? before === null : before?.amount_minor === next.amount_minor && before.currency === next.currency) return;
      void save({ payment_limit: next });
    };
    amount.addEventListener('change', sendLimit);
    code.addEventListener('change', sendLimit);
    const limitRow = field(
      'limit',
      'Tell me about payments of at least',
      amount,
      'In your Stripe account’s currency (a three-letter code such as usd). Empty: failed charges and disputes only.',
    );
    limitRow.insertBefore(code, limitRow.querySelector('small'));
    parts.push(limitRow);

    parts.push(el('h3', 'connected-heading', 'Holding her voice'));
    const q = s.quiet_hours;
    parts.push(
      toggle('quiet', q.on, 'Quiet hours', ['For what she notices on her own. A reminder you set for a time still speaks then.'], (on) => ({ quiet_hours: { on } })),
    );
    const from = el('input');
    from.type = 'time';
    from.value = q.from;
    from.disabled = busy || !q.on;
    from.addEventListener('change', () => {
      if (from.value && from.value !== q.from) void save({ quiet_hours: { from: from.value } });
    });
    const to = el('input');
    to.type = 'time';
    to.value = q.to;
    to.disabled = busy || !q.on;
    to.addEventListener('change', () => {
      if (to.value && to.value !== q.to) void save({ quiet_hours: { to: to.value } });
    });
    const hours = el('div', 'speaking-hours');
    hours.append(field('from', 'From', from), field('to', 'To', to));
    parts.push(hours);

    if (s.meeting !== undefined) {
      parts.push(
        toggle('meeting', s.meeting, 'While you are in a meeting', ['From your calendar: a timed meeting with other people, under way now. A heads-up for the next one still speaks. While this is on, Trillion reads your Google Calendar every minute even with Meetings off; nothing from it goes to your model provider unless Meetings is on.'], (on) => ({ meeting: on })),
      );
    }
    const mic = st.probes.mic;
    parts.push(
      toggle('mic', s.mic, 'While another app is using the microphone', [probeText(mic), mic?.note ? sentence(mic.note) : ''], (on) => ({ mic: on })),
    );
    parts.push(toggle('focus', s.focus, 'While Focus or Do Not Disturb is on', [probeText(st.probes.focus)], (on) => ({ focus: on })));

    const recent = el('h3', 'connected-heading', 'Recent (14 days)');
    recent.tabIndex = -1;
    recent.dataset.focus = 'finds';
    parts.push(recent);
    if (finds === null) parts.push(el('p', 'settings-hint', 'Recent finds could not be loaded.'));
    else if (!finds.length) parts.push(el('p', 'settings-hint', 'Nothing yet.'));
    else {
      const list = el('div', 'connected-list');
      list.append(...finds.map(find));
      parts.push(list);
    }

    // Focus stays where it was across the redraw.
    const active = document.activeElement instanceof HTMLElement && body.contains(document.activeElement) ? document.activeElement.dataset.focus : undefined;
    body.replaceChildren(...parts);
    if (active) (body.querySelector(`[data-focus="${CSS.escape(active)}"]`) as HTMLElement | null)?.focus();
  }

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
        else if (!busy && !(document.activeElement instanceof HTMLInputElement && body.contains(document.activeElement))) void load().then(draw);
      }, RELOAD_MS);
      draw();
      await load();
      draw();
    },
    summary: () => speakingSummary(state?.settings ?? null),
  };
}
