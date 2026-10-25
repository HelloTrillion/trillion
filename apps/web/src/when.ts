/**
 * Times on her widgets, in words (the widget design system spec, "The wire format"). Pure: every
 * function takes the clock as an argument, so a card's times are tested at a fixed moment and
 * refreshed by the board's one minute timer.
 *
 * ISO 8601 with an offset or `Z` is an absolute moment; without one it is the user's local time,
 * and a bare date is local midnight. Words are English and fixed, never the browser's locale, so
 * a card reads the same as her spoken reply.
 */

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'] as const;

const ISO = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?(Z|[+-]\d{2}:\d{2})?)?$/;
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

const MIN = 60_000;
const HOUR = 60 * MIN;
/** Up to this far ahead or behind, a time is a count ("in 2 h 10 min"); further, a clock. */
const COUNT_HOURS = 6;

/** The moment an ISO 8601 string names, or null for anything else (or an impossible date). */
export function parseWhen(text: unknown): Date | null {
  if (typeof text !== 'string') return null;
  const m = ISO.exec(text.trim());
  if (!m) return null;
  const [y, mo, d, hh, mm, ss] = [1, 2, 3, 4, 5, 6].map((i) => Number(m[i] ?? 0)) as [number, number, number, number, number, number];
  const ms = Math.round(Number(`0.${m[7] ?? '0'}`) * 1000);
  if (mo < 1 || mo > 12 || d < 1 || d > 31 || hh > 23 || mm > 59 || ss > 59) return null;
  const zone = m[8];
  let at: Date;
  if (zone) {
    const offset = zone === 'Z' ? 0 : (zone[0] === '-' ? -1 : 1) * (Number(zone.slice(1, 3)) * 60 + Number(zone.slice(4, 6)));
    at = new Date(Date.UTC(y, mo - 1, d, hh, mm, ss, ms) - offset * MIN);
    // A day past the month's end (Feb 30) rolls over; the date as written must survive.
    const check = new Date(Date.UTC(y, mo - 1, d));
    if (check.getUTCDate() !== d) return null;
  } else {
    at = new Date(y, mo - 1, d, hh, mm, ss, ms);
    if (at.getDate() !== d) return null;
  }
  return Number.isNaN(at.getTime()) ? null : at;
}

/** Whether a string is a date with no time of day ("2026-10-28"). */
export function isDateOnly(text: unknown): boolean {
  return typeof text === 'string' && DATE_ONLY.test(text.trim());
}

const toDate = (at: Date | string): Date | null => (typeof at === 'string' ? parseWhen(at) : Number.isNaN(at.getTime()) ? null : at);

/** "3:00 pm", "12:15 pm", "9:05 am". */
export function clock(at: Date): string {
  const h = at.getHours();
  return `${h % 12 || 12}:${String(at.getMinutes()).padStart(2, '0')} ${h < 12 ? 'am' : 'pm'}`;
}

/** Local midnight of a moment's day. */
const dayStart = (at: Date): number => new Date(at.getFullYear(), at.getMonth(), at.getDate()).getTime();

/** Calendar days from `now`'s day to `at`'s day: 0 today, 1 tomorrow, -1 yesterday. DST-safe. */
function dayGap(at: Date, now: Date): number {
  return Math.round((dayStart(at) - dayStart(now)) / (24 * HOUR));
}

/** "Oct 12", with the year when it is not this one. */
function dateWords(at: Date, now: Date): string {
  const base = `${MONTHS[at.getMonth()]} ${at.getDate()}`;
  return at.getFullYear() === now.getFullYear() ? base : `${base}, ${at.getFullYear()}`;
}

/** A day by name near today, else its date: "today", "yesterday", "Tue", "Oct 12". */
function dayWords(at: Date, now: Date): string {
  const gap = dayGap(at, now);
  if (gap === 0) return 'today';
  if (gap === 1) return 'tomorrow';
  if (gap === -1) return 'yesterday';
  if (Math.abs(gap) <= 6) return DAYS[at.getDay()] ?? '';
  return dateWords(at, now);
}

/** "25 min", "2 h", "2 h 10 min". */
function span(ms: number): string {
  const total = Math.round(Math.abs(ms) / MIN);
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (h === 0) return `${m} min`;
  return m === 0 ? `${h} h` : `${h} h ${m} min`;
}

/**
 * When something is, from `now`: "now", "in 25 min", "in 2 h 10 min", "5 min ago",
 * "yesterday 3:00 pm", "Tue 3:00 pm" (within 6 days), "Oct 12" (further). `started` says a past
 * moment is when something began ("started 5 min ago"). A bare date is a day: "today", "Tue".
 */
export function relative(at: Date | string, now: Date, started = false): string {
  const when = toDate(at);
  if (!when) return '';
  if (isDateOnly(at)) return dayWords(when, now);
  const diff = when.getTime() - now.getTime();
  if (Math.abs(diff) < MIN) return started && diff < 0 ? 'started just now' : 'now';
  if (Math.abs(diff) < COUNT_HOURS * HOUR) return diff > 0 ? `in ${span(diff)}` : `${started ? 'started ' : ''}${span(diff)} ago`;
  const gap = dayGap(when, now);
  if (Math.abs(gap) > 6) return dateWords(when, now);
  return `${started && diff < 0 ? 'started ' : ''}${dayWords(when, now)} ${clock(when)}`;
}

/**
 * Where an event stands: before it, when it starts ("in 25 min"); during it, "started 5 min ago";
 * after its end, when it ended ("ended 10 min ago"). With no end it is under way for an hour.
 */
export function countdown(at: Date | string, end: Date | string | undefined, now: Date): string {
  const start = toDate(at);
  if (!start) return '';
  if (isDateOnly(at)) return relative(at, now);
  const stop = (end !== undefined ? toDate(end) : null) ?? new Date(start.getTime() + HOUR);
  if (now < start) return relative(start, now);
  if (now < stop) return relative(start, now, true);
  return end !== undefined ? `ended ${relative(stop, now)}` : relative(start, now);
}

/**
 * From one time to another, shortly: "2:30–3:00 pm", "11:30 am–12:15 pm", across days
 * "Tue 3:00 pm–Wed 9:00 am" (more than six days apart, "Oct 27 3:00 pm–Nov 5 9:00 am"). With no end, the time alone. Days alone: "Oct 28–30", "Oct 28–Nov 2".
 */
export function range(at: Date | string, end?: Date | string): string {
  const a = toDate(at);
  if (!a) return '';
  const b = end !== undefined ? toDate(end) : null;
  if (isDateOnly(at)) {
    const first = `${MONTHS[a.getMonth()]} ${a.getDate()}`;
    if (!b || dayStart(a) === dayStart(b)) return first;
    return a.getMonth() === b.getMonth() && a.getFullYear() === b.getFullYear() ? `${first}–${b.getDate()}` : `${first}–${MONTHS[b.getMonth()]} ${b.getDate()}`;
  }
  if (!b) return clock(a);
  if (dayStart(a) !== dayStart(b)) {
    // Within the week a weekday is clear; further apart, two Tuesdays would not be.
    const far = Math.abs(dayGap(b, a)) > 6;
    const day = (d: Date): string => (far ? `${MONTHS[d.getMonth()]} ${d.getDate()}` : (DAYS[d.getDay()] ?? ''));
    return `${day(a)} ${clock(a)}–${day(b)} ${clock(b)}`;
  }
  const sameHalf = a.getHours() < 12 === b.getHours() < 12;
  const left = sameHalf ? clock(a).replace(/ [ap]m$/, '') : clock(a);
  return `${left}–${clock(b)}`;
}

/** How old something is (a card's source): "just now", "2 min ago", "1 h ago", "yesterday". */
export function age(at: Date | string, now: Date): string {
  const when = toDate(at);
  if (!when) return '';
  const diff = now.getTime() - when.getTime();
  if (diff < MIN) return 'just now';
  if (diff < HOUR) return `${Math.floor(diff / MIN)} min ago`;
  if (diff < 24 * HOUR) return `${Math.floor(diff / HOUR)} h ago`;
  const gap = -dayGap(when, now);
  if (gap <= 1) return 'yesterday';
  if (gap <= 6) return `${gap} days ago`;
  return dateWords(when, now);
}
