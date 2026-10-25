// @vitest-environment jsdom
import { beforeAll, describe, expect, it, vi } from 'vitest';
import {
  BLOCK_TYPES,
  chartSpecOf,
  chartSummary,
  deltaTone,
  drawBlocks,
  heroIndex,
  initials,
  layoutTimeline,
  minutesOf,
  nearestIndex,
  parseNote,
  peopleWords,
  percentOf,
  pointWords,
  runs,
  tintOf,
  toneClass,
} from '../src/blocks';

// Sunday 25 October 2026, 2:05 pm, local.
const NOW = new Date(2026, 9, 25, 14, 5);

beforeAll(() => {
  // jsdom measures nothing; the charts only need something to observe with.
  globalThis.ResizeObserver ??= class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver;
});

const draw = (...blocks: unknown[]): HTMLElement => drawBlocks(blocks, { now: NOW }).el;
const texts = (el: Element, sel: string): string[] => [...el.querySelectorAll(sel)].map((e) => e.textContent ?? '');

describe('initials', () => {
  it('takes the first letter of up to two words', () => {
    expect(initials('Sam')).toBe('S');
    expect(initials('Jo Lee')).toBe('JL');
    expect(initials('maple park team')).toBe('MP');
    expect(initials('  ')).toBe('');
    expect(initials('(Alex) Kim')).toBe('AK');
  });
});

describe('tintOf', () => {
  it('is one of five, and the same for the same name however it is written', () => {
    for (const n of ['Sam', 'Jo', 'Alex', 'Maple Park', '']) {
      const t = tintOf(n);
      expect(t).toBeGreaterThanOrEqual(0);
      expect(t).toBeLessThan(5);
      expect(Number.isInteger(t)).toBe(true);
    }
    expect(tintOf('Sam')).toBe(tintOf(' sam '));
    // Not everyone the same colour.
    expect(new Set(['Sam', 'Jo', 'Alex', 'Lee', 'Kim', 'Pat', 'Ray'].map(tintOf)).size).toBeGreaterThan(1);
  });
});

describe('heroIndex', () => {
  it('is the first top-level value only', () => {
    expect(heroIndex([{ type: 'text' }, { type: 'value' }, { type: 'value' }])).toBe(1);
    expect(heroIndex([{ type: 'columns', columns: [[{ type: 'value' }], []] }])).toBe(-1);
    expect(heroIndex([])).toBe(-1);
  });
});

describe('toneClass and deltaTone', () => {
  it('maps tones, and the old up and down, to a class', () => {
    expect(toneClass('good')).toBe('bk-good');
    expect(toneClass('up')).toBe('bk-good');
    expect(toneClass('down')).toBe('bk-bad');
    expect(toneClass('attention')).toBe('bk-attention');
    expect(toneClass('loud')).toBe('bk-neutral');
    expect(toneClass(undefined)).toBe('bk-neutral');
  });

  it('reads a delta by its sign unless a tone is given', () => {
    expect(deltaTone(undefined, '+12%')).toBe('good');
    expect(deltaTone(undefined, '−3')).toBe('bad');
    expect(deltaTone('bad', '+12%')).toBe('bad');
    expect(deltaTone(undefined, 'flat')).toBe('neutral');
  });
});

describe('peopleWords and percentOf', () => {
  it('names a few and counts the rest', () => {
    expect(peopleWords(['Sam'])).toBe('Sam');
    expect(peopleWords(['Sam', 'Jo'])).toBe('Sam and Jo');
    expect(peopleWords(['Sam', 'Jo', 'Alex'])).toBe('Sam, Jo and Alex');
    expect(peopleWords(['Sam', 'Jo', 'Alex', 'Lee'])).toBe('Sam, Jo and 2 more');
  });

  it('clamps a meter to its range', () => {
    expect(percentOf(3, 5)).toBe(60);
    expect(percentOf(9, 5)).toBe(100);
    expect(percentOf(-1, 5)).toBe(0);
    expect(percentOf(1, 0)).toBe(0);
  });
});

describe('drawBlocks', () => {
  it('draws only the first top-level value as the hero', () => {
    const el = draw(
      { type: 'value', value: '$4,200', label: 'Unpaid', delta: '+12%', hint: '3 invoices' },
      { type: 'value', value: '7' },
      { type: 'columns', columns: [[{ type: 'value', value: 'A' }], [{ type: 'value', value: 'B' }]] },
    );
    expect(texts(el, '.bk-hero .bk-num')).toEqual(['$4,200']);
    expect(el.querySelectorAll('.bk-value')).toHaveLength(4);
    expect(el.querySelectorAll('.bk-hero')).toHaveLength(1);
    expect(texts(el, '.bk-hero .bk-label')).toEqual(['Unpaid']);
    const delta = el.querySelector('.bk-delta');
    expect(delta?.textContent).toBe('+12%');
    expect(delta?.classList.contains('bk-good')).toBe(true);
    expect(texts(el, '.bk-hero .bk-meta')).toEqual(['3 invoices']);
  });

  it('draws a sparkline with its trend in words', () => {
    const el = draw({ type: 'sparkline', values: [1, 4, 2, 8], tone: 'good' });
    const spark = el.querySelector('.bk-spark');
    expect(spark?.getAttribute('aria-label')).toBe('Trend from 1 to 8, low 1, high 8');
    expect(spark?.querySelector('.bk-spark-line')?.getAttribute('d')).toBeTruthy();
  });

  it('draws a bar meter and a ring with its percent', () => {
    const el = draw({ type: 'meter', value: 2, max: 5, label: '2 of 5 done' }, { type: 'meter', value: 12, max: 10, shape: 'ring', label: 'Saved' });
    const [bar, ring] = [...el.querySelectorAll<HTMLElement>('.bk-meter')];
    expect(bar?.querySelector('.bk-meter-label')?.textContent).toBe('2 of 5 done');
    expect(bar?.querySelector('.bk-meter-num')?.textContent).toBe('40%');
    expect(bar?.querySelector<HTMLElement>('.bk-meter-fill')?.style.getPropertyValue('--v')).toBe('0.4');
    expect(ring?.querySelector('.bk-ring-num')?.textContent).toBe('100%');
    expect(ring?.getAttribute('aria-valuenow')).toBe('10');
    expect(ring?.querySelector('.bk-ring-fill')?.getAttribute('stroke-dasharray')).toBe('100 100');
  });

  it('names a meter without a label, and draws no fill for an empty ring', () => {
    const el = draw({ type: 'meter', value: 1, max: 4 }, { type: 'meter', value: 0, max: 10, shape: 'ring' });
    const [bar, ring] = [...el.querySelectorAll<HTMLElement>('.bk-meter')];
    expect(bar?.getAttribute('aria-label')).toBe('Progress');
    expect(ring?.querySelector('.bk-ring-num')?.textContent).toBe('0%');
    expect(ring?.querySelector('.bk-ring-fill')).toBeNull();
    expect(ring?.querySelector('.bk-ring-track')).not.toBeNull();
  });

  it('draws a time as its label, a countdown and its range', () => {
    const el = draw({ type: 'time', label: 'Design review', at: '2026-10-25T14:30', end: '2026-10-25T15:00' });
    expect(texts(el, '.bk-time-label')).toEqual(['Design review']);
    expect(texts(el, '.bk-time-when')).toEqual(['in 25 min']);
    expect(texts(el, '.bk-time-range')).toEqual(['2:30–3:00 pm']);
    expect(el.querySelector('.bk-time-when')?.getAttribute('data-at')).toBe('2026-10-25T14:30');
  });

  it('draws a chart, a donut, a table with its highlight and a timeline', () => {
    const drawn = drawBlocks(
      [
        { type: 'chart', kind: 'bar', x: ['Mon', 'Tue'], series: [{ name: 'Calls', values: [3, 5] }] },
        { type: 'chart', kind: 'donut', x: ['Rent', 'Food'], series: [{ name: 'Spend', values: [1200, 400] }] },
        { type: 'table', columns: ['Option', 'Total'], rows: [['Maple Park', 31], ['Riverside', 27]], highlight: 0 },
        { type: 'timeline', events: [{ title: 'Standup', start: '09:00', end: '09:15', meta: 'Room 2' }] },
      ],
      { now: NOW, title: 'Calls this week' },
    );
    const el = drawn.el;
    expect(el.querySelector('.bd-plot')?.getAttribute('aria-label')).toContain('Bar chart, Calls this week');
    expect(texts(el, '.bd-legend-name')).toEqual(['Rent', 'Food']);
    expect(texts(el, 'th')).toEqual(['Option', 'Total']);
    // Her pick is tinted, and said in words rather than as the page's current item.
    expect(texts(el, 'tr.bk-hl td')).toEqual(['Maple Park (her pick)', '31']);
    expect(el.querySelector('tr.bk-hl td .visually-hidden')?.textContent).toBe(' (her pick)');
    expect(el.querySelector('[aria-current]')).toBeNull();
    expect(texts(el, '.bd-agenda .bd-when')).toEqual(['09:00–09:15']);
    expect(texts(el, '.bd-agenda .bd-sub')).toEqual(['Room 2']);
    drawn.dispose();
  });

  it('draws rows with a lead, one trail and done', () => {
    const el = draw({
      type: 'rows',
      label: 'Today',
      items: [
        { title: 'Call back', person: 'Sam', detail: 'About the lease', when: '2026-10-25T15:05', meta: 'ignored', chip: 'ignored' },
        { title: 'Invoice 112', status: 'attention', meta: '$1,200', chip: 'ignored' },
        { title: 'Book room', chip: 'Maple Park' },
        { title: 'Send notes', done: true },
        { title: 'Plan trip', done: false },
      ],
    });
    expect(texts(el, '.bk-rows-block > .bk-label')).toEqual(['Today']);
    const rows = [...el.querySelectorAll('.bk-row')];
    expect(rows).toHaveLength(5);
    expect(rows[0]?.querySelector('.bk-avatar')?.textContent).toBe('S');
    expect(rows[0]?.querySelector('.bk-row-detail')?.textContent).toBe('Sam · About the lease');
    expect(texts(rows[0]!, '.bk-row-trail')).toEqual(['in 1 h']);
    expect(rows[1]?.querySelector('.bk-dot')?.classList.contains('bk-attention')).toBe(true);
    expect(texts(rows[1]!, '.bk-row-trail')).toEqual(['$1,200']);
    expect(texts(rows[2]!, '.bk-row-chip')).toEqual(['Maple Park']);
    expect(rows[3]?.hasAttribute('data-done')).toBe(true);
    expect(rows[3]?.querySelector('.bk-row-title')?.textContent).toBe('Done: Send notes');
    expect(rows[4]?.hasAttribute('data-done')).toBe(false);
    expect(rows[4]?.querySelector('.bk-check')).not.toBeNull();
    for (const r of rows) expect(r.querySelectorAll('.bk-row-trail, .bk-row-chip').length).toBeLessThanOrEqual(1);
  });

  it('draws a person, people, a status and chips', () => {
    const el = draw(
      { type: 'person', name: 'Jo Lee', detail: 'Re: the lease' },
      { type: 'people', names: ['Sam', 'Jo', 'Alex', 'Lee', 'Kim', 'Pat'] },
      { type: 'status', tone: 'bad', text: 'Payment failed' },
      { type: 'chips', items: ['Maple Park room 2', 'Hybrid'], tone: 'neutral' },
    );
    expect(texts(el, '.bk-person-name')).toEqual(['Jo Lee']);
    expect(texts(el, '.bk-person-detail')).toEqual(['Re: the lease']);
    expect(el.querySelector('.bk-person .bk-avatar')?.textContent).toBe('JL');
    expect(el.querySelectorAll('.bk-stack .bk-avatar')).toHaveLength(4);
    expect(texts(el, '.bk-people-names')).toEqual(['Sam, Jo and 4 more']);
    expect(el.querySelector('.bk-status')?.classList.contains('bk-bad')).toBe(true);
    // The dot's colour in words, as rows say it.
    expect(texts(el, '.bk-status-text')).toEqual(['Problem: Payment failed']);
    expect(el.querySelector('.bk-status-text .visually-hidden')?.textContent).toBe('Problem: ');
    expect(texts(el, '.bk-chips .bk-chip')).toEqual(['Maple Park room 2', 'Hybrid']);
  });

  it('draws text with its markup, and a tone as a callout', () => {
    const el = draw({ type: 'text', text: 'Bring:\n- a **blanket**\n- fruit' }, { type: 'text', text: 'Rent is due Friday', tone: 'attention' });
    const [plain, callout] = [...el.querySelectorAll('.bk-text')];
    expect(texts(plain!, 'li')).toEqual(['a blanket', 'fruit']);
    expect(texts(plain!, 'strong')).toEqual(['blanket']);
    expect(plain?.classList.contains('bk-callout')).toBe(false);
    expect(callout?.classList.contains('bk-callout')).toBe(true);
    expect(callout?.classList.contains('bk-attention')).toBe(true);
  });

  it('draws a quote, facts and columns', () => {
    const el = draw(
      { type: 'quote', text: 'Ship it Friday.', by: 'Alex', when: '2026-10-25T12:05' },
      { type: 'facts', items: [{ label: 'Due', value: 'Fri 30 Oct' }, { label: 'Owner', value: 'Sam' }] },
      { type: 'columns', columns: [[{ type: 'status', tone: 'good', text: 'Up' }], [{ type: 'chips', items: ['A'] }], [{ type: 'columns', columns: [[], []] }]] },
    );
    expect(texts(el, '.bk-quote-text')).toEqual(['Ship it Friday.']);
    expect(el.querySelector('.bk-quote figcaption')?.textContent).toBe('Alex · 2 h ago');
    expect(texts(el, '.bk-fact dt')).toEqual(['Due', 'Owner']);
    expect(texts(el, '.bk-fact dd')).toEqual(['Fri 30 Oct', 'Sam']);
    const cols = el.querySelector<HTMLElement>('.bk-columns');
    expect(cols?.querySelectorAll(':scope > .bk-col')).toHaveLength(3);
    expect(cols?.style.getPropertyValue('--cols')).toBe('3');
    // Columns never nest.
    expect(cols?.querySelectorAll('.bk-columns')).toHaveLength(0);
  });

  it('skips a block it does not know, and draws the rest', () => {
    const el = draw({ type: 'hologram', text: 'from a newer server' }, 'junk', null, { type: 'status', tone: 'good', text: 'Fine' }, { type: 'toString' });
    expect(el.children).toHaveLength(1);
    expect((el.children[0] as HTMLElement).dataset['block']).toBe('status');
  });

  it('says which block could not be drawn, and draws the rest', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    // A value whose label throws when read.
    const bad = { type: 'value', get label(): string { throw new Error('boom'); } };
    const el = draw(bad, { type: 'status', text: 'Fine' });
    expect(el.children).toHaveLength(1);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('value'), expect.any(Error));
    warn.mockRestore();
  });

  it('shows markup as text, never as elements', () => {
    const evil = '<script>alert(1)</script><img src=x onerror=alert(2)>';
    const el = draw(
      { type: 'value', value: evil, label: evil },
      { type: 'text', text: evil },
      { type: 'rows', items: [{ title: evil, detail: evil, person: evil }] },
      { type: 'chips', items: [evil] },
      { type: 'quote', text: evil, by: evil },
    );
    expect(el.querySelectorAll('script, img')).toHaveLength(0);
    expect(el.querySelector('.bk-num')?.textContent).toBe(evil);
    expect(el.querySelector('.bk-text')?.textContent).toBe(evil);
    expect(el.textContent).toContain('<script>');
  });

  it('brings its times up to date on tick', () => {
    const drawn = drawBlocks(
      [
        { type: 'time', label: 'Design review', at: '2026-10-25T14:30', end: '2026-10-25T15:00' },
        { type: 'rows', items: [{ title: 'Call Sam', when: '2026-10-25T14:15' }] },
      ],
      { now: NOW },
    );
    const when = drawn.el.querySelector('.bk-time-when');
    const trail = drawn.el.querySelector('.bk-row-trail');
    expect(when?.textContent).toBe('in 25 min');
    expect(trail?.textContent).toBe('in 10 min');
    drawn.tick(new Date(2026, 9, 25, 14, 20));
    expect(when?.textContent).toBe('in 10 min');
    expect(trail?.textContent).toBe('5 min ago');
    drawn.tick(new Date(2026, 9, 25, 14, 35));
    expect(when?.textContent).toBe('started 5 min ago');
    drawn.tick(new Date(2026, 9, 25, 15, 10));
    expect(when?.textContent).toBe('ended 10 min ago');
  });

  it('knows every block in the spec', () => {
    expect([...BLOCK_TYPES].sort()).toEqual(
      ['value', 'sparkline', 'meter', 'time', 'chart', 'table', 'timeline', 'rows', 'person', 'people', 'status', 'chips', 'text', 'quote', 'facts', 'columns'].sort(),
    );
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
    expect(chartSummary(spec, 'Daily spend')).toBe('Line chart, Daily spend, Oct 1 to Oct 31. Spend: low 1, high 2, last 2.');
    expect(chartSummary(chartSpecOf({ kind: 'bar' }), '')).toBe('Bar chart: no data.');
    const two = chartSpecOf({ kind: 'bar', x: ['Q1', 'Q2', 'Q3'], series: [{ name: 'A', values: [5, 1, 3] }, { name: 'B', values: [2, 8, 4] }], unit: '$' });
    expect(chartSummary(two, '')).toBe('Bar chart, Q1 to Q3. A: low $1, high $5, last $3. B: low $2, high $8, last $4.');
  });

  it('says one point in words for the arrow keys', () => {
    const spec = chartSpecOf({ kind: 'line', x: ['Mon', 'Tue'], series: [{ name: 'Steps', values: [4200, 5100] }] });
    expect(pointWords(spec, 1)).toBe('Tue: Steps 5.1k');
  });
});
