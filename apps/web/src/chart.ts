/**
 * Chart maths for the board: pure functions that turn a chart widget into
 * pixel-space shapes. The board draws them as inline SVG and sets every piece
 * of text with textContent; nothing here touches the DOM.
 *
 * Most callers need only `layoutChart(spec, width, height)`. The smaller
 * helpers (ticks, scales, paths, bars, arcs, formatValue) are exported so they
 * can be tested on their own.
 */

export type ChartKind = 'line' | 'bar' | 'area' | 'donut';

export interface ChartSeries {
  readonly name: string;
  readonly values: readonly number[];
}

export interface ChartSpec {
  readonly kind: ChartKind;
  readonly x: readonly string[];
  readonly series: readonly ChartSeries[];
  readonly unit?: string;
}

export interface Point {
  readonly x: number;
  readonly y: number;
}

export interface Rect {
  readonly series: number;
  readonly index: number;
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export type Scale = (v: number) => number;

const MAX_SERIES = 5;
const MAX_POINTS = 200;

/** Rounds to 2 decimals and avoids "-0", so path strings stay short and stable. */
function n2(v: number): string {
  const r = Math.round(v * 100) / 100;
  return String(r === 0 ? 0 : r);
}

function finite(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0;
}

/** Linear map from a domain to a range. An empty domain maps to the range's middle. */
export function scale(domain: readonly [number, number], range: readonly [number, number]): Scale {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  if (d0 === d1) {
    const mid = (r0 + r1) / 2;
    return () => mid;
  }
  // Halving first keeps huge values from overflowing; it costs no precision.
  const span = d1 / 2 - d0 / 2;
  return (v) => r0 + ((v / 2 - d0 / 2) / span) * (r1 - r0);
}

/** The nearest of 1, 2, 5 or 10 times a power of ten. */
function niceStep(raw: number): number {
  const exp = Math.floor(Math.log10(raw));
  const base = 10 ** exp;
  const f = raw / base;
  const nf = f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10;
  return nf * base;
}

/**
 * Round tick values that cover [min, max], about `count` of them, with steps
 * of 1, 2 or 5 times a power of ten. Zero is included unless `includeZero` is
 * false. Equal values still get a usable range.
 */
export function niceTicks(min: number, max: number, count = 5, includeZero = true): number[] {
  let lo = finite(Math.min(min, max));
  let hi = finite(Math.max(min, max));
  if (includeZero) {
    lo = Math.min(lo, 0);
    hi = Math.max(hi, 0);
  }
  if (lo === hi) {
    const pad = Math.abs(lo) * 0.1 || 1;
    lo -= pad;
    hi += pad;
  }
  const span = hi - lo;
  const gaps = Math.max(1, Math.floor(count) - 1);
  const step = niceStep(span / gaps);
  if (!Number.isFinite(span) || !Number.isFinite(step) || step <= 0) {
    return lo < hi ? [lo, hi] : [0, 1];
  }
  const first = Math.floor(lo / step + 1e-9);
  const last = Math.ceil(hi / step - 1e-9);
  if (!Number.isFinite(first) || !Number.isFinite(last) || last - first > 200) {
    return [lo, hi];
  }
  // Whole multiples of the step, trimmed to 12 digits, so 0.1 + 0.2 noise never shows.
  const out: number[] = [];
  for (let i = first; i <= last; i++) {
    out.push(Number((i * step).toPrecision(12)) + 0);
  }
  return out;
}

/** "M x y L x y …"; one point is just a move, none is empty. */
export function linePath(points: readonly Point[]): string {
  if (points.length === 0) return '';
  return points.map((p, i) => `${i === 0 ? 'M' : 'L'}${n2(p.x)} ${n2(p.y)}`).join(' ');
}

/** The line closed down to `baseline` (a y value), for a filled area. */
export function areaPath(points: readonly Point[], baseline: number): string {
  if (points.length === 0) return '';
  const first = points[0]!;
  const last = points[points.length - 1]!;
  return `${linePath(points)} L${n2(last.x)} ${n2(baseline)} L${n2(first.x)} ${n2(baseline)} Z`;
}

/**
 * Grouped bars. `series[s][i]` is series s at category i. Each category gets an
 * equal band across `plot`; the bars in a band sit side by side with some air
 * around the group. Bars start at the zero line and go down for negatives.
 */
export function bars(
  series: readonly (readonly number[])[],
  y: Scale,
  plot: { left: number; top: number; width: number; height: number },
): Rect[] {
  const count = Math.max(0, ...series.map((s) => s.length));
  if (count === 0 || series.length === 0) return [];
  const band = plot.width / count;
  const group = band * 0.7;
  const barW = group / series.length;
  const zero = Math.min(plot.top + plot.height, Math.max(plot.top, y(0)));
  const out: Rect[] = [];
  series.forEach((values, s) => {
    for (let i = 0; i < count; i++) {
      const top = Math.min(plot.top + plot.height, Math.max(plot.top, y(finite(values[i]))));
      out.push({
        series: s,
        index: i,
        x: plot.left + i * band + (band - group) / 2 + s * barW,
        y: Math.min(top, zero),
        width: barW,
        height: Math.abs(zero - top),
      });
    }
  });
  return out;
}

export interface Arc {
  readonly index: number;
  readonly value: number;
  /** Share of the whole, 0 to 1. */
  readonly fraction: number;
  /** A closed ring segment. */
  readonly d: string;
}

function polar(cx: number, cy: number, r: number, angle: number): string {
  return `${n2(cx + r * Math.cos(angle))} ${n2(cy + r * Math.sin(angle))}`;
}

/** A full ring as two half circles each way (one arc cannot close on itself). */
function fullRing(cx: number, cy: number, r: number, inner: number): string {
  const ring = (rad: number, sweep: 0 | 1) =>
    `M${n2(cx + rad)} ${n2(cy)} A${n2(rad)} ${n2(rad)} 0 1 ${sweep} ${n2(cx - rad)} ${n2(cy)} ` +
    `A${n2(rad)} ${n2(rad)} 0 1 ${sweep} ${n2(cx + rad)} ${n2(cy)} Z`;
  return inner > 0 ? `${ring(r, 1)} ${ring(inner, 0)}` : ring(r, 1);
}

/**
 * Ring segments for `values`, starting at twelve o'clock and going clockwise,
 * summing to the full circle. Zero, negative and non-finite values are skipped
 * (the returned `index` is the position in `values`). With nothing to draw the
 * result is empty; use `emptyRing` for the placeholder.
 */
export function donutArcs(
  values: readonly number[],
  r: number,
  inner: number,
  cx = 0,
  cy = 0,
): Arc[] {
  const safe = values.map((v) => (finite(v) > 0 ? finite(v) : 0));
  const total = safe.reduce((a, b) => a + b, 0);
  if (total <= 0) return [];
  const innerR = Math.max(0, Math.min(inner, r));
  const arcs: Arc[] = [];
  let start = -Math.PI / 2;
  let whole = false;
  safe.forEach((value, index) => {
    if (value <= 0) return;
    const fraction = value / total;
    if (fraction >= 0.9999) {
      // The rest are too small to see; this slice is the whole ring.
      arcs.length = 0;
      arcs.push({ index, value, fraction: 1, d: fullRing(cx, cy, r, innerR) });
      whole = true;
      return;
    }
    if (whole) return;
    const end = start + fraction * Math.PI * 2;
    const large = fraction > 0.5 ? 1 : 0;
    let d =
      `M${polar(cx, cy, r, start)} A${n2(r)} ${n2(r)} 0 ${large} 1 ${polar(cx, cy, r, end)} `;
    d +=
      innerR > 0
        ? `L${polar(cx, cy, innerR, end)} A${n2(innerR)} ${n2(innerR)} 0 ${large} 0 ${polar(cx, cy, innerR, start)} Z`
        : `L${n2(cx)} ${n2(cy)} Z`;
    arcs.push({ index, value, fraction, d });
    start = end;
  });
  return arcs;
}

/** The empty ring drawn when a donut has no positive values. */
export function emptyRing(r: number, inner: number, cx = 0, cy = 0): string {
  return fullRing(cx, cy, r, Math.max(0, Math.min(inner, r)));
}

const PREFIX_UNITS = new Set(['$', '€', '£', '¥']);

const STEPS: [number, string][] = [
  [1e12, 'T'],
  [1e9, 'B'],
  [1e6, 'M'],
  [1e3, 'k'],
  [1, ''],
];

/**
 * Compact number: 1.2k, 3.4M, 0.0042. `digits` is the decimals kept on the
 * scaled part (more when ticks need telling apart). Never throws; non-finite
 * gives a dash.
 */
export function compact(v: number, digits = 1): string {
  if (!Number.isFinite(v)) return '–';
  const sign = v < 0 ? '-' : '';
  const a = Math.abs(v);
  if (a === 0) return '0';
  const d = Math.min(Math.max(0, Math.floor(digits)), 8);
  for (let i = 0; i < STEPS.length; i++) {
    const [size, suffix] = STEPS[i]!;
    if (a < size) continue;
    let scaled = Number((a / size).toFixed(size === 1 ? Math.max(2, d) : d));
    // 999.96k rounds to 1000k; say 1M instead.
    if (scaled >= 1000 && i > 0) return `${sign}1${STEPS[i - 1]![1]}`;
    if (d === 1 && size > 1 && scaled >= 100) scaled = Math.round(scaled);
    return `${sign}${scaled}${suffix}`;
  }
  if (a >= 1e-6) return sign + String(Number(a.toPrecision(Math.max(2, d + 1))));
  return sign + a.toExponential(Math.max(0, d - 1));
}

/**
 * A value with its unit: "$1.2k", "45%", "3.4 kWh", "21°C". Currency symbols go
 * in front; % and ° stick to the number; other units follow after a space.
 */
export function formatValue(v: number, unit?: string, digits = 1): string {
  return withUnit(compact(v, digits), unit);
}

function withUnit(body: string, unit?: string): string {
  const u = (unit ?? '').trim();
  if (!u || body === '–') return body;
  if (PREFIX_UNITS.has(u)) {
    return body.startsWith('-') ? `-${u}${body.slice(1)}` : `${u}${body}`;
  }
  if (u.startsWith('%') || u.startsWith('°')) return `${body}${u}`;
  return `${body} ${u}`;
}

/**
 * Labels for axis ticks, with just enough precision that neighbours never
 * read the same (1.5k, 1.55k, 1.6k rather than 1.5k, 1.5k, 1.6k).
 */
export function tickLabels(ticks: readonly number[], unit?: string): string[] {
  let labels: string[] = [];
  for (let d = 1; d <= 8; d++) {
    labels = ticks.map((t) => formatValue(t, unit, d));
    if (new Set(labels).size === labels.length) break;
  }
  return labels;
}

/** Whole percents of `shares` (which sum to 1) that add up to exactly 100. */
export function percents(shares: readonly number[]): number[] {
  const raw = shares.map((f) => Math.max(0, f) * 100);
  const out = raw.map((v) => Math.floor(v));
  let left = 100 - out.reduce((a, b) => a + b, 0);
  const order = raw.map((v, i) => [v - Math.floor(v), i] as const).sort((a, b) => b[0] - a[0]);
  for (let k = 0; left > 0 && order.length > 0; k = (k + 1) % order.length, left--) {
    out[order[k]![1]]! += 1;
  }
  return out;
}

// ---- layout ------------------------------------------------------------

export interface YTick {
  readonly value: number;
  readonly y: number;
  readonly label: string;
}

export interface XLabel {
  readonly index: number;
  readonly x: number;
  readonly label: string;
  /** Text-anchor to use, so the first and last labels do not clip. */
  readonly anchor: 'start' | 'middle' | 'end';
}

export interface LaidSeries {
  readonly name: string;
  /** 0-4: which series colour to use. */
  readonly color: number;
  readonly points: readonly Point[];
  /** Line and area charts. */
  readonly line: string;
  /** Area charts only; empty otherwise. */
  readonly area: string;
}

export interface LegendEntry {
  readonly name: string;
  readonly color: number;
  /** Donut slices: the formatted value, e.g. "$40". */
  readonly value?: string;
  /** Donut slices: whole percent of the total. */
  readonly percent?: number;
}

export interface LaidArc extends Arc {
  readonly name: string;
  readonly color: number;
}

export interface ChartLayout {
  readonly kind: ChartKind;
  readonly width: number;
  readonly height: number;
  /** True when there is nothing to draw (no series, or a donut of zeros). */
  readonly empty: boolean;
  /** Plot area for line, area and bar charts. */
  readonly plot: { left: number; top: number; right: number; bottom: number };
  readonly yTicks: readonly YTick[];
  /** Where the zero line sits (a y pixel), for the axis rule. */
  readonly zeroY: number;
  readonly xLabels: readonly XLabel[];
  readonly series: readonly LaidSeries[];
  readonly rects: readonly Rect[];
  /** Donut only. */
  readonly arcs: readonly LaidArc[];
  readonly donut?: { cx: number; cy: number; r: number; inner: number; ring: string; total: string };
  readonly legend: readonly LegendEntry[];
}

const LABEL_CHAR_W = 6;

const MAX_SLICES = 8;

interface Slice {
  readonly name: string;
  readonly value: number;
  readonly other: boolean;
}

/** Positive values as slices; past 8, the smallest fold into one "Other". */
function donutSlices(values: readonly number[], nameAt: (i: number) => string): Slice[] {
  const all: Slice[] = [];
  values.forEach((v, i) => {
    if (v > 0) all.push({ name: nameAt(i), value: v, other: false });
  });
  if (all.length <= MAX_SLICES) return all;
  const keep = new Set(
    all
      .map((sl, i) => [sl.value, i] as const)
      .sort((p, q) => q[0] - p[0] || p[1] - q[1])
      .slice(0, MAX_SLICES - 1)
      .map(([, i]) => i),
  );
  const rest = all.filter((_, i) => !keep.has(i)).reduce((a, sl) => a + sl.value, 0);
  return [...all.filter((_, i) => keep.has(i)), { name: 'Other', value: rest, other: true }];
}

/** Colours 0-4 in turn, adjusted so the last slice differs from the first. */
function sliceColors(n: number): number[] {
  const out = Array.from({ length: n }, (_, i) => i % MAX_SERIES);
  if (n > 2 && out[n - 1] === out[0]) {
    out[n - 1] = [0, 1, 2, 3, 4].find((c) => c !== out[n - 2] && c !== out[0])!;
  }
  return out;
}

/**
 * Everything the board needs to draw one chart in a `width` x `height` box.
 * Series beyond 5 and points beyond 200 are dropped; missing or non-finite
 * values count as zero, so a bad spec draws something rather than throwing.
 */
export function layoutChart(spec: ChartSpec, width: number, height: number): ChartLayout {
  const w = Math.max(1, finite(width));
  const h = Math.max(1, finite(height));
  const series = spec.series.slice(0, spec.kind === 'donut' ? 1 : MAX_SERIES);
  const count = Math.min(
    MAX_POINTS,
    Math.max(spec.x.length, ...series.map((s) => s.values.length), 0),
  );
  const data = series.map((s) =>
    Array.from({ length: count }, (_, i) => finite(s.values[i])),
  );
  const labelAt = (i: number) => spec.x[i] ?? '';

  const base = {
    kind: spec.kind,
    width: w,
    height: h,
    plot: { left: 0, top: 0, right: w, bottom: h },
    yTicks: [] as YTick[],
    zeroY: h,
    xLabels: [] as XLabel[],
    series: [] as LaidSeries[],
    rects: [] as Rect[],
    arcs: [] as LaidArc[],
  };

  if (spec.kind === 'donut') {
    const slices = donutSlices(data[0] ?? [], labelAt);
    const cx = w / 2;
    const cy = h / 2;
    const r = Math.max(1, Math.min(w, h) / 2 - 2);
    const inner = r * 0.62;
    const drawn = donutArcs(
      slices.map((sl) => sl.value),
      r,
      inner,
      cx,
      cy,
    );
    const colors = sliceColors(drawn.length);
    const arcs: LaidArc[] = drawn.map((a, k) => {
      const sl = slices[a.index]!;
      return { ...a, name: sl.name, color: sl.other ? -1 : colors[k]! };
    });
    const total = slices.reduce((a, sl) => a + sl.value, 0);
    const pct = percents(arcs.map((a) => a.fraction));
    return {
      ...base,
      empty: arcs.length === 0,
      arcs,
      donut: {
        cx,
        cy,
        r,
        inner,
        ring: emptyRing(r, inner, cx, cy),
        total: formatValue(total, spec.unit),
      },
      legend: arcs.map((a, k) => ({
        name: a.name,
        color: a.color,
        value: formatValue(a.value, spec.unit),
        percent: pct[k]!,
      })),
    };
  }

  const legend = series.map((s, i) => ({ name: s.name, color: i }));
  const all = data.flat();
  if (series.length === 0 || count === 0 || all.length === 0) {
    return { ...base, empty: true, legend };
  }

  const min = Math.min(...all);
  const max = Math.max(...all);
  // A line may float away from zero when its values sit well above it.
  const floats = spec.kind === 'line' && min > 0 && min > max * 0.5;
  const tickCount = Math.max(2, Math.min(6, Math.floor((h - 28) / 36) + 1));
  const ticks = niceTicks(min, max, tickCount, !floats);
  const domain: [number, number] = [ticks[0]!, ticks[ticks.length - 1]!];

  const labels = tickLabels(ticks, spec.unit);
  const left = Math.min(w / 3, Math.max(...labels.map((l) => l.length)) * LABEL_CHAR_W + 10);
  const hasX = spec.x.length > 0;
  const top = Math.min(8, h / 4);
  const plot = {
    left,
    top,
    right: Math.max(left + 1, w - 8),
    bottom: Math.max(top + 1, h - (hasX ? 22 : 8)),
  };
  const plotW = Math.max(1, plot.right - plot.left);
  const plotH = Math.max(1, plot.bottom - plot.top);
  const y = scale(domain, [plot.bottom, plot.top]);

  const band = plotW / count;
  const px = (i: number): number => {
    if (spec.kind === 'bar') return plot.left + band * (i + 0.5);
    if (count === 1) return plot.left + plotW / 2;
    return plot.left + 6 + (i / (count - 1)) * Math.max(1, plotW - 12);
  };

  const widest = Math.max(1, ...Array.from({ length: count }, (_, i) => labelAt(i).length));
  const labelW = widest * LABEL_CHAR_W + 12;
  const fit = Math.max(1, Math.floor(plotW / labelW));
  const every = Math.ceil(count / fit);
  const shown: number[] = [];
  if (hasX) {
    for (let i = 0; i < count; i += every) shown.push(i);
    // The last point is always named; it pushes out a neighbour that is too close.
    if (shown[shown.length - 1] !== count - 1) {
      if (count - 1 - shown[shown.length - 1]! < every) shown.pop();
      shown.push(count - 1);
    }
  }
  const xLabels: XLabel[] = shown
    .filter((i) => labelAt(i) !== '')
    .map((i) => {
      const x = px(i);
      const half = (labelAt(i).length * LABEL_CHAR_W) / 2;
      let anchor: XLabel['anchor'] = 'middle';
      if (count > 1 && spec.kind !== 'bar') {
        anchor = i === 0 ? 'start' : i === count - 1 ? 'end' : 'middle';
      } else if (x - half < 0) anchor = 'start';
      else if (x + half > w) anchor = 'end';
      return { index: i, x, label: labelAt(i), anchor };
    });

  const zeroY = Math.min(plot.bottom, Math.max(plot.top, y(0)));
  const laid: LaidSeries[] =
    spec.kind === 'bar'
      ? []
      : series.map((s, i) => {
          const points = data[i]!.map((v, k) => ({ x: px(k), y: y(v) }));
          return {
            name: s.name,
            color: i,
            points,
            line: linePath(points),
            area: spec.kind === 'area' ? areaPath(points, zeroY) : '',
          };
        });

  return {
    ...base,
    empty: false,
    plot,
    yTicks: ticks.map((value, i) => ({ value, y: y(value), label: labels[i]! })),
    zeroY,
    xLabels,
    series: laid,
    rects:
      spec.kind === 'bar'
        ? bars(data, y, { left: plot.left, top: plot.top, width: plotW, height: plotH })
        : [],
    legend,
  };
}
