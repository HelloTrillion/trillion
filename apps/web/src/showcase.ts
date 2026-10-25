/**
 * A design preview of what the workspace could become, with made-up content. It opens only
 * from the URL (`#showcase`, `?showcase`, or `#showcase=decide` for one scene) and talks to
 * nothing: every button here changes this page and nothing else.
 */
import { type Workspace } from './workspace';

export type SceneId = 'briefing' | 'working' | 'decide' | 'compare';

export const SCENES: readonly { readonly id: SceneId; readonly label: string }[] = [
  { id: 'briefing', label: 'Briefing' },
  { id: 'working', label: 'Working' },
  { id: 'decide', label: 'Decide' },
  { id: 'compare', label: 'Compare' },
];

const isScene = (s: string): s is SceneId => SCENES.some((x) => x.id === s);

/**
 * Which scene the URL asks for: the first one for a bare flag, null when there is no flag.
 * Looked for in the query and the fragment, alone or among `&`-joined others.
 */
export function showcaseScene(search: string, hash: string): SceneId | null {
  for (const part of [search, hash]) {
    const m = /(?:^[?#]|&)showcase(?:=([\w-]*))?(?=&|$)/.exec(part);
    if (!m) continue;
    const want = m[1] ?? '';
    return isScene(want) ? want : 'briefing';
  }
  return null;
}

export type StepState = 'pending' | 'running' | 'done';

/** Each step's state `elapsed` ms in, the steps running one after another for their durations. */
export function stepStates(elapsed: number, durations: readonly number[]): StepState[] {
  let start = 0;
  return durations.map((d) => {
    const s: StepState = elapsed >= start + d ? 'done' : elapsed >= start ? 'running' : 'pending';
    start += d;
    return s;
  });
}

/** How far through step `i` it is, 0 to 1. */
export function stepProgress(elapsed: number, durations: readonly number[], i: number): number {
  const start = durations.slice(0, i).reduce((a, b) => a + b, 0);
  const d = durations[i] ?? 0;
  return d <= 0 ? 1 : Math.min(1, Math.max(0, (elapsed - start) / d));
}

/** Seconds as m:ss. */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** Minutes after midnight as a short time: 9:42, 12:30. */
export function timeOfDay(minutes: number): string {
  const m = ((Math.round(minutes) % 1440) + 1440) % 1440;
  const h = Math.floor(m / 60) % 12 || 12;
  return `${h}:${String(m % 60).padStart(2, '0')}`;
}

// ── DOM helpers ─────────────────────────────────────────────────────────

function h<K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, text?: string): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}

const SVG_NS = 'http://www.w3.org/2000/svg';
function s(tag: string, attrs: Record<string, string | number> = {}, ...kids: SVGElement[]): SVGElement {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  el.append(...kids);
  return el;
}
function svgIcon(size: number, view: string, cls: string, ...kids: SVGElement[]): SVGElement {
  return s('svg', { width: size, height: size, viewBox: view, class: cls, 'aria-hidden': 'true', fill: 'none' }, ...kids);
}
const check = (size = 14): SVGElement =>
  svgIcon(size, '0 0 16 16', 'sc-tick', s('path', { d: 'M3.5 8.5l3 3l6-7', stroke: 'currentColor', 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));

/** Staggered entrance: the n-th thing in a scene arrives a beat after the one before. */
function stagger<T extends HTMLElement>(el: T, i: number): T {
  el.classList.add('sc-rise');
  el.style.setProperty('--i', String(i));
  return el;
}

interface Ctx {
  readonly reduced: boolean;
  every(ms: number, fn: () => void): void;
  go(id: SceneId): void;
}

/** What she says over a scene: the workspace shows, the voice tells. */
function says(text: string): HTMLElement {
  const row = stagger(h('div', 'sc-says'), 0);
  const wave = h('span', 'sc-wave');
  wave.setAttribute('aria-hidden', 'true');
  for (let i = 0; i < 5; i++) {
    const bar = h('i');
    bar.style.setProperty('--b', String(i));
    wave.append(bar);
  }
  const q = h('q', 'sc-says-text', text);
  row.append(wave, q);
  return row;
}

function tile(title: string, cls: string, i: number): { el: HTMLElement; body: HTMLElement } {
  const el = stagger(h('section', `sc-tile ${cls}`), i);
  const head = h('h3', 'sc-tile-title', title);
  const body = h('div', 'sc-tile-body');
  el.append(head, body);
  return { el, body };
}

// ── Briefing ────────────────────────────────────────────────────────────

const DAY = { start: 7 * 60, end: 22 * 60 } as const;
const EVENTS: readonly { at: number; to: number; title: string; kind: string }[] = [
  { at: 7 * 60 + 30, to: 8 * 60 + 15, title: 'Run', kind: 'self' },
  { at: 10 * 60, to: 11 * 60, title: 'Coffee with Sam', kind: 'people' },
  { at: 12 * 60 + 30, to: 15 * 60, title: 'Picnic at Maple Park', kind: 'plan' },
  { at: 16 * 60 + 30, to: 17 * 60 + 15, title: 'Call the plumber', kind: 'task' },
  { at: 19 * 60, to: 20 * 60 + 30, title: 'Dinner with Alex', kind: 'people' },
];
const pct = (min: number): string => `${(((min - DAY.start) / (DAY.end - DAY.start)) * 100).toFixed(3)}%`;

function weatherArt(): SVGElement {
  const rays: SVGElement[] = [];
  for (let i = 0; i < 8; i++) {
    const a = (i * Math.PI) / 4;
    const x1 = 26 + Math.cos(a) * 15;
    const y1 = 24 + Math.sin(a) * 15;
    rays.push(s('line', { x1, y1, x2: 26 + Math.cos(a) * 20, y2: 24 + Math.sin(a) * 20 }));
  }
  return svgIcon(
    76,
    '0 0 76 60',
    'sc-weather-art',
    s('g', { class: 'sc-rays', stroke: 'currentColor', 'stroke-width': 2.5, 'stroke-linecap': 'round' }, ...rays),
    s('circle', { cx: 26, cy: 24, r: 10, class: 'sc-sun' }),
    s('path', { class: 'sc-cloud', d: 'M30 50h30a10 10 0 0 0 0-20a14 14 0 0 0-26-3a9 9 0 0 0-4 23z' }),
  );
}

function briefing(ctx: Ctx): HTMLElement {
  const root = h('div', 'sc-scene sc-briefing');
  root.append(says('Morning. It’s a quiet one: two things need you, and it stays dry until four.'));

  const hello = stagger(h('header', 'sc-hello'), 1);
  const date = new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' });
  hello.append(h('p', 'sc-eyebrow', date), h('h2', 'sc-greeting', 'Good morning.'), h('p', 'sc-lede', 'Five things on today. The picnic is the big one.'));

  // The day, left to right, with a line for now that walks across it.
  const day = stagger(h('section', 'sc-day'), 2);
  day.setAttribute('aria-label', 'Today');
  const track = h('div', 'sc-day-track');
  for (let hr = 7; hr <= 22; hr += 3) {
    const tick = h('span', 'sc-day-hour', hr === 12 ? 'Noon' : `${hr % 12 || 12}${hr < 12 ? ' am' : hr === 22 ? ' pm' : ''}`);
    tick.style.left = pct(hr * 60);
    track.append(tick);
  }
  const blocks = EVENTS.map((e, i) => {
    const b = h('div', `sc-event sc-event-${e.kind}`);
    b.style.left = pct(e.at);
    b.style.width = `calc(${pct(e.to)} - ${pct(e.at)})`;
    b.style.setProperty('--lane', String(i % 2));
    b.append(h('strong', '', e.title), h('span', '', `${timeOfDay(e.at)}–${timeOfDay(e.to)}`));
    b.title = e.title;
    track.append(b);
    return { b, e };
  });
  const past = h('div', 'sc-day-past');
  const now = h('div', 'sc-now');
  const nowLabel = h('span', 'sc-now-label');
  now.append(nowLabel);
  track.append(past, now);
  day.append(track);

  let minute = 9 * 60 + 42;
  function placeNow(): void {
    now.style.left = pct(minute);
    past.style.width = pct(minute);
    nowLabel.textContent = timeOfDay(minute);
    for (const { b, e } of blocks) {
      b.toggleAttribute('data-past', e.to <= minute);
      b.toggleAttribute('data-now', e.at <= minute && minute < e.to);
    }
  }
  placeNow();
  // A sample clock, a minute a second, so the line is seen to move.
  if (!ctx.reduced)
    ctx.every(1000, () => {
      minute = minute >= DAY.end ? DAY.start : minute + 1;
      placeNow();
    });

  const tiles = h('div', 'sc-tiles');

  const needs = tile('Needs you', 'sc-needs', 3);
  const needsList = h('ul', 'sc-list');
  for (const [what, sub] of [
    ['Reply to Sam about Saturday', 'Drafted · three lines'],
    ['Renew two library books', 'Due Monday'],
  ] as const) {
    const li = h('li', 'sc-need');
    const text = h('div', 'sc-need-text');
    text.append(h('strong', '', what), h('span', '', sub));
    const ok = h('button', 'sc-mini', 'Approve');
    ok.type = 'button';
    ok.addEventListener('click', () => {
      li.dataset['done'] = '';
      ok.replaceChildren(check(12), document.createTextNode(' Done'));
      ok.disabled = true;
    });
    li.append(text, ok);
    needsList.append(li);
  }
  needs.body.append(needsList);

  const weather = tile('Weather', 'sc-weather', 4);
  const wNow = h('div', 'sc-weather-now');
  const temp = h('div', 'sc-temp');
  temp.append(h('span', 'sc-temp-big', '19°'), h('span', 'sc-temp-range', 'High 21° · Low 11°'));
  wNow.append(weatherArt(), temp);
  const hours = h('ol', 'sc-hours');
  for (const [at, t, rain] of [
    ['9', 14, false],
    ['12', 18, false],
    ['3', 21, false],
    ['6', 16, true],
    ['9', 12, true],
  ] as const) {
    const li = h('li', rain ? 'sc-hour sc-hour-rain' : 'sc-hour');
    const bar = h('i');
    bar.style.setProperty('--t', String((t - 10) / 12));
    li.append(h('span', '', `${t}°`), bar, h('small', '', at));
    hours.append(li);
  }
  weather.body.append(wNow, hours, h('p', 'sc-note', 'Dry until about 4, then light rain.'));

  const rem = tile('Reminders', 'sc-reminders', 5);
  const remList = h('ul', 'sc-list');
  for (const [at, what] of [
    ['10:00', 'Bring the blue blanket'],
    ['3:30', 'Pick up bread on Maple Street'],
    ['Tomorrow', 'Water the plants'],
  ] as const) {
    const li = h('li', 'sc-reminder');
    const box = h('button', 'sc-check');
    box.type = 'button';
    box.setAttribute('aria-pressed', 'false');
    box.setAttribute('aria-label', `Done: ${what}`);
    box.append(check(12));
    box.addEventListener('click', () => {
      const on = box.getAttribute('aria-pressed') !== 'true';
      box.setAttribute('aria-pressed', String(on));
      li.toggleAttribute('data-done', on);
    });
    li.append(box, h('span', 'sc-when', at), h('span', 'sc-what', what));
    remList.append(li);
  }
  rem.body.append(remList);

  tiles.append(needs.el, weather.el, rem.el);
  root.append(hello, day, tiles);
  return root;
}

// ── Working ─────────────────────────────────────────────────────────────

const AGENTS = [
  { id: 'weather', label: 'Weather', ring: 0 },
  { id: 'maps', label: 'Maps', ring: 1 },
  { id: 'calendar', label: 'Calendar', ring: 2 },
] as const;

const STEPS: readonly { title: string; agent: string | null; ms: number; sources: readonly string[] }[] = [
  { title: 'Check Saturday’s forecast', agent: 'weather', ms: 2600, sources: ['Forecast · Saturday'] },
  { title: 'Find parks within 20 minutes', agent: 'maps', ms: 3200, sources: ['Maple Park', 'Riverside Green', 'Hilltop Meadow'] },
  { title: 'Find a free afternoon', agent: 'calendar', ms: 2400, sources: ['Your calendar'] },
  { title: 'Draft the plan', agent: null, ms: 4600, sources: [] },
  { title: 'Ask you before telling Sam', agent: null, ms: 1400, sources: [] },
];
const DURATIONS = STEPS.map((x) => x.ms);
const TOTAL = DURATIONS.reduce((a, b) => a + b, 0);
const HOLD = 6000;
const DRAFT_STEP = 3;
const DRAFT = [
  'Saturday picnic',
  '· Maple Park, east lawn: shade until 3',
  '· Leave 12:10, home by 3:30',
  '· Dry, 19°, a light breeze',
  '· Bring the blanket, fruit and the speaker',
].join('\n');

function working(ctx: Ctx): HTMLElement {
  const root = h('div', 'sc-scene sc-working');
  root.append(says('On it. I’m checking the weather and a few parks at once.'));

  const task = stagger(h('header', 'sc-task'), 1);
  const status = h('span', 'sc-task-status');
  task.append(h('p', 'sc-eyebrow', 'Working on'), h('h2', 'sc-task-title', 'Planning Saturday’s picnic'), status);

  const grid = h('div', 'sc-work-grid');

  // A small orb with her helpers circling it; a helper lights up, and reaches in, while it works.
  const orbit = stagger(h('div', 'sc-orbit'), 2);
  orbit.setAttribute('aria-hidden', 'true');
  const core = h('div', 'sc-core');
  orbit.append(core);
  const sats = new Map<string, HTMLElement>();
  for (const [i, a] of AGENTS.entries()) {
    const ring = h('div', `sc-ring sc-ring-${a.ring}`);
    ring.style.setProperty('--a', `${i * 130 + 20}deg`);
    const sat = h('div', `sc-sat sc-sat-${a.id}`);
    sat.append(h('span', 'sc-sat-label', a.label));
    ring.append(h('i', 'sc-beam'), sat);
    orbit.append(ring);
    sats.set(a.id, ring);
  }

  const steps = stagger(h('ol', 'sc-steps'), 3);
  const rows = STEPS.map((st) => {
    const li = h('li', 'sc-step');
    const mark = h('span', 'sc-step-mark');
    mark.append(check(12));
    const text = h('div', 'sc-step-text');
    const who = st.agent ? (AGENTS.find((a) => a.id === st.agent)?.label ?? '') : 'Trillion';
    text.append(h('strong', '', st.title), h('span', 'sc-step-who', who));
    li.append(mark, text);
    steps.append(li);
    return li;
  });

  const side = h('div', 'sc-work-side');
  const sourcesBox = stagger(h('section', 'sc-sources'), 4);
  const chips = h('div', 'sc-chips');
  sourcesBox.append(h('h3', 'sc-tile-title', 'Sources'), chips);
  const draft = stagger(h('section', 'sc-draft'), 5);
  const draftText = h('pre', 'sc-draft-text');
  const caret = h('span', 'sc-caret');
  const draftOut = h('span');
  draftText.append(draftOut, caret);
  const review = h('button', 'sc-primary', 'Review the message');
  review.type = 'button';
  review.addEventListener('click', () => ctx.go('decide'));
  const draftFoot = h('div', 'sc-draft-foot');
  draftFoot.append(h('span', 'sc-note', 'Nothing goes to Sam until you say so.'), review);
  draft.append(h('h3', 'sc-tile-title', 'Draft plan'), draftText, draftFoot);
  side.append(sourcesBox, draft);

  const left = h('div', 'sc-work-left');
  left.append(orbit, steps);
  grid.append(left, side);
  root.append(task, grid);

  let shown: StepState[] = [];
  let chipCount = 0;
  function render(t: number): void {
    const states = stepStates(t, DURATIONS);
    states.forEach((st, i) => {
      if (shown[i] === st) return;
      const row = rows[i];
      if (row) row.dataset['state'] = st;
    });
    const running = states.indexOf('running');
    const agent = running >= 0 ? (STEPS[running]?.agent ?? null) : null;
    for (const [id, ring] of sats) ring.toggleAttribute('data-live', id === agent);
    core.dataset['state'] = running < 0 ? (states.every((x) => x === 'done') ? 'done' : 'idle') : 'working';
    status.textContent =
      running >= 0 ? `Step ${running + 1} of ${STEPS.length}` : states.every((x) => x === 'done') ? 'Ready for you' : 'Starting';
    status.dataset['state'] = running >= 0 ? 'working' : 'done';

    // Sources arrive as each step finishes.
    const want = STEPS.flatMap((st, i) => (states[i] === 'done' ? st.sources : []));
    if (want.length < chipCount) {
      chips.replaceChildren();
      chipCount = 0;
    }
    for (; chipCount < want.length; chipCount++) chips.append(h('span', 'sc-chip', want[chipCount]));
    chips.toggleAttribute('data-empty', want.length === 0);

    const typed = Math.round(stepProgress(t, DURATIONS, DRAFT_STEP) * DRAFT.length);
    if (draftOut.textContent?.length !== typed) draftOut.textContent = DRAFT.slice(0, typed);
    draft.dataset['state'] = states[DRAFT_STEP] ?? 'pending';
    review.hidden = states[STEPS.length - 1] !== 'done';
    shown = states;
  }

  if (ctx.reduced) {
    render(TOTAL);
  } else {
    const t0 = performance.now();
    render(0);
    // Runs through, rests on the finished plan, then starts over.
    ctx.every(60, () => render((performance.now() - t0) % (TOTAL + HOLD)));
  }
  return root;
}

// ── Decide ──────────────────────────────────────────────────────────────

const ASK_SECONDS = 300;
const MESSAGE =
  'Hi Sam! Saturday’s on: Maple Park, the east lawn, 12:30. I’ll bring the blanket and fruit. Could you bring drinks?';

function decide(ctx: Ctx): HTMLElement {
  const root = h('div', 'sc-scene sc-decide');
  root.append(says('I’ve written it. Shall I send it to Sam?'));

  const grid = h('div', 'sc-decide-grid');
  const card = stagger(h('section', 'sc-approve'), 1);
  card.setAttribute('aria-labelledby', 'sc-approve-title');

  const head = h('header', 'sc-approve-head');
  const kind = svgIcon(
    22,
    '0 0 24 24',
    'sc-approve-icon',
    s('path', { d: 'M4 5.5h16v10H9l-5 4z', stroke: 'currentColor', 'stroke-width': 1.6, 'stroke-linejoin': 'round' }),
  );
  const titles = h('div');
  const title = h('h2', 'sc-approve-title', 'Send this message to Sam?');
  title.id = 'sc-approve-title';
  titles.append(h('p', 'sc-eyebrow', 'Needs your OK'), title);

  // The ask runs out: say nothing, and nothing is sent.
  const R = 20;
  const C = 2 * Math.PI * R;
  const arc = s('circle', { cx: 24, cy: 24, r: R, class: 'sc-ring-arc', 'stroke-dasharray': C.toFixed(2) });
  const timer = h('div', 'sc-timer');
  const timerText = h('span', 'sc-timer-text');
  timer.append(svgIcon(48, '0 0 48 48', 'sc-timer-ring', s('circle', { cx: 24, cy: 24, r: R, class: 'sc-ring-bg' }), arc), timerText);
  timer.setAttribute('role', 'timer');
  head.append(kind, titles, timer);

  const preview = h('div', 'sc-message');
  const to = h('p', 'sc-message-to');
  to.append(h('span', 'sc-avatar', 'S'), h('strong', '', 'Sam'), h('span', '', 'Messages'));
  const bubble = h('p', 'sc-bubble', MESSAGE);
  preview.append(to, bubble);

  const risk = h('div', 'sc-risk');
  risk.append(
    svgIcon(18, '0 0 24 24', 'sc-risk-icon', s('path', { d: 'M12 4l9 16H3z', stroke: 'currentColor', 'stroke-width': 1.6, 'stroke-linejoin': 'round' }), s('path', { d: 'M12 10v4.5M12 17.2v.01', stroke: 'currentColor', 'stroke-width': 1.8, 'stroke-linecap': 'round' })),
    h('p', '', 'This sends a message as you. Once it’s sent I can’t take it back, so I’m asking first.'),
  );

  const actions = h('div', 'sc-actions');
  const approve = h('button', 'sc-primary', 'Approve');
  const edit = h('button', 'sc-secondary', 'Edit');
  const later = h('button', 'sc-quiet', 'Not now');
  for (const b of [approve, edit, later]) b.type = 'button';
  actions.append(approve, edit, later, h('span', 'sc-voice-hint', 'or just say “send it”'));

  const outcome = h('div', 'sc-outcome');
  outcome.setAttribute('role', 'status');
  const outcomeText = h('p');
  const again = h('button', 'sc-secondary', 'Ask again');
  again.type = 'button';
  outcome.append(outcomeText, again);

  card.append(head, preview, risk, actions, outcome);

  const checked = stagger(h('aside', 'sc-checked'), 2);
  const list = h('ul', 'sc-checked-list');
  ['Sam is in your contacts', 'Saturday afternoon is free', 'The forecast is dry', 'Only Sam will get it'].forEach((t, i) => {
    const li = h('li', '', t);
    li.prepend(check(13));
    li.style.setProperty('--i', String(i + 3));
    list.append(li);
  });
  checked.append(h('h3', 'sc-tile-title', 'What I checked'), list, h('p', 'sc-note', 'If you don’t answer, it isn’t sent.'));

  grid.append(card, checked);
  root.append(grid);

  let left = ASK_SECONDS;
  function tick(): void {
    timerText.textContent = clock(left);
    timer.setAttribute('aria-label', `${clock(left)} left to answer`);
    arc.setAttribute('stroke-dashoffset', ((1 - left / ASK_SECONDS) * C).toFixed(2));
    timer.toggleAttribute('data-low', left <= 60);
  }
  function settle(state: 'sent' | 'held' | 'expired'): void {
    const asked = state !== 'expired';
    card.dataset['state'] = state;
    bubble.contentEditable = 'false';
    outcomeText.textContent =
      state === 'sent'
        ? 'Sent to Sam. (Sample: nothing was sent.)'
        : state === 'held'
          ? 'Okay, I’ll hold on to it. Ask me any time.'
          : 'Time ran out, so I didn’t send it.';
    // Running out on its own does not take focus from wherever the user is.
    if (asked) again.focus();
  }
  function reset(): void {
    left = ASK_SECONDS;
    delete card.dataset['state'];
    tick();
    approve.focus();
  }
  approve.addEventListener('click', () => settle('sent'));
  later.addEventListener('click', () => settle('held'));
  again.addEventListener('click', reset);
  edit.addEventListener('click', () => {
    const on = bubble.isContentEditable;
    bubble.contentEditable = on ? 'false' : 'true';
    edit.textContent = on ? 'Edit' : 'Done editing';
    card.toggleAttribute('data-editing', !on);
    if (!on) bubble.focus();
  });
  tick();
  ctx.every(1000, () => {
    if (card.dataset['state']) return;
    left -= 1;
    tick();
    if (left <= 0) settle('expired');
  });
  return root;
}

// ── Compare ─────────────────────────────────────────────────────────────

const SPOTS = [
  {
    name: 'Maple Park',
    art: 'a',
    drive: '12 min',
    metrics: { Shade: 0.85, Quiet: 0.6, Space: 0.7 },
    tags: ['Shade trees', 'Restrooms', 'Tables'],
    note: 'Shade until 3, and the closest. Easy for an afternoon.',
  },
  {
    name: 'Riverside Green',
    art: 'b',
    drive: '18 min',
    metrics: { Shade: 0.4, Quiet: 0.75, Space: 0.9 },
    tags: ['By the water', 'Dogs welcome'],
    note: 'Lovely, but breezy by the river after 2.',
  },
  {
    name: 'Hilltop Meadow',
    art: 'c',
    drive: '25 min',
    metrics: { Shade: 0.2, Quiet: 0.95, Space: 0.8 },
    tags: ['Big views', 'Short climb'],
    note: 'The best view, with little shade: hot by 1.',
  },
] as const;
const PICK = 0;

function compare(): HTMLElement {
  const root = h('div', 'sc-scene sc-compare');
  const line = says('Three good spots. I’d go with Maple Park.');
  const lineText = line.querySelector('q');
  root.append(line);

  const head = stagger(h('header', 'sc-task'), 1);
  head.append(h('p', 'sc-eyebrow', 'Options for Saturday'), h('h2', 'sc-task-title', 'Where to have the picnic'));

  const cards = h('div', 'sc-options');
  const buttons: HTMLButtonElement[] = [];
  SPOTS.forEach((spot, i) => {
    const card = stagger(h('article', 'sc-option'), i + 2);
    if (i === PICK) card.dataset['pick'] = '';
    const hero = h('div', `sc-hero sc-hero-${spot.art}`);
    hero.setAttribute('aria-hidden', 'true');
    hero.append(h('i', 'sc-hero-sun'), h('i', 'sc-hero-far'), h('i', 'sc-hero-near'));
    if (i === PICK) hero.append(h('span', 'sc-pick-badge', 'She’d pick'));

    const body = h('div', 'sc-option-body');
    const top = h('div', 'sc-option-top');
    top.append(h('h3', 'sc-option-name', spot.name), h('span', 'sc-drive', spot.drive));
    const bars = h('dl', 'sc-bars');
    for (const [k, v] of Object.entries(spot.metrics)) {
      const bar = h('dd');
      const fill = h('i');
      fill.style.setProperty('--v', String(v));
      bar.append(fill);
      bars.append(h('dt', '', k), bar);
    }
    const tags = h('div', 'sc-tags');
    for (const t of spot.tags) tags.append(h('span', 'sc-tag', t));
    const note = h('p', i === PICK ? 'sc-reason' : 'sc-note', spot.note);
    const choose = h('button', 'sc-secondary sc-choose', 'Choose');
    choose.type = 'button';
    choose.setAttribute('aria-pressed', 'false');
    choose.addEventListener('click', () => {
      for (const [j, b] of buttons.entries()) {
        b.setAttribute('aria-pressed', String(i === j));
        b.textContent = i === j ? 'Chosen' : 'Choose';
        b.closest('.sc-option')?.toggleAttribute('data-chosen', i === j);
      }
      if (lineText) lineText.textContent = i === PICK ? 'Maple Park it is. I’ll add it to the plan.' : `${spot.name} it is. I’ll pack an extra layer.`;
    });
    buttons.push(choose);
    body.append(top, bars, tags, note, choose);
    card.append(hero, body);
    cards.append(card);
  });
  root.append(head, cards);
  return root;
}

const BUILD: Record<SceneId, (ctx: Ctx) => HTMLElement> = { briefing, working, decide, compare };

// ── The preview ─────────────────────────────────────────────────────────

// One close listener for every opening; it stops the timers of the one shown.
let heard = false;
let stopShown = (): void => {};

/** Open the preview in the workspace at `first`. Its timers stop when it closes. */
export function openShowcase(workspace: Workspace, first: SceneId = 'briefing'): void {
  if (workspace.showing === 'showcase') return;
  const root = h('div', 'sc');
  const head = h('div', 'sc-head');
  const tabs = h('div', 'sc-switch');
  tabs.setAttribute('role', 'tablist');
  tabs.setAttribute('aria-label', 'Preview scenes');
  const badge = h('span', 'sc-badge', 'Preview · sample content');
  head.append(tabs, badge);
  const stage = h('div', 'sc-stage');
  stage.setAttribute('role', 'tabpanel');
  stage.id = 'sc-stage';
  root.append(head, stage);

  const timers: number[] = [];
  const stop = (): void => {
    for (const t of timers.splice(0)) clearInterval(t);
  };
  stopShown = stop;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const ctx: Ctx = {
    reduced,
    every: (ms, fn) => void timers.push(window.setInterval(fn, ms)),
    go: (id) => show(id, true),
  };

  const buttons = SCENES.map((sc) => {
    const b = h('button', 'sc-tab', sc.label);
    b.type = 'button';
    b.setAttribute('role', 'tab');
    b.setAttribute('aria-controls', stage.id);
    b.addEventListener('click', () => show(sc.id, false));
    tabs.append(b);
    return b;
  });
  tabs.addEventListener('keydown', (ev) => {
    const i = buttons.findIndex((b) => b === document.activeElement);
    if (i < 0) return;
    const step = ev.key === 'ArrowRight' ? 1 : ev.key === 'ArrowLeft' ? -1 : 0;
    if (!step) return;
    ev.preventDefault();
    const next = SCENES[(i + step + SCENES.length) % SCENES.length];
    if (next) show(next.id, true);
  });

  function show(id: SceneId, focusTab: boolean): void {
    stop();
    SCENES.forEach((sc, i) => {
      const b = buttons[i];
      if (!b) return;
      const on = sc.id === id;
      b.setAttribute('aria-selected', String(on));
      b.tabIndex = on ? 0 : -1;
      if (on) {
        tabs.style.setProperty('--at', String(i));
        stage.setAttribute('aria-label', sc.label);
        if (focusTab) b.focus();
      }
    });
    stage.replaceChildren(BUILD[id](ctx));
    stage.scrollTop = 0;
    root.closest('.workspace-body')?.scrollTo({ top: 0 });
  }

  show(first, false);
  if (!heard) {
    heard = true;
    workspace.onClose((id) => {
      if (id === 'showcase') stopShown();
    });
  }
  workspace.open('Workspace preview', root, 'showcase');
}
