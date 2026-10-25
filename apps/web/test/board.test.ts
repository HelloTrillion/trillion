// @vitest-environment jsdom
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { chartSpecOf } from '../src/blocks';
import { layoutChart } from '../src/chart';
import {
  TICK_MS,
  asksOf,
  boardTitle,
  buttonAction,
  chartHeight,
  createBoardView,
  hasChart,
  planRows,
  revive,
  rowTemplate,
  sourceWords,
  spans,
} from '../src/board';
import { type Board, type Widget } from '../src/view';

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

afterEach(() => {
  vi.useRealTimers();
  document.body.replaceChildren();
});

const board = (...widgets: Widget[]): Board => ({ title: 'Today', open: true, widgets });
const meeting: Widget = {
  id: 'w1',
  type: 'meeting',
  size: 'medium',
  title: 'Next meeting',
  blocks: [
    { type: 'time', label: 'Design review', at: '2026-10-25T14:30', end: '2026-10-25T15:00' },
    { type: 'people', names: ['Sam', 'Jo'] },
  ],
  source: { name: 'Calendar', at: '2026-10-25T14:03' },
  asks: ['Prep me for this', 'Move it to tomorrow'],
};

function mount(over: Partial<Parameters<typeof createBoardView>[0]> = {}, withAsks = true) {
  let now = NOW;
  const asked: string[] = [];
  const view = createBoardView({
    onRemove: () => {},
    onShowcase: () => {},
    ...(withAsks ? { onAsk: (text: string) => void asked.push(text) } : {}),
    now: () => now,
    ...over,
  });
  document.body.append(view.el);
  return { view, asked, setNow: (d: Date) => (now = d) };
}

describe('the board', () => {
  it('draws a card as its title, its blocks and a foot with its source and age', () => {
    const { view } = mount();
    view.draw(board(meeting));
    const card = view.el.querySelector<HTMLElement>('.bd-card');
    expect(card?.getAttribute('aria-label')).toBe('Next meeting');
    expect(card?.querySelector('.bd-card-title')?.textContent).toBe('Next meeting');
    // The blocks sit in the body, in a column that gives them the card's width.
    expect(card?.querySelector('.bd-card-body > .bk')).not.toBeNull();
    expect(card?.querySelector('.bk-time-when')?.textContent).toBe('in 25 min');
    expect(card?.querySelector('.bd-card-foot .bd-source')?.textContent).toBe('Calendar · 2 min ago');
    // The foot comes last: title, blocks, then where it came from.
    expect(card?.lastElementChild?.classList.contains('bd-card-foot')).toBe(true);
  });

  it('draws no source without one, and no foot at all with neither source nor asks', () => {
    const { view } = mount();
    view.draw(board({ ...meeting, source: undefined }, { id: 'w2', type: 'note', size: 'small', title: 'Bring', blocks: [{ type: 'text', text: 'Fruit' }] }));
    const [first, second] = [...view.el.querySelectorAll('.bd-card')];
    expect(first?.querySelector('.bd-source')).toBeNull();
    expect(first?.querySelectorAll('.bd-ask')).toHaveLength(2);
    expect(second?.querySelector('.bd-card-foot')).toBeNull();
  });

  it('sends exactly the words of an ask that is tapped', () => {
    const { view, asked } = mount();
    view.draw(board(meeting));
    const asks = [...view.el.querySelectorAll<HTMLButtonElement>('.bd-ask')];
    expect(asks.map((a) => a.textContent)).toEqual(['Prep me for this↗', 'Move it to tomorrow↗']);
    expect(asks[0]?.querySelector('.bd-ask-arrow')?.getAttribute('aria-hidden')).toBe('true');
    asks[0]?.click();
    expect(asked).toEqual(['Prep me for this']);
  });

  it('draws no asks when nothing would send them', () => {
    const { view } = mount({}, false);
    view.draw(board(meeting));
    expect(view.el.querySelector('.bd-ask')).toBeNull();
    expect(view.el.querySelector('.bd-source')?.textContent).toBe('Calendar · 2 min ago');
  });

  it('hides the asks while an approval waits, and shows them again after', () => {
    const strip = document.createElement('div');
    const { view } = mount({ approvals: strip });
    view.draw(board(meeting), { any: true, waiting: true, from: 'Trillion' });
    expect(view.el.classList.contains('bd-asks-off')).toBe(true);
    view.draw(board(meeting), { any: true, waiting: false, from: 'Trillion' });
    expect(view.el.classList.contains('bd-asks-off')).toBe(false);
    // Waiting in the conversation rather than on the board.
    view.draw(board(meeting), { any: false, waiting: false, from: 'Trillion', asksOff: true });
    expect(view.el.classList.contains('bd-asks-off')).toBe(true);
    view.draw(board(meeting));
    expect(view.el.classList.contains('bd-asks-off')).toBe(false);
  });

  it('brings every time and age up to date once a minute, with one timer for the board', () => {
    vi.useFakeTimers();
    const { view, setNow } = mount();
    view.draw(board(meeting, { ...meeting, id: 'w2' }));
    expect(vi.getTimerCount()).toBe(1);
    setNow(new Date(2026, 9, 25, 14, 20));
    vi.advanceTimersByTime(TICK_MS);
    expect([...view.el.querySelectorAll('.bk-time-when')].map((t) => t.textContent)).toEqual(['in 10 min', 'in 10 min']);
    expect([...view.el.querySelectorAll('.bd-source')].map((t) => t.textContent)).toEqual(['Calendar · 17 min ago', 'Calendar · 17 min ago']);
    // Drawn again, still one timer.
    view.draw(board(meeting));
    expect(vi.getTimerCount()).toBe(1);
  });

  it('stops its timer when it is put away', () => {
    vi.useFakeTimers();
    const { view } = mount();
    view.draw(board(meeting));
    expect(vi.getTimerCount()).toBe(1);
    view.dispose();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('draws a widget from before blocks with an empty body, and the rest of the board', () => {
    const { view } = mount();
    const old: Widget = { id: 'old', type: 'stat', size: 'small', title: 'Spend', value: '$1,240' };
    expect(() => view.draw(board(old, meeting))).not.toThrow();
    const cards = [...view.el.querySelectorAll('.bd-card')];
    expect(cards).toHaveLength(2);
    expect(cards[0]?.querySelector('.bd-card-body .bk')?.children).toHaveLength(0);
    expect(cards[0]?.querySelector('.bd-card-title')?.textContent).toBe('Spend');
    expect(cards[1]?.querySelector('.bk-time')).not.toBeNull();
  });

  it('keeps an unchanged card in place, and redraws a changed one', () => {
    const { view } = mount();
    view.draw(board(meeting));
    const before = view.el.querySelector('.bd-card');
    view.draw(board(meeting));
    expect(view.el.querySelector('.bd-card')).toBe(before);
    view.draw(board({ ...meeting, title: 'Later meeting' }));
    expect(view.el.querySelector('.bd-card')).not.toBe(before);
    expect(view.el.querySelector('.bd-card-title')?.textContent).toBe('Later meeting');
  });

  it('shows her words as text, never as elements', () => {
    const { view } = mount();
    const evil = '<img src=x onerror=alert(1)>';
    view.draw(board({ ...meeting, title: evil, source: { name: evil, at: '2026-10-25T14:03' }, asks: [evil] }));
    expect(view.el.querySelectorAll('img')).toHaveLength(0);
    expect(view.el.querySelector('.bd-card-title')?.textContent).toBe(evil);
  });
});

describe('sourceWords', () => {
  it('names the source and how old it is, or nothing without a name', () => {
    expect(sourceWords({ name: 'Calendar', at: '2026-10-25T14:03' }, NOW)).toBe('Calendar · 2 min ago');
    expect(sourceWords({ name: 'Inbox', at: 'whenever' }, NOW)).toBe('Inbox');
    expect(sourceWords({ name: ' ', at: '2026-10-25T14:03' }, NOW)).toBe('');
    expect(sourceWords(undefined, NOW)).toBe('');
    expect(sourceWords(['Calendar'], NOW)).toBe('');
  });
});

describe('asksOf', () => {
  it('keeps at most two asks, and only words', () => {
    expect(asksOf(['Prep me for this', 3, ' ', 'Move it', 'Cancel it'])).toEqual(['Prep me for this', 'Move it']);
    expect(asksOf('Prep me')).toEqual([]);
    expect(asksOf(undefined)).toEqual([]);
  });
});

describe('hasChart', () => {
  it('finds a line, bar or area chart at the top or in a column, and not a donut', () => {
    expect(hasChart([{ type: 'value' }, { type: 'chart', kind: 'bar' }])).toBe(true);
    expect(hasChart([{ type: 'columns', columns: [[{ type: 'chart', kind: 'line' }], []] }])).toBe(true);
    expect(hasChart([{ type: 'chart', kind: 'donut' }])).toBe(false);
    expect(hasChart(undefined)).toBe(false);
  });

  it('lets a row whose widget holds a chart block take the spare height', () => {
    expect(planRows([{ size: 'wide', type: 'spend', blocks: [{ type: 'chart', kind: 'area' }] }], 6).rows).toEqual(['grow']);
    expect(planRows([{ size: 'wide', type: 'spend', blocks: [{ type: 'value' }] }], 6).rows).toEqual(['fit']);
  });
});

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

describe('chartHeight', () => {
  it('gives every size a fixed height that the whole drawing fits inside', () => {
    const spec = chartSpecOf({
      kind: 'area',
      x: Array.from({ length: 31 }, (_, i) => `Oct ${i + 1}`),
      series: [{ name: 'Spend', values: Array.from({ length: 31 }, (_, i) => (i % 7) * 3.5) }],
      unit: '$',
    });
    for (const size of ['small', 'medium', 'wide', 'full'] as const) {
      const height = chartHeight(size);
      expect(height).toBeGreaterThanOrEqual(150);
      expect(height).toBeLessThanOrEqual(240);
      const L = layoutChart(spec, 600, height);
      expect(L.plot.top).toBeGreaterThanOrEqual(0);
      expect(L.plot.bottom).toBeLessThanOrEqual(height);
      for (const t of L.yTicks) expect(t.y).toBeLessThanOrEqual(height);
      for (const p of L.series[0]?.points ?? []) expect(p.y).toBeLessThanOrEqual(L.plot.bottom + 0.01);
      // The x labels sit on a baseline 5px above the bottom: room for them under the plot.
      expect(height - 5 - L.plot.bottom).toBeGreaterThanOrEqual(11);
    }
  });
});

describe('planRows', () => {
  const w = (size: 'small' | 'medium' | 'wide' | 'full', type = 'list') => ({ size, type });

  it('lets a widget alone fill the board', () => {
    expect(planRows([w('small')], 6).spans).toEqual([6]);
    expect(planRows([w('medium')], 2).spans).toEqual([2]);
  });

  it('keeps rows that already add up, and widens the last card of a row that does not', () => {
    expect(planRows([w('medium'), w('medium')], 6).spans).toEqual([3, 3]);
    expect(planRows([w('medium'), w('small')], 6).spans).toEqual([3, 3]);
    expect(planRows([w('small'), w('small'), w('small')], 6).spans).toEqual([2, 2, 2]);
    // A wide card does not fit beside a medium one: the medium widens, the wide starts a row and fills it.
    expect(planRows([w('medium'), w('wide'), w('small')], 6).spans).toEqual([6, 4, 2]);
  });

  it('lets only rows with a chart take the spare height', () => {
    const plan = planRows([w('small', 'stat'), w('small', 'stat'), w('small', 'stat'), w('wide', 'chart'), w('small', 'table')], 6);
    expect(plan.spans).toEqual([2, 2, 2, 4, 2]);
    expect(plan.rows).toEqual(['fit', 'grow']);
    expect(rowTemplate(plan.rows)).toBe('auto minmax(min-content, 1fr)');
  });

  it('puts every widget in its own row in one column', () => {
    const plan = planRows([w('full', 'chart'), w('small', 'note')], 1);
    expect(plan.spans).toEqual([1, 1]);
    expect(plan.rows).toEqual(['grow', 'fit']);
    expect(rowTemplate([])).toBe('auto');
  });
});

describe('buttonAction', () => {
  const widget = { id: 'w1', type: 'note', size: 'medium' as const };
  it('shows the empty state for no board or an empty one, and reopens only a closed board with widgets', () => {
    expect(buttonAction(null)).toBe('empty');
    expect(buttonAction({ title: 'T', open: false, widgets: [] })).toBe('empty');
    expect(buttonAction({ title: 'T', open: false, widgets: [widget] })).toBe('reopen');
    expect(buttonAction({ title: 'T', open: true, widgets: [widget] })).toBe('show');
  });
});

describe('revive', () => {
  it('undims a card whose removal never came back and gives its × back', () => {
    const attrs = new Set(['data-leaving']);
    const x = { disabled: true };
    const card = {
      removeAttribute: (n: string) => void attrs.delete(n),
      querySelector: (q: string) => (q === '.bd-x' ? x : null),
    };
    revive(card as unknown as Parameters<typeof revive>[0]);
    expect(attrs.has('data-leaving')).toBe(false);
    expect(x.disabled).toBe(false);
  });
});
