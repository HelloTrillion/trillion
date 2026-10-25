import { describe, expect, it } from 'vitest';
import {
  areaPath,
  bars,
  donutArcs,
  emptyRing,
  formatValue,
  layoutChart,
  linePath,
  percents,
  tickLabels,
  niceTicks,
  scale,
} from '../src/chart';

describe('niceTicks', () => {
  it('uses round steps and includes zero', () => {
    expect(niceTicks(3, 97, 5)).toEqual([0, 20, 40, 60, 80, 100]);
    expect(niceTicks(1.2, 3.4, 5)).toEqual([0, 1, 2, 3, 4]);
  });

  it('covers negatives and all-negative data', () => {
    expect(niceTicks(-45, 80, 5)).toEqual([-50, 0, 50, 100]);
    expect(niceTicks(-9, -3, 4)).toEqual([-10, -5, 0]);
  });

  it('can leave zero out', () => {
    const t = niceTicks(1000, 1010, 5, false);
    expect(t[0]).toBeGreaterThanOrEqual(1000 - 5);
    expect(t[t.length - 1]).toBeLessThanOrEqual(1015);
  });

  it('gives equal values a range', () => {
    expect(niceTicks(5, 5, 5).length).toBeGreaterThan(1);
    const t = niceTicks(0, 0, 5);
    expect(t).toContain(0);
    expect(t.length).toBeGreaterThan(1);
  });

  it('has no float noise', () => {
    expect(niceTicks(0, 0.3, 4)).toEqual([0, 0.1, 0.2, 0.3]);
  });
});

describe('scale', () => {
  it('maps linearly and can invert', () => {
    const s = scale([0, 10], [100, 0]);
    expect(s(0)).toBe(100);
    expect(s(5)).toBe(50);
    expect(s(10)).toBe(0);
  });

  it('maps an empty domain to the middle', () => {
    expect(scale([3, 3], [0, 100])(3)).toBe(50);
  });
});

describe('paths', () => {
  it('draws a line', () => {
    expect(linePath([{ x: 0, y: 10 }, { x: 5.123, y: 20 }])).toBe('M0 10 L5.12 20');
  });

  it('handles one point and none', () => {
    expect(linePath([{ x: 3, y: 4 }])).toBe('M3 4');
    expect(linePath([])).toBe('');
    expect(areaPath([], 10)).toBe('');
  });

  it('closes an area down to the baseline', () => {
    expect(areaPath([{ x: 0, y: 5 }, { x: 10, y: 2 }], 20)).toBe('M0 5 L10 2 L10 20 L0 20 Z');
  });
});

describe('bars', () => {
  const plot = { left: 0, top: 0, width: 100, height: 100 };

  it('groups series side by side from the zero line', () => {
    const y = scale([0, 10], [100, 0]);
    const r = bars([[10, 5], [5, 0]], y, plot);
    expect(r).toHaveLength(4);
    const a = r.find((b) => b.series === 0 && b.index === 0)!;
    expect(a.y).toBe(0);
    expect(a.height).toBe(100);
    const b = r.find((b) => b.series === 1 && b.index === 0)!;
    expect(b.x).toBeCloseTo(a.x + a.width);
    expect(b.height).toBe(50);
    expect(r.find((b) => b.series === 1 && b.index === 1)!.height).toBe(0);
  });

  it('puts negative bars below the zero line', () => {
    const y = scale([-10, 10], [100, 0]);
    const [neg] = bars([[-5]], y, plot);
    expect(neg!.y).toBe(50);
    expect(neg!.height).toBe(25);
  });

  it('copes with nothing', () => {
    expect(bars([], scale([0, 1], [1, 0]), plot)).toEqual([]);
    expect(bars([[]], scale([0, 1], [1, 0]), plot)).toEqual([]);
  });
});

describe('donutArcs', () => {
  it('shares sum to the full circle', () => {
    const arcs = donutArcs([1, 2, 3, 4], 50, 30, 50, 50);
    expect(arcs).toHaveLength(4);
    expect(arcs.reduce((a, b) => a + b.fraction, 0)).toBeCloseTo(1);
    expect(arcs[0]!.fraction).toBeCloseTo(0.1);
    expect(arcs[0]!.d.startsWith('M50 0 A50 50 0 0 1')).toBe(true);
  });

  it('flags large arcs', () => {
    expect(donutArcs([3, 1], 10, 5)[0]!.d).toContain('A10 10 0 1 1');
  });

  it('skips zero, negative and bad values but keeps indexes', () => {
    const arcs = donutArcs([0, 5, -2, Number.NaN, 5], 10, 5);
    expect(arcs.map((a) => a.index)).toEqual([1, 4]);
    expect(arcs[0]!.fraction).toBeCloseTo(0.5);
  });

  it('draws one value as a whole ring', () => {
    const arcs = donutArcs([0, 7], 10, 5);
    expect(arcs).toHaveLength(1);
    expect(arcs[0]!.fraction).toBe(1);
    expect(arcs[0]!.d).not.toContain('NaN');
  });

  it('is empty when all zeros', () => {
    expect(donutArcs([0, 0], 10, 5)).toEqual([]);
    expect(donutArcs([], 10, 5)).toEqual([]);
    expect(emptyRing(10, 5)).toContain('A10 10');
  });
});

describe('formatValue', () => {
  it('compacts', () => {
    expect(formatValue(0)).toBe('0');
    expect(formatValue(3.4)).toBe('3.4');
    expect(formatValue(41.2)).toBe('41.2');
    expect(formatValue(1234)).toBe('1.2k');
    expect(formatValue(3_400_000)).toBe('3.4M');
    expect(formatValue(2_500_000_000)).toBe('2.5B');
    expect(formatValue(7e12)).toBe('7T');
    expect(formatValue(1000)).toBe('1k');
    expect(formatValue(-1500)).toBe('-1.5k');
  });

  it('rolls over at the edges', () => {
    expect(formatValue(999_960)).toBe('1M');
    expect(formatValue(999.999)).toBe('1k');
  });

  it('handles very large, very small and bad numbers', () => {
    expect(formatValue(1e15)).toBe('1000T');
    expect(formatValue(0.0042)).toBe('0.0042');
    expect(formatValue(0.5)).toBe('0.5');
    expect(formatValue(1e-9)).toBe('1e-9');
    expect(formatValue(Number.NaN, '$')).toBe('–');
    expect(formatValue(Number.POSITIVE_INFINITY)).toBe('–');
  });

  it('puts currency first and the rest after', () => {
    expect(formatValue(1234, '$')).toBe('$1.2k');
    expect(formatValue(-5, '€')).toBe('-€5');
    expect(formatValue(45, '%')).toBe('45%');
    expect(formatValue(21, '°C')).toBe('21°C');
    expect(formatValue(3.4, 'kWh')).toBe('3.4 kWh');
    expect(formatValue(3, '  ')).toBe('3');
    expect(formatValue(3, undefined)).toBe('3');
  });
});

describe('layoutChart', () => {
  const x = ['Mon', 'Tue', 'Wed', 'Thu'];
  const series = [{ name: 'Spend', values: [1, 4, 2, 8] }];

  it('lays out a line in pixel space', () => {
    const l = layoutChart({ kind: 'line', x, series, unit: '$' }, 400, 200);
    expect(l.empty).toBe(false);
    expect(l.series).toHaveLength(1);
    expect(l.series[0]!.line.startsWith('M')).toBe(true);
    expect(l.series[0]!.area).toBe('');
    for (const p of l.series[0]!.points) {
      expect(p.x).toBeGreaterThanOrEqual(l.plot.left);
      expect(p.x).toBeLessThanOrEqual(l.plot.right);
      expect(p.y).toBeGreaterThanOrEqual(l.plot.top);
      expect(p.y).toBeLessThanOrEqual(l.plot.bottom);
    }
    expect(l.yTicks[0]!.label).toBe('$0');
    expect(l.xLabels.map((t) => t.label)).toEqual(x);
    expect(l.legend).toEqual([{ name: 'Spend', color: 0 }]);
  });

  it('adds an area under the line', () => {
    const l = layoutChart({ kind: 'area', x, series }, 400, 200);
    expect(l.series[0]!.area.endsWith('Z')).toBe(true);
  });

  it('draws bars for every series and point', () => {
    const two = [...series, { name: 'Other', values: [3, 3, 3, 3] }];
    const l = layoutChart({ kind: 'bar', x, series: two }, 400, 200);
    expect(l.rects).toHaveLength(8);
    expect(l.series).toHaveLength(0);
    expect(l.legend.map((e) => e.color)).toEqual([0, 1]);
  });

  it('thins crowded x labels', () => {
    const many = Array.from({ length: 60 }, (_, i) => `Oct ${i + 1}`);
    const l = layoutChart(
      { kind: 'line', x: many, series: [{ name: 'a', values: many.map((_, i) => i) }] },
      300,
      150,
    );
    expect(l.xLabels.length).toBeLessThan(10);
    expect(l.xLabels[0]!.index).toBe(0);
  });

  it('survives all-equal and all-negative values', () => {
    const same = layoutChart({ kind: 'line', x, series: [{ name: 'a', values: [5, 5, 5, 5] }] }, 300, 150);
    expect(same.series[0]!.points.every((p) => Number.isFinite(p.y))).toBe(true);
    const neg = layoutChart({ kind: 'bar', x, series: [{ name: 'a', values: [-1, -2, -3, -4] }] }, 300, 150);
    expect(neg.rects.every((r) => r.height >= 0 && Number.isFinite(r.y))).toBe(true);
    expect(neg.yTicks[neg.yTicks.length - 1]!.value).toBe(0);
  });

  it('survives a single point', () => {
    const l = layoutChart({ kind: 'line', x: ['Mon'], series: [{ name: 'a', values: [3] }] }, 300, 150);
    expect(l.series[0]!.line).toMatch(/^M[\d.]+ [\d.]+$/);
    expect(l.series[0]!.points[0]!.x).toBeCloseTo((l.plot.left + l.plot.right) / 2);
  });

  it('does not crash on empty series', () => {
    expect(layoutChart({ kind: 'line', x: [], series: [] }, 300, 150).empty).toBe(true);
    expect(layoutChart({ kind: 'bar', x: ['a'], series: [{ name: 's', values: [] }] }, 300, 150).rects).toHaveLength(1);
    expect(layoutChart({ kind: 'donut', x: [], series: [] }, 300, 150).empty).toBe(true);
  });

  it('caps series and points', () => {
    const six = Array.from({ length: 6 }, (_, i) => ({ name: `s${i}`, values: [1, 2] }));
    expect(layoutChart({ kind: 'line', x: ['a', 'b'], series: six }, 300, 150).series).toHaveLength(5);
    const long = Array.from({ length: 300 }, (_, i) => i);
    const l = layoutChart({ kind: 'line', x: [], series: [{ name: 'a', values: long }] }, 300, 150);
    expect(l.series[0]!.points).toHaveLength(200);
    expect(l.xLabels).toEqual([]);
  });

  it('lays out a donut with a legend', () => {
    const l = layoutChart(
      { kind: 'donut', x: ['Food', 'Rent', 'Fun'], series: [{ name: 's', values: [30, 70, 0] }], unit: '$' },
      200,
      200,
    );
    expect(l.empty).toBe(false);
    expect(l.arcs.map((a) => a.name)).toEqual(['Food', 'Rent']);
    expect(l.legend).toEqual([
      { name: 'Food', color: 0, value: '$30', percent: 30 },
      { name: 'Rent', color: 1, value: '$70', percent: 70 },
    ]);
    expect(l.donut!.total).toBe('$100');
  });

  it('shows an empty ring for a donut of zeros', () => {
    const l = layoutChart({ kind: 'donut', x: ['a', 'b'], series: [{ name: 's', values: [0, 0] }] }, 200, 200);
    expect(l.empty).toBe(true);
    expect(l.arcs).toEqual([]);
    expect(l.donut!.ring).toContain('A');
  });

  it('treats bad numbers as zero', () => {
    const l = layoutChart({ kind: 'line', x: ['a', 'b'], series: [{ name: 's', values: [Number.NaN, 2] }] }, 300, 150);
    expect(l.series[0]!.line).not.toContain('NaN');
  });
});

describe('review fixes', () => {
  it('never repeats a y label on a floating line', () => {
    for (const [lo, hi] of [[1520, 1580], [100.1, 100.9], [0.51, 0.53], [99_000, 99_900], [1e-120, 3e-120]]) {
      const l = layoutChart(
        { kind: 'line', x: ['a', 'b'], series: [{ name: 's', values: [lo!, hi!] }] },
        300,
        200,
      );
      const labels = l.yTicks.map((t) => t.label);
      expect(new Set(labels).size).toBe(labels.length);
    }
    expect(tickLabels([1500, 1520, 1540], undefined)).toEqual(['1.5k', '1.52k', '1.54k']);
  });

  it('does not throw on tiny, huge or odd values', () => {
    for (const v of [1e-120, 1e-320, 1e308, -1e308, Number.MAX_VALUE]) {
      expect(() => niceTicks(0, v)).not.toThrow();
      expect(() => niceTicks(v, v)).not.toThrow();
      const l = layoutChart({ kind: 'bar', x: ['a', 'b'], series: [{ name: 's', values: [v, -v] }] }, 300, 200);
      expect(l.rects.every((r) => Number.isFinite(r.y) && Number.isFinite(r.height))).toBe(true);
    }
    expect(niceTicks(-1e308, 1e308).every(Number.isFinite)).toBe(true);
  });

  it('keeps only the whole ring when one slice fills it', () => {
    const arcs = donutArcs([1e9, 1, 1], 10, 5, 0, 0);
    expect(arcs).toHaveLength(1);
    expect(arcs[0]!.index).toBe(0);
    expect(arcs[0]!.d).toBe(
      'M10 0 A10 10 0 1 1 -10 0 A10 10 0 1 1 10 0 Z M5 0 A5 5 0 1 0 -5 0 A5 5 0 1 0 5 0 Z',
    );
    const l = layoutChart(
      { kind: 'donut', x: ['a', 'b', 'c'], series: [{ name: 's', values: [1e9, 1, 1] }] },
      200,
      200,
    );
    expect(l.arcs).toHaveLength(1);
    expect(l.legend).toEqual([{ name: 'a', color: 0, value: '1B', percent: 100 }]);
  });

  it('sets the large-arc flag on the inner edge too', () => {
    const [big] = donutArcs([3, 1], 10, 5);
    expect(big!.d).toContain('A5 5 0 1 0');
    const [small] = donutArcs([1, 3], 10, 5);
    expect(small!.d).toContain('A5 5 0 0 0');
    expect(small!.d).toContain('A10 10 0 0 1');
  });

  it('spaces x labels, names the last and anchors the ends', () => {
    const xs = Array.from({ length: 30 }, (_, i) => `Oct ${i + 1}`);
    const l = layoutChart(
      { kind: 'line', x: xs, series: [{ name: 'a', values: xs.map((_, i) => i) }] },
      300,
      150,
    );
    const idx = l.xLabels.map((t) => t.index);
    expect(idx[0]).toBe(0);
    expect(idx[idx.length - 1]).toBe(29);
    expect(l.xLabels[0]!.anchor).toBe('start');
    expect(l.xLabels[l.xLabels.length - 1]!.anchor).toBe('end');
    for (let i = 1; i < l.xLabels.length; i++) {
      expect(l.xLabels[i]!.x - l.xLabels[i - 1]!.x).toBeGreaterThanOrEqual(40);
    }
  });

  it('leaves out empty x labels', () => {
    const l = layoutChart(
      { kind: 'line', x: ['a', '', 'c'], series: [{ name: 's', values: [1, 2, 3] }] },
      300,
      150,
    );
    expect(l.xLabels.map((t) => t.label)).toEqual(['a', 'c']);
  });

  it('keeps the plot the right way up in tiny boxes', () => {
    for (const h of [1, 5, 20, 30]) {
      const l = layoutChart({ kind: 'bar', x: ['a'], series: [{ name: 's', values: [3] }] }, 40, h);
      expect(l.plot.bottom).toBeGreaterThan(l.plot.top);
      expect(l.plot.right).toBeGreaterThan(l.plot.left);
      expect(l.rects.every((r) => r.height >= 0)).toBe(true);
    }
  });

  it('rounds donut percents to exactly 100', () => {
    expect(percents([1 / 3, 1 / 3, 1 / 3]).reduce((a, b) => a + b, 0)).toBe(100);
    const l = layoutChart(
      { kind: 'donut', x: ['a', 'b', 'c'], series: [{ name: 's', values: [1, 1, 1] }] },
      200,
      200,
    );
    expect(l.legend.map((e) => e.percent)).toEqual([34, 33, 33]);
  });

  it('colours neighbours differently, including last next to first', () => {
    const vals = [5, 4, 3, 2, 1, 1];
    const l = layoutChart(
      { kind: 'donut', x: vals.map((_, i) => `s${i}`), series: [{ name: 's', values: vals }] },
      200,
      200,
    );
    const c = l.arcs.map((a) => a.color);
    for (let i = 0; i < c.length; i++) expect(c[i]).not.toBe(c[(i + 1) % c.length]);
  });

  it('rolls extra donut slices into Other', () => {
    const vals = Array.from({ length: 12 }, (_, i) => i + 1);
    const l = layoutChart(
      { kind: 'donut', x: vals.map((v) => `s${v}`), series: [{ name: 's', values: vals }] },
      200,
      200,
    );
    expect(l.legend).toHaveLength(8);
    const other = l.legend[7]!;
    expect(other.name).toBe('Other');
    expect(other.color).toBe(-1);
    expect(l.legend.reduce((a, e) => a + e.percent!, 0)).toBe(100);
    expect(l.legend.slice(0, 7).map((e) => e.name)).toEqual(['s6', 's7', 's8', 's9', 's10', 's11', 's12']);
  });
});
