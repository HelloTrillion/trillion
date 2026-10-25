import { describe, expect, it } from 'vitest';
import {
  boardTitle,
  chartSpecOf,
  chartSummary,
  layoutTimeline,
  minutesOf,
  nearestIndex,
  parseNote,
  runs,
  spans,
  toneOf,
} from '../src/board';

describe('spans', () => {
  it('fills a row of six with any mix that adds up, and a row of two on a narrower workspace', () => {
    expect(spans('small')).toEqual({ wide: 2, mid: 1 });
    expect(spans('medium')).toEqual({ wide: 3, mid: 1 });
    expect(spans('wide')).toEqual({ wide: 4, mid: 2 });
    expect(spans('full')).toEqual({ wide: 6, mid: 2 });
    expect(spans('wide').wide + spans('small').wide).toBe(6);
  });
});

describe('boardTitle', () => {
  it('is hers, or a plain word for none', () => {
    expect(boardTitle({ title: 'October spend', open: true, widgets: [] })).toBe('October spend');
    expect(boardTitle({ title: '  ', open: true, widgets: [] })).toBe('Board');
    expect(boardTitle(null)).toBe('Board');
  });
});

describe('toneOf', () => {
  it('keeps the tone she gave, else reads the sign of the delta', () => {
    expect(toneOf('down', '+12%')).toBe('down');
    expect(toneOf(undefined, '+12%')).toBe('up');
    expect(toneOf(undefined, '-3')).toBe('down');
    expect(toneOf(undefined, '−3')).toBe('down');
    expect(toneOf('loud', 'flat')).toBe('neutral');
  });
});

describe('parseNote', () => {
  it('splits paragraphs on blank lines and keeps line breaks within one', () => {
    expect(parseNote('One\ntwo\n\nThree')).toEqual([
      { kind: 'paragraph', lines: [[{ text: 'One', bold: false }], [{ text: 'two', bold: false }]] },
      { kind: 'paragraph', lines: [[{ text: 'Three', bold: false }]] },
    ]);
  });

  it('turns "- " lines into a list, between paragraphs', () => {
    const blocks = parseNote('Bring:\n- a blanket\n- **fruit**\nThat is all.');
    expect(blocks.map((b) => b.kind)).toEqual(['paragraph', 'list', 'paragraph']);
    expect(blocks[1]).toEqual({
      kind: 'list',
      items: [[{ text: 'a blanket', bold: false }], [{ text: 'fruit', bold: true }]],
    });
  });

  it('treats markup as text: nothing but bold means anything', () => {
    expect(parseNote('<b>hi</b> [x](y)')).toEqual([{ kind: 'paragraph', lines: [[{ text: '<b>hi</b> [x](y)', bold: false }]] }]);
    expect(parseNote('-not a bullet')[0]?.kind).toBe('paragraph');
    expect(parseNote('\n\n  \n')).toEqual([]);
  });
});

describe('runs', () => {
  it('finds bold parts and leaves a lone pair of stars as written', () => {
    expect(runs('a **b** c **d**')).toEqual([
      { text: 'a ', bold: false },
      { text: 'b', bold: true },
      { text: ' c ', bold: false },
      { text: 'd', bold: true },
    ]);
    expect(runs('5 ** 2')).toEqual([{ text: '5 ** 2', bold: false }]);
  });
});

describe('minutesOf', () => {
  it('reads HH:MM and nothing else', () => {
    expect(minutesOf('09:30')).toBe(570);
    expect(minutesOf('7:05')).toBe(425);
    expect(minutesOf('24:00')).toBeNull();
    expect(minutesOf('9.30')).toBeNull();
    expect(minutesOf(930)).toBeNull();
  });
});

describe('layoutTimeline', () => {
  it('runs from the hour before the first event to the hour after the last', () => {
    const t = layoutTimeline([{ start: '09:30', end: '10:15' }, { start: '12:00', end: '13:40' }]);
    expect(t.from).toBe(9 * 60);
    expect(t.to).toBe(14 * 60);
    expect(t.hours).toEqual([9, 10, 11, 12, 13]);
    expect(t.items[0]).toMatchObject({ index: 0, lane: 0, left: 10, width: 15 });
  });

  it('puts overlapping events in separate lanes and reuses a lane once it is free', () => {
    const t = layoutTimeline([
      { start: '09:00', end: '11:00' },
      { start: '10:00', end: '10:30' },
      { start: '11:00', end: '12:00' },
    ]);
    expect(t.items.map((i) => i.lane)).toEqual([0, 1, 0]);
    expect(t.lanes).toBe(2);
  });

  it('gives an event without an end half an hour, skips bad times, and thins the hour labels', () => {
    const t = layoutTimeline([{ start: '06:00' }, { start: 'noon' }, { start: '21:00', end: '20:00' }]);
    expect(t.items.map((i) => [i.index, i.end - i.start])).toEqual([
      [0, 30],
      [2, 30],
    ]);
    expect(t.hours.length).toBeLessThanOrEqual(7);
    expect(layoutTimeline([])).toEqual({ from: 0, to: 0, hours: [], lanes: 0, items: [] });
  });
});

describe('nearestIndex', () => {
  it('finds the closest x', () => {
    expect(nearestIndex([10, 20, 30], 24)).toBe(1);
    expect(nearestIndex([10, 20, 30], -5)).toBe(0);
    expect(nearestIndex([], 3)).toBe(-1);
  });
});

describe('chartSpecOf', () => {
  it('reads a chart widget, defaulting what is missing', () => {
    expect(chartSpecOf({ kind: 'bar', x: ['a', 'b'], series: [{ name: 'S', values: [1, 'x'] }], unit: '$' })).toEqual({
      kind: 'bar',
      x: ['a', 'b'],
      series: [{ name: 'S', values: [1, 0] }],
      unit: '$',
    });
    expect(chartSpecOf({ kind: 'pie' })).toEqual({ kind: 'line', x: [], series: [] });
  });
});

describe('chartSummary', () => {
  it('says what the chart shows', () => {
    const spec = chartSpecOf({ kind: 'line', x: ['Oct 1', 'Oct 31'], series: [{ name: 'Spend', values: [1, 2] }] });
    expect(chartSummary(spec, 'Daily spend')).toBe('Line chart, Daily spend: Spend, from Oct 1 to Oct 31.');
    expect(chartSummary(chartSpecOf({ kind: 'bar' }), '')).toBe('Bar chart: no data.');
  });
});
