/**
 * Her board, drawn in the workspace: one glass card per widget from the kit, laid out in a grid
 * that follows the workspace's width. Everything she put there may quote a page she read
 * (SPN-5), so every piece of text is set with textContent and nothing is ever parsed as HTML.
 */
import { type ChartKind, type ChartSpec, type ChartLayout, formatValue, layoutChart } from './chart';
import { icon } from './icons';
import { hourLabel } from './showcase';
import { type Board, type Widget, type WidgetSize } from './view';

// ── Pure helpers ────────────────────────────────────────────────────────

/** How many columns a widget takes on a wide workspace (of 6) and a middling one (of 2). */
export function spans(size: WidgetSize): { readonly wide: number; readonly mid: number } {
  switch (size) {
    case 'small':
      return { wide: 2, mid: 1 };
    case 'wide':
      return { wide: 4, mid: 2 };
    case 'full':
      return { wide: 6, mid: 2 };
    default:
      return { wide: 3, mid: 1 };
  }
}

/** The least height a chart is drawn at in a card of each size, in CSS px; it grows with its card. */
export function chartHeight(size: WidgetSize): number {
  return size === 'small' ? 150 : size === 'medium' ? 200 : 230;
}

export type RowFit = 'grow' | 'fit';

export interface RowPlan {
  /** Columns each widget takes, in order. Every row adds up to the full width. */
  readonly spans: readonly number[];
  /** Per row: one with a chart shares the spare height; the rest keep their natural height. */
  readonly rows: readonly RowFit[];
}

/**
 * Widgets laid out in rows of `columns`, in her order. A widget that does not fit what is left
 * of a row starts the next one, and the last widget of a row widens to fill it, so a row never
 * has a hole and a widget alone takes the whole width.
 */
export function planRows(widgets: readonly { readonly size: WidgetSize; readonly type: string }[], columns: number): RowPlan {
  const cols = Math.max(1, Math.floor(columns));
  const out: number[] = [];
  const rows: RowFit[] = [];
  let used = 0;
  let grow = false;
  const end = (): void => {
    if (used === 0) return;
    out[out.length - 1]! += cols - used;
    rows.push(grow ? 'grow' : 'fit');
    used = 0;
    grow = false;
  };
  for (const w of widgets) {
    const sp = spans(w.size);
    const want = Math.min(cols, cols >= 6 ? sp.wide : cols >= 2 ? sp.mid : 1);
    if (used + want > cols) end();
    out.push(want);
    used += want;
    grow ||= w.type === 'chart';
    if (used === cols) end();
  }
  end();
  return { spans: out, rows };
}

/** A plan's rows as `grid-template-rows`. */
export function rowTemplate(rows: readonly RowFit[]): string {
  return rows.map((r) => (r === 'grow' ? 'minmax(min-content, 1fr)' : 'auto')).join(' ') || 'auto';
}

/** What the Workspace button does: the empty state for no widgets, and tells her only when it reopens one. */
export function buttonAction(board: Board | null): 'empty' | 'reopen' | 'show' {
  if (!board || board.widgets.length === 0) return 'empty';
  return board.open ? 'show' : 'reopen';
}

/** The workspace's title for a board: hers, or a plain word when she gave none. */
export function boardTitle(board: Board | null): string {
  return board?.title.trim() || 'Board';
}

export type Tone = 'up' | 'down' | 'neutral';

/** A stat's tone: the one she gave, else read from the sign of its delta. */
export function toneOf(tone: unknown, delta: string): Tone {
  if (tone === 'up' || tone === 'down' || tone === 'neutral') return tone;
  const d = delta.trim();
  if (d.startsWith('+') || d.startsWith('▲')) return 'up';
  if (d.startsWith('-') || d.startsWith('−') || d.startsWith('▼')) return 'down';
  return 'neutral';
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
export function parseNote(text: string): NoteBlock[] {
  const blocks: NoteBlock[] = [];
  for (const chunk of text.replace(/\r\n?/g, '\n').split(/\n[ \t]*\n/)) {
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

const str = (v: unknown): string => (typeof v === 'string' ? v : typeof v === 'number' && Number.isFinite(v) ? String(v) : '');
const records = (v: unknown): Record<string, unknown>[] =>
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
  const range = first && last && spec.x.length > 1 ? `, ${first} to ${last}` : '';
  const each = values.map((sr) => {
    const v = (n: number): string => formatValue(n, spec.unit, 2);
    const end = sr.values[sr.values.length - 1] ?? 0;
    return `${sr.name || 'Values'}: low ${v(Math.min(...sr.values))}, high ${v(Math.max(...sr.values))}, last ${v(end)}`;
  });
  return `${head}${range}. ${each.join('. ')}.`;
}

/** One point of a chart in words, for a screen reader moving through it with the arrow keys. */
export function pointWords(spec: ChartSpec, i: number): string {
  const at = spec.x[i] ? `${spec.x[i]}: ` : '';
  return at + spec.series.slice(0, 5).map((sr) => `${sr.name || 'Value'} ${formatValue(sr.values[i] ?? 0, spec.unit, 2)}`).join(', ');
}

// ── DOM ─────────────────────────────────────────────────────────────────

function h<K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, text?: string): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}

const SVG_NS = 'http://www.w3.org/2000/svg';
function s(tag: string, attrs: Record<string, string | number> = {}, text?: string): SVGElement {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  if (text !== undefined) el.textContent = text;
  return el;
}

/** The class for chart colour 0-4; anything else (a donut's "Other") is neutral. */
const colorClass = (c: number): string => (c >= 0 && c < 5 ? `bd-c${c}` : 'bd-cx');

function swatch(color: number): HTMLElement {
  const sw = h('i', `bd-sw ${colorClass(color)}`);
  sw.setAttribute('aria-hidden', 'true');
  return sw;
}

function runsInto(parent: HTMLElement, rs: readonly Run[]): void {
  for (const r of rs) parent.append(r.bold ? h('strong', '', r.text) : document.createTextNode(r.text));
}

/** Something to stop when the widget leaves the board. */
type Dispose = () => void;
const nothing: Dispose = () => {};

// Stat ──

function stat(w: Widget, body: HTMLElement): Dispose {
  const value = h('p', 'bd-stat-value', str(w['value']));
  body.append(value);
  const delta = str(w['delta']);
  const hint = str(w['hint']);
  if (delta || hint) {
    const foot = h('p', 'bd-stat-foot');
    if (delta) {
      const chip = h('span', 'bd-delta', delta);
      chip.dataset['tone'] = toneOf(w['tone'], delta);
      foot.append(chip);
    }
    if (hint) foot.append(h('span', 'bd-hint', hint));
    body.append(foot);
  }
  return nothing;
}

// Chart ──

const DONUT = 168;

function chart(w: Widget, body: HTMLElement): Dispose {
  const spec = chartSpecOf(w);
  const title = str(w['title']);
  return spec.kind === 'donut' ? donut(spec, title, body) : cartesian(spec, title, chartHeight(w.size), body);
}

function legendOf(entries: ChartLayout['legend'], withValues: boolean): HTMLUListElement {
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

function cartesian(spec: ChartSpec, title: string, least: number, body: HTMLElement): Dispose {
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

function donut(spec: ChartSpec, title: string, body: HTMLElement): Dispose {
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
  const rows = [...legend.children] as HTMLElement[];
  const light = (k: number): void => {
    slices.forEach((p, i) => p.toggleAttribute('data-dim', k >= 0 && i !== k));
    rows.forEach((r, i) => r.toggleAttribute('data-hot', i === k));
    const e = L.legend[k];
    big.textContent = e ? (e.value ?? '') : (d?.total ?? '');
    small.textContent = e ? `${e.name} · ${e.percent ?? 0}%` : 'Total';
  };
  slices.forEach((p, i) => {
    p.addEventListener('pointerenter', () => light(i));
    p.addEventListener('pointerleave', () => light(-1));
  });
  rows.forEach((r, i) => {
    r.addEventListener('pointerenter', () => light(i));
    r.addEventListener('pointerleave', () => light(-1));
  });
  return nothing;
}

// List ──

function list(w: Widget, body: HTMLElement): Dispose {
  const ul = h('ul', 'bd-list');
  for (const it of records(w['items'])) {
    const li = h('li', 'bd-item');
    const done = it['done'] === true;
    if (done) li.dataset['done'] = '';
    const mark = h('span', 'bd-mark');
    mark.setAttribute('aria-hidden', 'true');
    if (done) mark.append(icon('check', 12));
    const text = h('div', 'bd-item-text');
    const t = h('strong', '', str(it['title']));
    text.append(t);
    if (done) t.prepend(h('span', 'visually-hidden', 'Done: '));
    const sub = str(it['subtitle']);
    if (sub) text.append(h('span', 'bd-sub', sub));
    li.append(mark, text);
    const meta = str(it['meta']);
    if (meta) li.append(h('span', 'bd-meta', meta));
    ul.append(li);
  }
  body.append(ul);
  return nothing;
}

// Table ──

function table(w: Widget, body: HTMLElement): Dispose {
  const scroll = h('div', 'bd-table-wrap');
  scroll.tabIndex = 0;
  const t = h('table', 'bd-table');
  const columns = Array.isArray(w['columns']) ? (w['columns'] as unknown[]).map(str) : [];
  const rows = Array.isArray(w['rows']) ? (w['rows'] as unknown[]).filter(Array.isArray) : [];
  // A column of numbers lines up on the right.
  const numeric = columns.map((_, c) => rows.length > 0 && rows.every((r) => typeof (r as unknown[])[c] === 'number'));
  const head = h('tr');
  columns.forEach((c, i) => {
    const th = h('th', numeric[i] ? 'bd-num' : '', c);
    th.scope = 'col';
    head.append(th);
  });
  const thead = h('thead');
  thead.append(head);
  const tbody = h('tbody');
  for (const r of rows) {
    const tr = h('tr');
    columns.forEach((_, i) => {
      const v = (r as unknown[])[i];
      tr.append(h('td', numeric[i] ? 'bd-num' : '', typeof v === 'number' ? v.toLocaleString() : str(v)));
    });
    tbody.append(tr);
  }
  t.append(thead, tbody);
  scroll.append(t);
  body.append(scroll);
  return nothing;
}

// Note ──

function note(w: Widget, body: HTMLElement): Dispose {
  const box = h('div', 'bd-note');
  for (const b of parseNote(str(w['text']))) {
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
  body.append(box);
  return nothing;
}

// Timeline ──

function timeline(w: Widget, body: HTMLElement): Dispose {
  const events = records(w['events']);
  const T = layoutTimeline(events.map((e) => ({ start: e['start'], end: e['end'] })));
  const when = (e: Record<string, unknown>): string => {
    const end = str(e['end']);
    return end ? `${str(e['start'])}–${end}` : str(e['start']);
  };

  // A track for a card wide enough to read one; the agenda beneath says the same in rows.
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
    const text = h('div', 'bd-item-text');
    text.append(h('strong', '', str(e['title'])));
    const meta = str(e['meta']);
    if (meta) text.append(h('span', 'bd-sub', meta));
    li.append(text);
    ol.append(li);
  });
  body.append(ol);
  return nothing;
}

// Compare ──

function compare(w: Widget, body: HTMLElement): Dispose {
  const pick = str(w['pick']);
  const grid = h('div', 'bd-options');
  for (const o of records(w['options'])) {
    const name = str(o['name']);
    const card = h('article', 'bd-option');
    const top = h('div', 'bd-option-top');
    top.append(h('h4', 'bd-option-name', name));
    if (pick && name === pick) {
      card.dataset['pick'] = '';
      top.append(h('span', 'bd-pick', 'Her pick'));
    }
    card.append(top);
    const metrics = records(o['metrics']);
    if (metrics.length) {
      const dl = h('dl', 'bd-bars');
      for (const m of metrics) {
        const score = typeof m['score'] === 'number' && Number.isFinite(m['score']) ? Math.min(10, Math.max(0, m['score'])) : 0;
        const dd = h('dd');
        dd.title = `${score} of 10`;
        const fill = h('i');
        fill.style.setProperty('--v', String(score / 10));
        dd.append(fill, h('span', 'visually-hidden', `${score} of 10`));
        dl.append(h('dt', '', str(m['label'])), dd);
      }
      card.append(dl);
    }
    const tags = Array.isArray(o['tags']) ? (o['tags'] as unknown[]).map(str).filter(Boolean) : [];
    if (tags.length) {
      const row = h('div', 'bd-tags');
      for (const t of tags) row.append(h('span', 'bd-tag', t));
      card.append(row);
    }
    const n = str(o['note']);
    if (n) card.append(h('p', 'bd-sub', n));
    grid.append(card);
  }
  body.append(grid);
  const reason = str(w['reason']);
  if (reason) body.append(h('p', 'bd-reason', reason));
  return nothing;
}

const DRAW: Readonly<Record<string, (w: Widget, body: HTMLElement) => Dispose>> = { stat, chart, list, table, note, timeline, compare };

/** The name a card goes by: its title, or for a stat its label. */
function nameOf(w: Widget): string {
  return str(w['title']) || (w.type === 'stat' ? str(w['label']) : '');
}

function card(w: Widget, onRemove: (id: string) => void): { el: HTMLElement; dispose: Dispose } {
  // The type is an attribute, not a class: a class named for it (bd-list, bd-table) would also be its parts'.
  const el = h('section', 'bd-card');
  el.dataset['type'] = w.type;
  el.dataset['size'] = w.size;
  const name = nameOf(w);
  const head = h('header', 'bd-card-head');
  if (name) {
    head.append(h('h3', 'bd-card-title', name));
    el.setAttribute('aria-label', name);
  }
  const x = h('button', 'bd-x');
  x.type = 'button';
  x.setAttribute('aria-label', name ? `Take “${name}” off the board` : 'Take this off the board');
  x.title = 'Take off the board';
  x.append(icon('close', 14));
  x.addEventListener('click', () => {
    el.dataset['leaving'] = '';
    x.disabled = true;
    onRemove(w.id);
  });
  head.append(x);
  const body = h('div', 'bd-card-body');
  el.append(head, body);
  let dispose = nothing;
  try {
    dispose = (DRAW[w.type] ?? (() => nothing))(w, body);
  } catch {
    // A widget the page cannot draw must not take the rest of the board with it.
    body.replaceChildren(h('p', 'bd-sub', 'This could not be shown.'));
  }
  return { el, dispose };
}

export interface BoardOptions {
  /** The user took a widget off. */
  readonly onRemove: (id: string) => void;
  /** The empty state's link to the sample preview. */
  readonly onShowcase: () => void;
  /** The strip of approvals (approvals.ts), pinned above her widgets when there are any. */
  readonly approvals?: HTMLElement;
}

/** What the approvals strip holds: any card at all, and whether one is waiting for the user. */
export interface Pinned {
  readonly any: boolean;
  readonly waiting: boolean;
  /** Her name, for the label over her widgets while an approval waits. */
  readonly from: string;
}

const NOTHING_PINNED: Pinned = { any: false, waiting: false, from: '' };

/** Each card's span and each row's height, for a wide (6) and a middling (2) workspace, and one column. */
function layoutRows(widgets: readonly Widget[], cards: readonly HTMLElement[], grid: HTMLElement): void {
  const wide = planRows(widgets, 6);
  const mid = planRows(widgets, 2);
  cards.forEach((c, i) => {
    c.style.setProperty('--wide', String(wide.spans[i] ?? 6));
    c.style.setProperty('--mid', String(mid.spans[i] ?? 2));
  });
  grid.style.setProperty('--rows-wide', rowTemplate(wide.rows));
  grid.style.setProperty('--rows-mid', rowTemplate(mid.rows));
  grid.style.setProperty('--rows-one', rowTemplate(planRows(widgets, 1).rows));
}

export interface BoardView {
  readonly el: HTMLElement;
  /**
   * Draw `board` (or the empty state), keeping what has not changed and the scroll position.
   * With approvals pinned, they go above her widgets and the board is not empty without them.
   */
  draw(board: Board | null, pinned?: Pinned): void;
  /** The board is no longer shown: stop watching card sizes. The next draw starts afresh. */
  dispose(): void;
}

/**
 * A card kept from the last draw is live again: a removal that never came back (refused, or lost
 * with the connection) leaves it as it was, not dimmed with a dead ×.
 */
export function revive(card: Pick<HTMLElement, 'removeAttribute' | 'querySelector'>): void {
  card.removeAttribute('data-leaving');
  const x = card.querySelector<HTMLButtonElement>('.bd-x');
  if (x) x.disabled = false;
}

export function createBoardView(opts: BoardOptions): BoardView {
  const el = h('div', 'bd');
  el.tabIndex = -1;
  const grid = h('div', 'bd-grid');
  // Over her widgets while an approval waits: what follows is her context, not what is approved.
  const from = h('h3', 'bd-from');
  let emptyEl: HTMLElement | null = null;
  // By id: what it was drawn from, so an unchanged widget keeps its element (and its hover).
  let drawn = new Map<string, { key: string; el: HTMLElement; dispose: Dispose }>();

  function empty(): HTMLElement {
    const box = h('div', 'bd-empty bd-new');
    const glow = h('div', 'bd-empty-glow');
    glow.setAttribute('aria-hidden', 'true');
    const link = h('button', 'bd-link', 'See a sample board');
    link.type = 'button';
    link.addEventListener('click', () => opts.onShowcase());
    box.append(
      glow,
      h('h3', 'bd-empty-title', 'Ask her to show you something'),
      h('p', 'bd-empty-text', 'Numbers, plans and side-by-side choices appear here when she puts them on the board.'),
      link,
    );
    return box;
  }

  function dispose(): void {
    for (const d of drawn.values()) d.dispose();
    drawn = new Map();
    grid.replaceChildren();
    emptyEl = null;
  }

  return {
    el,
    dispose,
    draw(board, pinned = NOTHING_PINNED) {
      // Opening afresh: every card arrives. Already open: only new ones do.
      const fresh = !el.isConnected;
      if (fresh) dispose();
      const scroller = el.closest('.workspace-body');
      const top = scroller?.scrollTop ?? 0;
      const active = document.activeElement;
      const focused = active instanceof HTMLElement && el.contains(active) ? active : null;
      const widgets = board?.widgets ?? [];
      const next = new Map<string, { key: string; el: HTMLElement; dispose: Dispose }>();
      const cards: HTMLElement[] = [];
      let arriving = 0;
      for (const w of widgets) {
        const key = JSON.stringify(w);
        const old = drawn.get(w.id);
        if (old && old.key === key) {
          old.el.classList.remove('bd-new');
          revive(old.el);
          next.set(w.id, old);
          cards.push(old.el);
          continue;
        }
        const c = card(w, opts.onRemove);
        if (!old) {
          c.el.classList.add('bd-new');
          c.el.style.setProperty('--i', String(arriving++));
        }
        next.set(w.id, { key, ...c });
        cards.push(c.el);
      }
      for (const [id, old] of drawn) if (next.get(id) !== old) old.dispose();
      drawn = next;
      layoutRows(widgets, cards, grid);
      if (cards.length) {
        // In place: what went is taken out, and only new, changed or moved cards are put in, so an
        // unchanged card is never detached (its focus and its scroll survive).
        const keep = new Set(cards);
        for (const child of [...grid.children]) if (!keep.has(child as HTMLElement)) child.remove();
        cards.forEach((c, i) => {
          const at = grid.children[i] ?? null;
          if (at !== c) grid.insertBefore(c, at);
        });
      } else {
        grid.replaceChildren();
      }
      const parts: HTMLElement[] = [];
      if (pinned.any && opts.approvals) parts.push(opts.approvals);
      if (cards.length) {
        if (pinned.waiting) {
          from.textContent = `From ${pinned.from}`;
          parts.push(from);
        }
        parts.push(grid);
      } else if (!pinned.any) {
        emptyEl ??= empty();
        parts.push(emptyEl);
      }
      // Shown again later, the empty state arrives afresh.
      if (emptyEl && !parts.includes(emptyEl)) emptyEl = null;
      // In place, so the strip and the grid stay attached (and keep focus) while what is beside them changes.
      const keepParts = new Set(parts);
      for (const child of [...el.children]) if (!keepParts.has(child as HTMLElement)) child.remove();
      parts.forEach((p, i) => {
        const at = el.children[i] ?? null;
        if (at !== p) el.insertBefore(p, at);
      });
      if (scroller) scroller.scrollTop = top;
      if (focused?.isConnected) {
        // A moved card lost focus on the way: it goes back where it was.
        if (document.activeElement !== focused) focused.focus({ preventScroll: true });
      } else if (focused) {
        // The card that had focus went (its × was pressed): focus stays on the board.
        el.focus({ preventScroll: true });
      }
    },
  };
}
