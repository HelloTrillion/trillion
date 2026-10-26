/**
 * The blocks every widget on her board is made of (the widget design system spec, "Blocks"). The
 * server expands every widget into blocks; this draws them, one function per block, and decides
 * order, spacing, size and emphasis: she picks the content, never the look.
 *
 * Everything here may quote a page she read (SPN-5): every piece of text is set with textContent
 * and nothing is ever parsed as HTML. Times carry `data-at` and are refreshed by `tick`, so one
 * timer for the board keeps "in 25 min" true. A block this page does not know (a newer server) is
 * skipped and the rest of the card still draws.
 */
import { type ChartKind, type ChartLayout, type ChartSpec, areaPath, formatValue, layoutChart, linePath } from './chart';
import { icon } from './icons';
import { hourLabel } from './showcase';
import { countdown, range, relative } from './when';

// ── Pure helpers ────────────────────────────────────────────────────────

export type BlockTone = 'good' | 'bad' | 'attention' | 'neutral';

/** A tone word as the server sends it (or the widgets' old up and down); anything else is neutral. */
export function toneOfBlock(tone: unknown): BlockTone {
  if (tone === 'good' || tone === 'up') return 'good';
  if (tone === 'bad' || tone === 'down') return 'bad';
  if (tone === 'attention') return 'attention';
  return 'neutral';
}

/** The class that colours something by its tone: `bk-good`, `bk-bad`, `bk-attention`, `bk-neutral`. */
export function toneClass(tone: unknown): string {
  return `bk-${toneOfBlock(tone)}`;
}

/** A delta's tone: the one she gave, else read from its sign ("+12%" is good, "-3" bad). */
export function deltaTone(tone: unknown, delta: string): BlockTone {
  if (tone !== undefined && tone !== null) return toneOfBlock(tone);
  const d = delta.trim();
  if (d.startsWith('+') || d.startsWith('▲')) return 'good';
  if (d.startsWith('-') || d.startsWith('−') || d.startsWith('▼')) return 'bad';
  return 'neutral';
}

/** Up to two initials for an avatar: "Sam" S, "Jo Lee" JL, "maple park team" MP. */
export function initials(name: string): string {
  return name
    .trim()
    .split(/\s+/)
    .map((word) => Array.from(word).find((c) => /[\p{L}\p{N}]/u.test(c)) ?? '')
    .filter(Boolean)
    .slice(0, 2)
    .join('')
    .toUpperCase();
}

/** An avatar's tint, 0 to 4: a hash of the name, so a name keeps its colour on every card. */
export function tintOf(name: string): number {
  // FNV-1a over the name as people would write it either way.
  let hash = 0x811c9dc5;
  for (const c of name.trim().toLowerCase()) {
    hash ^= c.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash % 5;
}

/** The block drawn as the hero: the first top-level `value`; -1 for none. */
export function heroIndex(blocks: readonly unknown[]): number {
  return blocks.findIndex((b) => typeof b === 'object' && b !== null && (b as Record<string, unknown>)['type'] === 'value');
}

/** A few people in words: "Sam", "Sam and Jo", "Sam, Jo and Alex", "Sam, Jo and 2 more". */
export function peopleWords(names: readonly string[]): string {
  const [a, b, c] = names;
  if (names.length <= 1) return a ?? '';
  if (names.length === 2) return `${a} and ${b}`;
  if (names.length === 3) return `${a}, ${b} and ${c}`;
  return `${a}, ${b} and ${names.length - 2} more`;
}

/** A meter's share of its max, 0 to 100, whole. */
export function percentOf(value: number, max: number): number {
  if (!(max > 0) || !Number.isFinite(value)) return 0;
  return Math.round((Math.min(max, Math.max(0, value)) / max) * 100);
}

/** Each bar's share of the largest, 0 to 1: the largest fills its track. */
export function barShares(values: readonly number[]): number[] {
  const clean = values.map((v) => (Number.isFinite(v) && v > 0 ? v : 0));
  const top = Math.max(0, ...clean);
  return clean.map((v) => (top > 0 ? v / top : 0));
}

/**
 * What moves on from each funnel stage to the next, as a whole percent: the next count over this
 * one. Null after the last stage, after a stage of 0 (nothing to move on from), and before a
 * larger stage (more than all moving on is not a funnel, so it says nothing).
 */
export function funnelSteps(counts: readonly number[]): (number | null)[] {
  return counts.map((c, i) => {
    const next = counts[i + 1];
    if (next === undefined || !(c > 0) || !Number.isFinite(next) || next > c) return null;
    return Math.round((Math.max(0, next) / c) * 100);
  });
}

/** The current step's index: a step's label (as written, else ignoring case) or its index; -1 for neither. */
export function currentStep(steps: readonly string[], current: unknown): number {
  if (typeof current === 'number') return Number.isInteger(current) && current >= 0 && current < steps.length ? current : -1;
  if (typeof current !== 'string') return -1;
  const exact = steps.indexOf(current);
  if (exact >= 0) return exact;
  const want = current.trim().toLocaleLowerCase();
  return steps.findIndex((s) => s.trim().toLocaleLowerCase() === want);
}

export interface Run {
  readonly text: string;
  readonly bold: boolean;
}

export type NoteBlock =
  | { readonly kind: 'paragraph'; readonly lines: readonly (readonly Run[])[] }
  | { readonly kind: 'list'; readonly items: readonly (readonly Run[])[] };

/** A line's `**bold**` parts; an unmatched pair of stars stays as written. */
export function runs(line: string): Run[] {
  const out: Run[] = [];
  let last = 0;
  for (const m of line.matchAll(/\*\*(.+?)\*\*/g)) {
    if (m.index > last) out.push({ text: line.slice(last, m.index), bold: false });
    out.push({ text: m[1] ?? '', bold: true });
    last = m.index + m[0].length;
  }
  if (last < line.length) out.push({ text: line.slice(last), bold: false });
  return out;
}

/** A note's text as blocks: paragraphs split by blank lines, `- ` lines as bullets, `**bold**`. */
export function parseNote(written: string): NoteBlock[] {
  const blocks: NoteBlock[] = [];
  for (const chunk of written.replace(/\r\n?/g, '\n').split(/\n[ \t]*\n/)) {
    let lines: Run[][] = [];
    let items: Run[][] = [];
    const flushLines = (): void => {
      if (lines.length) blocks.push({ kind: 'paragraph', lines });
      lines = [];
    };
    const flushItems = (): void => {
      if (items.length) blocks.push({ kind: 'list', items });
      items = [];
    };
    for (const raw of chunk.split('\n')) {
      const line = raw.trim();
      if (!line) continue;
      const bullet = /^- +(.*)$/.exec(line);
      if (bullet) {
        flushLines();
        items.push(runs(bullet[1] ?? ''));
      } else {
        flushItems();
        lines.push(runs(line));
      }
    }
    flushLines();
    flushItems();
  }
  return blocks;
}

/** "09:30" as minutes after midnight; null for anything else. */
export function minutesOf(hhmm: unknown): number | null {
  if (typeof hhmm !== 'string') return null;
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm.trim());
  if (!m) return null;
  const h = Number(m[1]);
  const min = Number(m[2]);
  return h < 24 && min < 60 ? h * 60 + min : null;
}

export interface PlacedEvent {
  /** Position in the events she gave. */
  readonly index: number;
  readonly start: number;
  readonly end: number;
  /** Rows, so overlapping events never sit on each other. */
  readonly lane: number;
  /** Percent of the track. */
  readonly left: number;
  readonly width: number;
}

export interface TimelineLayout {
  readonly from: number;
  readonly to: number;
  /** Hours (0-23) to label along the track. */
  readonly hours: readonly number[];
  readonly lanes: number;
  readonly items: readonly PlacedEvent[];
}

/** An event with no end, or one that ends before it starts, is shown half an hour long. */
const POINT_MINUTES = 30;

/** Events on one day along a track from the hour before the first to the hour after the last. */
export function layoutTimeline(events: readonly { readonly start: unknown; readonly end?: unknown }[]): TimelineLayout {
  const timed = events.flatMap((e, index) => {
    const start = minutesOf(e.start);
    if (start === null) return [];
    const end = minutesOf(e.end);
    return [{ index, start, end: end !== null && end > start ? end : Math.min(start + POINT_MINUTES, 1440) }];
  });
  if (timed.length === 0) return { from: 0, to: 0, hours: [], lanes: 0, items: [] };
  const from = Math.floor(Math.min(...timed.map((e) => e.start)) / 60) * 60;
  const to = Math.max(from + 60, Math.ceil(Math.max(...timed.map((e) => e.end)) / 60) * 60);
  const span = to - from;
  const laneEnds: number[] = [];
  const items = [...timed]
    .sort((a, b) => a.start - b.start || a.index - b.index)
    .map((e) => {
      let lane = laneEnds.findIndex((end) => end <= e.start);
      if (lane < 0) lane = laneEnds.push(e.end) - 1;
      else laneEnds[lane] = e.end;
      return { ...e, lane, left: ((e.start - from) / span) * 100, width: ((e.end - e.start) / span) * 100 };
    })
    .sort((a, b) => a.index - b.index);
  const step = Math.max(1, Math.ceil(span / 60 / 6));
  const hours: number[] = [];
  for (let m = from; m < to; m += step * 60) hours.push(m / 60);
  return { from, to, hours, lanes: laneEnds.length, items };
}

/** The index of the x nearest to `x`; -1 when there are none. */
export function nearestIndex(xs: readonly number[], x: number): number {
  let best = -1;
  let gap = Infinity;
  xs.forEach((v, i) => {
    const d = Math.abs(v - x);
    if (d < gap) {
      gap = d;
      best = i;
    }
  });
  return best;
}

const KINDS: readonly ChartKind[] = ['line', 'bar', 'area', 'donut'];
const KIND_WORDS: Readonly<Record<ChartKind, string>> = { line: 'Line chart', bar: 'Bar chart', area: 'Area chart', donut: 'Donut chart' };

export const str = (v: unknown): string => (typeof v === 'string' ? v : typeof v === 'number' && Number.isFinite(v) ? String(v) : '');
export const records = (v: unknown): Record<string, unknown>[] =>
  Array.isArray(v) ? v.filter((x): x is Record<string, unknown> => typeof x === 'object' && x !== null && !Array.isArray(x)) : [];

/** A chart widget's fields as a spec for the chart maths. */
export function chartSpecOf(w: Readonly<Record<string, unknown>>): ChartSpec {
  const kind = KINDS.find((k) => k === w['kind']) ?? 'line';
  const x = Array.isArray(w['x']) ? (w['x'] as unknown[]).map(str) : [];
  const series = records(w['series']).map((s) => ({
    name: str(s['name']),
    values: Array.isArray(s['values']) ? (s['values'] as unknown[]).map((v) => (typeof v === 'number' && Number.isFinite(v) ? v : 0)) : [],
  }));
  const unit = str(w['unit']);
  return unit ? { kind, x, series, unit } : { kind, x, series };
}

/** What a chart shows, in words, for a screen reader. */
export function chartSummary(spec: ChartSpec, title: string): string {
  const head = title ? `${KIND_WORDS[spec.kind]}, ${title}` : KIND_WORDS[spec.kind];
  const values = spec.series.filter((sr) => sr.values.length > 0);
  if (values.length === 0) return `${head}: no data.`;
  const first = spec.x[0];
  const last = spec.x[spec.x.length - 1];
  const across = first && last && spec.x.length > 1 ? `, ${first} to ${last}` : '';
  const each = values.map((sr) => {
    const v = (n: number): string => formatValue(n, spec.unit, 2);
    const end = sr.values[sr.values.length - 1] ?? 0;
    return `${sr.name || 'Values'}: low ${v(Math.min(...sr.values))}, high ${v(Math.max(...sr.values))}, last ${v(end)}`;
  });
  return `${head}${across}. ${each.join('. ')}.`;
}

/** One point of a chart in words, for a screen reader moving through it with the arrow keys. */
export function pointWords(spec: ChartSpec, i: number): string {
  const at = spec.x[i] ? `${spec.x[i]}: ` : '';
  return at + spec.series.slice(0, 5).map((sr) => `${sr.name || 'Value'} ${formatValue(sr.values[i] ?? 0, spec.unit, 2)}`).join(', ');
}

// ── Drawing shared with the board ──────────────────────────────────────

export function h<K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, text?: string): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}

const SVG_NS = 'http://www.w3.org/2000/svg';
export function s(tag: string, attrs: Record<string, string | number> = {}, text?: string): SVGElement {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  if (text !== undefined) el.textContent = text;
  return el;
}

/** The class for chart colour 0-4; anything else (a donut's "Other") is neutral. */
export const colorClass = (c: number): string => (c >= 0 && c < 5 ? `bd-c${c}` : 'bd-cx');

export function swatch(color: number): HTMLElement {
  const sw = h('i', `bd-sw ${colorClass(color)}`);
  sw.setAttribute('aria-hidden', 'true');
  return sw;
}

export function runsInto(parent: HTMLElement, rs: readonly Run[]): void {
  for (const r of rs) parent.append(r.bold ? h('strong', '', r.text) : document.createTextNode(r.text));
}

/** Something to stop when the widget leaves the board. */
export type Dispose = () => void;
export const nothing: Dispose = () => {};

const DONUT = 168;

export function legendOf(entries: ChartLayout['legend'], withValues: boolean): HTMLUListElement {
  const ul = h('ul', withValues ? 'bd-legend bd-legend-values' : 'bd-legend');
  for (const e of entries) {
    const li = h('li');
    li.append(swatch(e.color), h('span', 'bd-legend-name', e.name || '–'));
    if (withValues) {
      li.append(h('span', 'bd-legend-value', e.value ?? ''));
      if (e.percent !== undefined) li.append(h('span', 'bd-legend-pct', `${e.percent}%`));
    }
    ul.append(li);
  }
  return ul;
}

export function cartesian(spec: ChartSpec, title: string, least: number, body: HTMLElement): Dispose {
  const box = h('div', 'bd-plot');
  // It fills what its card has to spare, never less than this; the drawing fills it exactly.
  box.style.minHeight = `${least}px`;
  box.tabIndex = 0;
  box.setAttribute('role', 'img');
  box.setAttribute('aria-label', chartSummary(spec, title));
  const svg = s('svg', { class: 'bd-svg', 'aria-hidden': 'true' });
  const tip = h('div', 'bd-tip');
  tip.hidden = true;
  box.append(svg, tip);
  // What the arrow keys point at, read out (the chart itself is one image to a screen reader).
  const said = h('p', 'visually-hidden');
  said.setAttribute('aria-live', 'polite');
  body.append(box, said);
  if (spec.series.length > 1) body.append(legendOf(layoutChart(spec, 1, 1).legend, false));

  let L: ChartLayout | null = null;
  let xs: number[] = [];
  let at = -1;
  const guide = s('line', { class: 'bd-guide' });
  const dots = s('g', { class: 'bd-dots' });

  function paint(width: number, height: number): void {
    L = layoutChart(spec, width, height);
    // Sized by CSS to the box; the view box is the box measured, so text stays its real size.
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.replaceChildren();
    at = -1;
    tip.hidden = true;
    if (L.empty) {
      svg.append(s('text', { class: 'bd-axis', x: width / 2, y: height / 2, 'text-anchor': 'middle' }, 'No data yet'));
      xs = [];
      return;
    }
    const { plot } = L;
    for (const t of L.yTicks) {
      svg.append(s('line', { class: t.value === 0 ? 'bd-gridline bd-zero' : 'bd-gridline', x1: plot.left, x2: plot.right, y1: t.y, y2: t.y }));
      svg.append(s('text', { class: 'bd-axis', x: plot.left - 8, y: t.y, dy: '0.32em', 'text-anchor': 'end' }, t.label));
    }
    for (const l of L.xLabels) {
      svg.append(s('text', { class: 'bd-axis', x: l.x, y: height - 5, 'text-anchor': l.anchor }, l.label));
    }
    for (const r of L.rects) {
      svg.append(
        s('rect', {
          class: `bd-bar ${colorClass(r.series)}`,
          'data-i': r.index,
          x: r.x,
          y: r.y,
          width: Math.max(0, r.width - 1),
          height: r.height,
          rx: Math.min(3, r.width / 3),
        }),
      );
    }
    for (const sr of L.series) if (sr.area) svg.append(s('path', { class: `bd-area ${colorClass(sr.color)}`, d: sr.area }));
    for (const sr of L.series) {
      svg.append(s('path', { class: `bd-line ${colorClass(sr.color)}`, d: sr.line }));
      // Few points, or one alone, are easier to read as dots.
      if (sr.points.length <= 12) {
        for (const p of sr.points) svg.append(s('circle', { class: `bd-point ${colorClass(sr.color)}`, cx: p.x, cy: p.y, r: 2.5 }));
      }
    }
    guide.setAttribute('y1', String(plot.top));
    guide.setAttribute('y2', String(plot.bottom));
    svg.append(guide, dots);
    if (L.series[0]) xs = L.series[0].points.map((p) => p.x);
    else {
      const count = L.rects.filter((r) => r.series === 0).length;
      const band = (plot.right - plot.left) / Math.max(1, count);
      xs = Array.from({ length: count }, (_, i) => plot.left + band * (i + 0.5));
    }
  }

  function point(i: number, speak = false): void {
    if (!L || L.empty || i < 0 || i >= xs.length) {
      hide();
      return;
    }
    at = i;
    const x = xs[i] ?? 0;
    guide.setAttribute('x1', String(x));
    guide.setAttribute('x2', String(x));
    box.dataset['hover'] = '';
    dots.replaceChildren();
    for (const sr of L.series) {
      const p = sr.points[i];
      if (p) dots.append(s('circle', { class: `bd-dot ${colorClass(sr.color)}`, cx: p.x, cy: p.y, r: 4 }));
    }
    for (const r of svg.querySelectorAll<SVGRectElement>('.bd-bar')) r.toggleAttribute('data-hot', r.dataset['i'] === String(i));
    const head = spec.x[i] ?? '';
    tip.replaceChildren();
    if (head) tip.append(h('p', 'bd-tip-head', head));
    spec.series.slice(0, 5).forEach((sr, k) => {
      const row = h('p', 'bd-tip-row');
      row.append(swatch(k), h('span', 'bd-tip-name', sr.name), h('strong', '', formatValue(sr.values[i] ?? 0, spec.unit, 2)));
      tip.append(row);
    });
    tip.hidden = false;
    if (speak) said.textContent = pointWords(spec, i);
    const tw = tip.offsetWidth;
    // Beside the guide, on whichever side has room.
    const left = x + 12 + tw <= L.width ? x + 12 : Math.max(0, x - 12 - tw);
    tip.style.left = `${left}px`;
    tip.style.top = `${L.plot.top}px`;
  }

  function hide(): void {
    at = -1;
    tip.hidden = true;
    delete box.dataset['hover'];
    dots.replaceChildren();
    for (const r of svg.querySelectorAll('.bd-bar[data-hot]')) r.removeAttribute('data-hot');
  }

  box.addEventListener('pointermove', (ev) => {
    const r = svg.getBoundingClientRect();
    point(nearestIndex(xs, ev.clientX - r.left));
  });
  box.addEventListener('pointerleave', hide);
  box.addEventListener('blur', hide);
  box.addEventListener('keydown', (ev) => {
    const step = ev.key === 'ArrowRight' ? 1 : ev.key === 'ArrowLeft' ? -1 : 0;
    if (ev.key === 'Home' || ev.key === 'End') {
      ev.preventDefault();
      point(ev.key === 'Home' ? 0 : xs.length - 1, true);
    } else if (step) {
      ev.preventDefault();
      point(at < 0 ? (step > 0 ? 0 : xs.length - 1) : Math.min(xs.length - 1, Math.max(0, at + step)), true);
    }
  });

  // Drawn at its real size, so text stays its real size; redrawn when the card resizes.
  let last = '';
  const ro = new ResizeObserver(([entry]) => {
    if (!entry) return;
    const width = Math.round(entry.contentRect.width);
    const height = Math.round(entry.contentRect.height);
    if (width < 2 || height < 2 || `${width}x${height}` === last) return;
    last = `${width}x${height}`;
    paint(width, height);
  });
  ro.observe(box);
  return () => ro.disconnect();
}

export function donut(spec: ChartSpec, title: string, body: HTMLElement): Dispose {
  const L = layoutChart(spec, DONUT, DONUT);
  const wrap = h('div', 'bd-donut');
  const fig = h('div', 'bd-donut-fig');
  fig.setAttribute('role', 'img');
  const words = L.legend.map((e) => `${e.name} ${e.value ?? ''} (${e.percent ?? 0}%)`).join(', ');
  fig.setAttribute('aria-label', `${chartSummary(spec, title).replace(/\.$/, '')}${words ? `: ${words}` : ''}. Total ${L.donut?.total ?? ''}.`);
  // Drawn once at its least size; the view box scales it to its cell, up to a cap (CSS).
  const svg = s('svg', { class: 'bd-svg', viewBox: `0 0 ${DONUT} ${DONUT}`, 'aria-hidden': 'true' });
  fig.append(svg);
  const d = L.donut;
  if (L.empty || !d) {
    if (d) svg.append(s('path', { class: 'bd-ring-empty', d: d.ring, 'fill-rule': 'evenodd' }));
  }
  const slices = L.arcs.map((a) => {
    const p = s('path', { class: `bd-slice ${colorClass(a.color)}`, d: a.d, 'fill-rule': 'evenodd' });
    svg.append(p);
    return p;
  });
  const big = s('text', { class: 'bd-donut-total', x: DONUT / 2, y: DONUT / 2, 'text-anchor': 'middle' }, d?.total ?? '');
  const small = s('text', { class: 'bd-donut-label', x: DONUT / 2, y: DONUT / 2 + 18, 'text-anchor': 'middle' }, 'Total');
  svg.append(big, small);
  const legend = legendOf(L.legend, true);
  wrap.append(fig, legend);
  body.append(wrap);

  // Pointing at a slice or its legend row lights both, and the centre names it.
  const entries = [...legend.children] as HTMLElement[];
  const light = (k: number): void => {
    slices.forEach((p, i) => p.toggleAttribute('data-dim', k >= 0 && i !== k));
    entries.forEach((r, i) => r.toggleAttribute('data-hot', i === k));
    const e = L.legend[k];
    big.textContent = e ? (e.value ?? '') : (d?.total ?? '');
    small.textContent = e ? `${e.name} · ${e.percent ?? 0}%` : 'Total';
  };
  slices.forEach((p, i) => {
    p.addEventListener('pointerenter', () => light(i));
    p.addEventListener('pointerleave', () => light(-1));
  });
  entries.forEach((r, i) => {
    r.addEventListener('pointerenter', () => light(i));
    r.addEventListener('pointerleave', () => light(-1));
  });
  return nothing;
}

/** A table: numbers right-aligned in their column; `highlight` (a row index) tinted. */
export function drawTable(columnsIn: unknown, rowsIn: unknown, highlight?: unknown): HTMLElement {
  const scroll = h('div', 'bd-table-wrap');
  scroll.tabIndex = 0;
  const t = h('table', 'bd-table');
  const heads = Array.isArray(columnsIn) ? (columnsIn as unknown[]).map(str) : [];
  const lines = Array.isArray(rowsIn) ? (rowsIn as unknown[]).filter(Array.isArray) : [];
  // A column of numbers lines up on the right.
  const numeric = heads.map((_, c) => lines.length > 0 && lines.every((r) => typeof (r as unknown[])[c] === 'number'));
  const head = h('tr');
  heads.forEach((c, i) => {
    const th = h('th', numeric[i] ? 'bd-num' : '', c);
    th.scope = 'col';
    head.append(th);
  });
  const thead = h('thead');
  thead.append(head);
  const tbody = h('tbody');
  lines.forEach((r, k) => {
    const tr = h('tr');
    const picked = k === highlight;
    if (picked) tr.className = 'bk-hl';
    heads.forEach((_, i) => {
      const v = (r as unknown[])[i];
      const td = h('td', numeric[i] ? 'bd-num' : '', typeof v === 'number' ? v.toLocaleString() : str(v));
      // The tint says it to the eye; this says it to a screen reader.
      if (picked && i === 0) td.append(hidden(' (her pick)'));
      tr.append(td);
    });
    tbody.append(tr);
  });
  t.append(thead, tbody);
  scroll.append(t);
  return scroll;
}

/** A note's text: paragraphs, `- ` bullets and `**bold**`, and nothing else. */
export function drawNote(written: string): HTMLElement {
  const box = h('div', 'bd-note');
  for (const b of parseNote(written)) {
    if (b.kind === 'paragraph') {
      const p = h('p');
      b.lines.forEach((line, i) => {
        if (i > 0) p.append(h('br'));
        runsInto(p, line);
      });
      box.append(p);
    } else {
      const ul = h('ul');
      for (const item of b.items) {
        const li = h('li');
        runsInto(li, item);
        ul.append(li);
      }
      box.append(ul);
    }
  }
  return box;
}

/** A day's events: a track when the card is wide enough to read one, and the same as rows beneath. */
export function drawTimeline(eventsIn: unknown, body: HTMLElement): void {
  const events = records(eventsIn);
  const T = layoutTimeline(events.map((e) => ({ start: e['start'], end: e['end'] })));
  const when = (e: Record<string, unknown>): string => {
    const end = str(e['end']);
    return end ? `${str(e['start'])}–${end}` : str(e['start']);
  };

  const track = h('div', 'bd-track');
  track.setAttribute('aria-hidden', 'true');
  track.style.setProperty('--lanes', String(Math.max(1, T.lanes)));
  const span = T.to - T.from;
  for (const hr of T.hours) {
    const tick = h('span', 'bd-hour', hourLabel(hr));
    tick.style.left = `${((hr * 60 - T.from) / span) * 100}%`;
    track.append(tick);
  }
  for (const it of T.items) {
    const e = events[it.index];
    if (!e) continue;
    const b = h('div', `bd-event bd-c${it.index % 5}`);
    // Never past the track's right edge, even at its smallest width.
    b.style.width = `max(${it.width}%, 6px)`;
    b.style.left = `min(${it.left}%, calc(100% - max(${it.width}%, 6px)))`;
    b.style.setProperty('--lane', String(it.lane));
    b.append(h('strong', '', str(e['title'])));
    b.title = `${str(e['title'])} · ${when(e)}`;
    track.append(b);
  }
  if (T.items.length) body.append(track);

  const ol = h('ol', 'bd-agenda');
  events.forEach((e, i) => {
    const li = h('li');
    li.append(swatch(i % 5), h('span', 'bd-when', when(e)));
    const words = h('div', 'bd-item-text');
    words.append(h('strong', '', str(e['title'])));
    const meta = str(e['meta']);
    if (meta) words.append(h('span', 'bd-sub', meta));
    li.append(words);
    ol.append(li);
  });
  body.append(ol);
}

// ── Blocks ──────────────────────────────────────────────────────────────

type Rec = Readonly<Record<string, unknown>>;

export interface DrawOptions {
  /** The clock the times are drawn at; `tick` moves it on. */
  readonly now: Date;
  /** The least height of a line, bar or area chart, in CSS px. */
  readonly chartHeight?: number;
  /** The card's title, for what a chart says to a screen reader. */
  readonly title?: string;
}

export interface DrawnBlocks {
  readonly el: HTMLElement;
  /** Bring every time on the card up to `now` ("in 25 min" becomes "in 24 min"). */
  tick(now: Date): void;
  /** The card left the board: stop watching sizes. */
  dispose(): void;
}

interface Ctx {
  readonly chartHeight: number;
  readonly title: string;
  readonly disposers: Dispose[];
}

const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
const strings = (v: unknown): string[] => (Array.isArray(v) ? (v as unknown[]).map(str).filter(Boolean) : []);
/** A tone's class, or with none the first series colour: data that is neither good nor bad. */
const seriesOr = (tone: unknown): string => (tone === undefined || tone === null ? 'bk-series' : toneClass(tone));
const hidden = (text: string): HTMLElement => h('span', 'visually-hidden', text);

/** What a tone means, for someone who cannot see its colour. */
const TONE_WORDS: Readonly<Record<BlockTone, string>> = { good: 'Good', bad: 'Problem', attention: 'Needs attention', neutral: '' };

/** The words a time element shows at `now`, from what it carries. */
function timeWords(el: HTMLElement, now: Date): string {
  const at = el.dataset['at'] ?? '';
  switch (el.dataset['when']) {
    case 'count':
      return countdown(at, el.dataset['end'], now);
    default:
      return relative(at, now);
  }
}

/** A time that keeps itself true: `data-at` (and `data-end`) for `tick`, `datetime` for machines. */
function timeEl(cls: string, at: string, now: Date, kind: 'rel' | 'count' = 'rel', end?: string): HTMLElement {
  const t = h('time', cls);
  t.dateTime = at;
  t.dataset['at'] = at;
  t.dataset['when'] = kind;
  if (end) t.dataset['end'] = end;
  t.textContent = timeWords(t, now);
  return t;
}

function avatar(name: string, size: 'sm' | 'md' = 'sm'): HTMLElement {
  const a = h('span', `bk-avatar bk-avatar-${size} bk-tint-${tintOf(name)}`, initials(name));
  a.setAttribute('aria-hidden', 'true');
  return a;
}

function dot(tone: BlockTone): HTMLElement {
  const d = h('span', `bk-dot ${toneClass(tone)}`);
  d.setAttribute('aria-hidden', 'true');
  return d;
}

// Value ──

function value(b: Rec, now: Date, ctx: Ctx, hero: boolean): HTMLElement {
  const box = h('div', hero ? 'bk-value bk-hero' : 'bk-value');
  const label = str(b['label']);
  if (label) box.append(h('p', 'bk-label', label));
  const n = h('p', 'bk-num', str(b['value']));
  const delta = str(b['delta']);
  // With no delta to carry it, a tone colours the number itself.
  if (!delta && b['tone'] !== undefined) n.classList.add(toneClass(b['tone']));
  box.append(n);
  const hint = str(b['hint']);
  if (delta || hint) {
    const foot = h('p', 'bk-value-foot');
    if (delta) foot.append(h('span', `bk-delta ${toneClass(deltaTone(b['tone'], delta))}`, delta));
    if (hint) foot.append(h('span', 'bk-meta', hint));
    box.append(foot);
  }
  return box;
}

// Sparkline ──

const SPARK_W = 120;
const SPARK_H = 32;

function sparkline(b: Rec): HTMLElement {
  const values = Array.isArray(b['values']) ? (b['values'] as unknown[]).map(num) : [];
  const box = h('div', `bk-spark ${seriesOr(b['tone'])}`);
  box.setAttribute('role', 'img');
  if (values.length > 0) {
    const f = (v: number): string => formatValue(v, undefined, 2);
    box.setAttribute('aria-label', `Trend from ${f(values[0] ?? 0)} to ${f(values[values.length - 1] ?? 0)}, low ${f(Math.min(...values))}, high ${f(Math.max(...values))}`);
  }
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const pad = 3;
  const points = values.map((v, i) => ({
    x: values.length > 1 ? (i / (values.length - 1)) * SPARK_W : SPARK_W / 2,
    y: pad + (1 - (hi > lo ? (v - lo) / (hi - lo) : 0.5)) * (SPARK_H - pad * 2),
  }));
  const svg = s('svg', { class: 'bk-spark-svg', viewBox: `0 0 ${SPARK_W} ${SPARK_H}`, preserveAspectRatio: 'none', 'aria-hidden': 'true' });
  if (points.length > 1) {
    svg.append(s('path', { class: 'bk-spark-area', d: areaPath(points, SPARK_H) }));
    svg.append(s('path', { class: 'bk-spark-line', d: linePath(points), 'vector-effect': 'non-scaling-stroke' }));
  }
  box.append(svg);
  return box;
}

// Meter ──

const RING = 15.9155; // a circle of circumference 100, so the dash is the percent

function meter(b: Rec, _now: Date, ctx: Ctx): HTMLElement {
  const max = num(b['max']);
  const pct = percentOf(num(b['value']), max);
  const label = str(b['label']);
  const ring = b['shape'] === 'ring';
  const box = h('div', `bk-meter ${ring ? 'bk-meter-ring' : 'bk-meter-bar'} ${seriesOr(b['tone'])}`);
  box.setAttribute('role', 'meter');
  box.setAttribute('aria-valuemin', '0');
  box.setAttribute('aria-valuemax', String(max));
  box.setAttribute('aria-valuenow', String(Math.min(max, Math.max(0, num(b['value'])))));
  box.setAttribute('aria-valuetext', `${pct}%`);
  // With no label of its own, a meter is named by its card (a goal titled by its label).
  box.setAttribute('aria-label', label || ctx.title || 'Progress');
  if (ring) {
    const fig = h('div', 'bk-ring');
    const svg = s('svg', { class: 'bk-ring-svg', viewBox: '0 0 36 36', 'aria-hidden': 'true' });
    svg.append(s('circle', { class: 'bk-ring-track', cx: 18, cy: 18, r: RING }));
    // At 0% a round cap would still draw a dot: no fill at all.
    if (pct > 0) svg.append(s('circle', { class: 'bk-ring-fill', cx: 18, cy: 18, r: RING, 'stroke-dasharray': `${pct} 100`, transform: 'rotate(-90 18 18)' }));
    fig.append(svg, h('span', 'bk-ring-num', `${pct}%`));
    box.append(fig);
    if (label) box.append(h('p', 'bk-meter-label', label));
  } else {
    const head = h('div', 'bk-meter-head');
    head.append(h('span', 'bk-meter-label', label), h('span', 'bk-meter-num', `${pct}%`));
    const track = h('div', 'bk-meter-track');
    const fill = h('i', 'bk-meter-fill');
    fill.style.setProperty('--v', String(pct / 100));
    track.append(fill);
    box.append(head, track);
  }
  return box;
}

// Time ──

function time(b: Rec, now: Date): HTMLElement {
  const at = str(b['at']);
  const end = str(b['end']) || undefined;
  const box = h('div', 'bk-time');
  const label = str(b['label']);
  if (label) box.append(h('p', 'bk-time-label', label));
  box.append(timeEl('bk-time-when', at, now, 'count', end));
  // The clock as well, so the card still says when once the countdown is a day away.
  const span = range(at, end);
  if (span) box.append(h('p', 'bk-meta bk-time-range', span));
  return box;
}

// Chart, table, timeline ──

function chartBlock(b: Rec, now: Date, ctx: Ctx): HTMLElement {
  const box = h('div', 'bk-chart');
  const spec = chartSpecOf(b);
  ctx.disposers.push(spec.kind === 'donut' ? donut(spec, ctx.title, box) : cartesian(spec, ctx.title, ctx.chartHeight, box));
  return box;
}

function tableBlock(b: Rec): HTMLElement {
  return drawTable(b['columns'], b['rows'], b['highlight']);
}

function timelineBlock(b: Rec): HTMLElement {
  const box = h('div', 'bk-timeline');
  drawTimeline(b['events'], box);
  return box;
}

// Rows ──

const wordsOf = (text: string): string => ` ${text.toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim()} `;

/** Whether a row's title already names the person ("Sam", "Sam Lee" for Sam), so the row need not again. */
export function saysName(title: string, name: string): boolean {
  return name.trim() !== '' && wordsOf(title).includes(wordsOf(name));
}

function rows(b: Rec, now: Date): HTMLElement {
  const box = h('div', 'bk-rows-block');
  const label = str(b['label']);
  if (label) box.append(h('p', 'bk-label', label));
  const ul = h('ul', 'bk-rows');
  for (const it of records(b['items'])) {
    const li = h('li', 'bk-row');
    const done = it['done'];
    if (done === true) li.dataset['done'] = '';
    // On the left: who, else how it stands, else whether it's done.
    const who = str(it['person']);
    const standing = it['status'];
    if (who) li.append(avatar(who));
    else if (standing !== undefined) li.append(dot(toneOfBlock(standing)));
    else if (typeof done === 'boolean') {
      const mark = h('span', 'bk-check');
      mark.setAttribute('aria-hidden', 'true');
      if (done) mark.append(icon('check', 11));
      li.append(mark);
    }
    const words = h('div', 'bk-row-text');
    const title = h('strong', 'bk-row-title', str(it['title']));
    if (done === true) title.prepend(hidden('Done: '));
    const word = standing !== undefined ? TONE_WORDS[toneOfBlock(standing)] : '';
    if (word) title.prepend(hidden(`${word}: `));
    words.append(title);
    // Who, once: beside the detail only when the title does not already say it.
    const detail = [saysName(str(it['title']), who) ? '' : who, str(it['detail'])].filter(Boolean).join(' · ');
    if (detail) words.append(h('span', 'bk-row-detail', detail));
    li.append(words);
    // On the right, one of: an amount (with its time under it), when, meta, a chip.
    const amount = str(it['amount']);
    const when = str(it['when']);
    const meta = str(it['meta']);
    const chip = str(it['chip']);
    if (amount) {
      const end = h('div', 'bk-row-end');
      const tone = toneOfBlock(it['amount_tone']);
      const sum = h('span', `bk-row-amount ${toneClass(tone)}`, amount);
      // The colour, in words, as a status dot says it.
      if (TONE_WORDS[tone]) sum.prepend(hidden(`${TONE_WORDS[tone]}: `));
      end.append(sum);
      if (when) end.append(timeEl('bk-row-trail', when, now));
      li.append(end);
    } else if (when) li.append(timeEl('bk-row-trail', when, now));
    else if (meta) li.append(h('span', 'bk-row-trail', meta));
    else if (chip) li.append(h('span', 'bk-chip bk-row-chip', chip));
    ul.append(li);
  }
  box.append(ul);
  return box;
}

// People ──

function person(b: Rec): HTMLElement {
  const name = str(b['name']);
  const box = h('div', 'bk-person');
  const words = h('div', 'bk-person-text');
  words.append(h('strong', 'bk-person-name', name));
  const detail = str(b['detail']);
  if (detail) words.append(h('span', 'bk-person-detail', detail));
  box.append(avatar(name, 'md'), words);
  return box;
}

const STACK = 4;

function people(b: Rec): HTMLElement {
  const names = strings(b['names']);
  const box = h('div', 'bk-people');
  const stack = h('div', 'bk-stack');
  stack.setAttribute('aria-hidden', 'true');
  for (const n of names.slice(0, STACK)) stack.append(avatar(n));
  box.append(stack, h('p', 'bk-people-names', peopleWords(names)));
  return box;
}

// Status, chips ──

function status(b: Rec): HTMLElement {
  const tone = toneOfBlock(b['tone']);
  const p = h('p', `bk-status ${toneClass(tone)}`);
  const words = h('span', 'bk-status-text', str(b['text']));
  // The dot's colour, in words, as rows say it.
  if (TONE_WORDS[tone]) words.prepend(hidden(`${TONE_WORDS[tone]}: `));
  p.append(dot(tone), words);
  return p;
}

function chips(b: Rec): HTMLElement {
  const ul = h('ul', `bk-chips ${toneClass(b['tone'])}`);
  for (const item of strings(b['items'])) {
    const li = h('li', 'bk-chip', item);
    ul.append(li);
  }
  return ul;
}

// Text, quote, facts ──

function text(b: Rec): HTMLElement {
  const note = drawNote(str(b['text']));
  note.classList.add('bk-text');
  // A tone makes it a callout: a tinted panel with an edge in the tone's colour.
  if (b['tone'] !== undefined) note.classList.add('bk-callout', toneClass(b['tone']));
  return note;
}

function quote(b: Rec, now: Date): HTMLElement {
  const fig = h('figure', 'bk-quote');
  const bq = h('blockquote', 'bk-quote-text');
  bq.append(h('p', '', str(b['text'])));
  fig.append(bq);
  const by = str(b['by']);
  const when = str(b['when']);
  if (by || when) {
    const cap = h('figcaption', 'bk-meta');
    if (by) cap.append(h('span', 'bk-quote-by', by));
    if (by && when) cap.append(document.createTextNode(' · '));
    if (when) cap.append(timeEl('', when, now));
    fig.append(cap);
  }
  return fig;
}

function facts(b: Rec): HTMLElement {
  const dl = h('dl', 'bk-facts');
  for (const f of records(b['items'])) {
    const row = h('div', 'bk-fact');
    row.append(h('dt', 'bk-label', str(f['label'])), h('dd', 'bk-fact-value', str(f['value'])));
    dl.append(row);
  }
  return dl;
}

// Bars ──

function bars(b: Rec): HTMLElement {
  const items = records(b['items']);
  const unit = str(b['unit']) || undefined;
  const shares = barShares(items.map((it) => num(it['value'])));
  const ul = h('ul', `bk-bars ${seriesOr(b['tone'])}`);
  items.forEach((it, i) => {
    const li = h('li', 'bk-bar');
    const head = h('div', 'bk-bar-head');
    head.append(h('span', 'bk-bar-label', str(it['label'])), h('span', 'bk-bar-num', formatValue(num(it['value']), unit, 2)));
    const track = h('div', 'bk-bar-track');
    track.setAttribute('aria-hidden', 'true');
    const fill = h('i', 'bk-bar-fill');
    fill.style.setProperty('--v', String(shares[i] ?? 0));
    track.append(fill);
    li.append(head, track);
    const note = str(it['note']);
    if (note) li.append(h('span', 'bk-bar-note', note));
    ul.append(li);
  });
  return ul;
}

// Funnel ──

function funnel(b: Rec): HTMLElement {
  const stages = records(b['stages']);
  const counts = stages.map((st) => Math.max(0, num(st['count'])));
  const first = counts[0] ?? 0;
  const steps = funnelSteps(counts);
  const ol = h('ol', 'bk-funnel bk-series');
  stages.forEach((st, i) => {
    const li = h('li', 'bk-funnel-stage');
    const band = h('div', 'bk-funnel-band');
    // Narrowing from the first stage, centred. The words sit across the whole band, so they fit at any width.
    const fill = h('i', 'bk-funnel-fill');
    fill.setAttribute('aria-hidden', 'true');
    const count = counts[i] ?? 0;
    fill.style.setProperty('--v', String(first > 0 ? Math.min(1, count / first) : 0));
    if (count === 0) fill.dataset['none'] = '';
    const figures = h('span', 'bk-funnel-figures');
    figures.append(h('strong', 'bk-funnel-count', count.toLocaleString()));
    const worth = str(st['value']);
    if (worth) figures.append(h('span', 'bk-funnel-value', worth));
    band.append(fill, h('span', 'bk-funnel-label', str(st['label'])), figures);
    li.append(band);
    const pct = steps[i];
    if (pct !== null && pct !== undefined) li.append(h('p', 'bk-funnel-step', `${pct}% move on`));
    ol.append(li);
  });
  return ol;
}

// Stages ──

function stagesBlock(b: Rec): HTMLElement {
  const steps = strings(b['steps']);
  const at = currentStep(steps, b['current']);
  const box = h('div', `bk-stages ${seriesOr(b['tone'])}`);
  box.style.setProperty('--steps', String(Math.max(1, steps.length)));
  // Many steps in a narrow card: only the current one is named, under the track (CSS decides when).
  if (steps.length > 4) box.dataset['many'] = steps.length > 5 ? 'lots' : '';
  const ol = h('ol', 'bk-stages-track');
  ol.setAttribute('aria-hidden', 'true');
  steps.forEach((label, i) => {
    const state = at < 0 || i > at ? 'open' : i < at ? 'done' : 'current';
    const li = h('li', 'bk-step');
    li.dataset['state'] = state;
    const mark = h('span', 'bk-step-dot');
    if (state === 'done') mark.append(icon('check', 9));
    li.append(mark, h('span', 'bk-step-label', label));
    ol.append(li);
  });
  box.append(ol);
  const current = steps[at];
  if (current !== undefined) {
    const named = h('p', 'bk-stages-now');
    named.setAttribute('aria-hidden', 'true');
    named.append(h('strong', '', current), document.createTextNode(` · ${at + 1} of ${steps.length}`));
    box.append(named, hidden(`Step ${at + 1} of ${steps.length}: ${current}`));
  } else if (steps.length) {
    box.append(hidden(`Steps: ${steps.join(', ')}`));
  }
  return box;
}

// Columns ──

function columns(b: Rec, now: Date, ctx: Ctx): HTMLElement {
  const box = h('div', 'bk-columns');
  const cols = Array.isArray(b['columns']) ? (b['columns'] as unknown[]).filter(Array.isArray) : [];
  box.style.setProperty('--cols', String(Math.max(1, cols.length)));
  for (const col of cols) {
    const c = h('div', 'bk-col');
    // Inside a column nothing is the hero, and columns never nest.
    drawInto(c, col as unknown[], now, ctx, -1, true);
    box.append(c);
  }
  return box;
}

type Draw = (b: Rec, now: Date, ctx: Ctx, hero: boolean) => HTMLElement;

const DRAW: Readonly<Record<string, Draw>> = {
  value,
  sparkline,
  meter,
  time,
  chart: chartBlock,
  table: tableBlock,
  timeline: timelineBlock,
  rows,
  bars,
  funnel,
  stages: stagesBlock,
  person,
  people,
  status,
  chips,
  text,
  quote,
  facts,
  columns,
};

/** The block types this page draws. */
export const BLOCK_TYPES: readonly string[] = Object.keys(DRAW);

function drawInto(parent: HTMLElement, blocks: readonly unknown[], now: Date, ctx: Ctx, hero: number, inside: boolean): void {
  blocks.forEach((raw, i) => {
    if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return;
    const b = raw as Rec;
    const kind = b['type'];
    const draw = typeof kind === 'string' && Object.hasOwn(DRAW, kind) ? DRAW[kind] : undefined;
    // A block from a newer server, or columns inside columns: skipped, and the card still draws.
    if (!draw || (inside && kind === 'columns')) return;
    try {
      const el = draw(b, now, ctx, i === hero);
      el.dataset['block'] = kind as string;
      parent.append(el);
    } catch (err) {
      // One block that cannot be drawn must not take the rest of the card with it.
      console.warn(`A ${String(kind)} block could not be drawn`, err);
    }
  });
}

/** A card's blocks, drawn at `now`: the order as given, the first top-level value as the hero. */
export function drawBlocks(blocks: readonly unknown[], opts: DrawOptions): DrawnBlocks {
  const el = h('div', 'bk');
  const ctx: Ctx = { chartHeight: opts.chartHeight ?? 160, title: opts.title ?? '', disposers: [] };
  const list = Array.isArray(blocks) ? blocks : [];
  drawInto(el, list, opts.now, ctx, heroIndex(list), false);
  return {
    el,
    tick(now) {
      for (const t of el.querySelectorAll<HTMLElement>('[data-at]')) {
        const words = timeWords(t, now);
        if (t.textContent !== words) t.textContent = words;
      }
    },
    dispose() {
      for (const d of ctx.disposers.splice(0)) d();
    },
  };
}
