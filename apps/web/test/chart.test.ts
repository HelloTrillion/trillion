import { describe, expect, it } from 'vitest';
import {
  areaPath,
  bars,
  donutArcs,
  emptyRing,
  formatValue,
  layoutChart,
  linePath,
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
    expect(formatValue(999.999)).toBe('1000');
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
