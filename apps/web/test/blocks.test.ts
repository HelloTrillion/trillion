// @vitest-environment jsdom
import { beforeAll, describe, expect, it } from 'vitest';
import { BLOCK_TYPES, deltaTone, drawBlocks, heroIndex, initials, peopleWords, percentOf, tintOf, toneClass } from '../src/blocks';

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
      { now: NOW },
    );
    const el = drawn.el;
    expect(el.querySelector('.bd-plot')?.getAttribute('aria-label')).toContain('Bar chart');
    expect(texts(el, '.bd-legend-name')).toEqual(['Rent', 'Food']);
    expect(texts(el, 'th')).toEqual(['Option', 'Total']);
    expect(texts(el, 'tr.bk-hl td')).toEqual(['Maple Park', '31']);
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
    expect(texts(el, '.bk-status-text')).toEqual(['Payment failed']);
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
