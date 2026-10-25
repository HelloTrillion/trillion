// @vitest-environment jsdom
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { type Kit, NOTE_MS, RULES, blockName, createKit, isKit, widgetCaption } from '../src/kit';
import SAMPLES from '../src/kit/samples.json';
import { type Widget } from '../src/view';

const NOW = new Date(2026, 9, 25, 14, 5);
const widgets = SAMPLES.widgets as unknown as Widget[];
const blocks = SAMPLES.blocks as unknown as Widget[];

beforeAll(() => {
  // jsdom measures nothing; the charts only need something to observe with.
  globalThis.ResizeObserver ??= class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver;
});

let kit: Kit | null = null;
function open(opts: Parameters<typeof createKit>[0] = {}): Kit {
  kit = createKit({ now: () => NOW, ...opts });
  document.body.append(kit.el);
  return kit;
}

afterEach(() => {
  kit?.dispose();
  kit = null;
  vi.useRealTimers();
  vi.restoreAllMocks();
  document.body.replaceChildren();
  delete document.documentElement.dataset['theme'];
});

const section = (k: Kit, id: string): HTMLElement => {
  const el = k.el.querySelector<HTMLElement>(`[aria-labelledby="${id}"]`);
  if (!el) throw new Error(`no section ${id}`);
  return el;
};
const pick = (k: Kit, group: string, value: string): HTMLButtonElement => {
  const b = k.el.querySelector<HTMLButtonElement>(`[aria-label="${group}"] [data-value="${value}"]`);
  if (!b) throw new Error(`no ${group} ${value}`);
  return b;
};

describe('the route', () => {
  it('opens on #kit, alone or among others', () => {
    expect(isKit('#kit')).toBe(true);
    expect(isKit('#token=abc&kit')).toBe(true);
    expect(isKit('#kitchen')).toBe(false);
    expect(isKit('#showcase')).toBe(false);
    expect(isKit('')).toBe(false);
  });

  it('labels a block sample with its block', () => {
    expect(blockName({ id: 'b', type: 'custom', size: 'medium', title: 'x', blocks: [{ type: 'meter' }] })).toBe('meter');
    expect(blockName({ id: 'b', type: 'custom', size: 'medium', title: 'value' })).toBe('value');
  });
});

describe('the kit page', () => {
  it('draws every sample exactly once, each with its caption', () => {
    const k = open();
    const w = [...section(k, 'kit-widgets').querySelectorAll<HTMLElement>('.bd-card')];
    expect(w).toHaveLength(widgets.length);
    w.forEach((card, i) => {
      expect(card.dataset['type']).toBe(widgets[i]?.type);
      const pack = widgets[i]?.['pack'];
      expect(card.querySelector('.kit-cap')?.textContent).toBe([widgets[i]?.type, pack, widgets[i]?.size].filter(Boolean).join(' · '));
    });
    const b = [...section(k, 'kit-blocks').querySelectorAll<HTMLElement>('.bd-card')];
    expect(b).toHaveLength(blocks.length);
    expect(b.map((c) => c.querySelector('.kit-cap')?.textContent)).toEqual(blocks.map((x) => blockName(x)));
    // Nothing failed to draw.
    expect(k.el.textContent).not.toContain('This could not be shown.');
    // Each widget's own title shows once, in its own card.
    const titles = [...k.el.querySelectorAll('.bd-card')].map((c) => c.getAttribute('aria-label'));
    expect(titles).toEqual([...widgets, ...blocks].map((x) => x['title']));
  });

  it('captions a sample with its pack between its type and size, when it has one', () => {
    const pay: Widget = { id: 'p', type: 'transactions', size: 'medium', title: 'Payments', pack: 'money', blocks: [] };
    const stat: Widget = { id: 's', type: 'stat', size: 'small', title: 'Spend', blocks: [] };
    expect(widgetCaption(pay)).toBe('transactions · money · medium');
    expect(widgetCaption(stat)).toBe('stat · small');
    const k = open({ samples: { widgets: [pay, stat], blocks: [] } });
    expect([...section(k, 'kit-widgets').querySelectorAll('.kit-cap')].map((c) => c.textContent)).toEqual(['transactions · money · medium', 'stat · small']);
  });

  it('lays the cards out in the board grid, with their sizes', () => {
    const k = open();
    const grid = section(k, 'kit-widgets').querySelector<HTMLElement>('.bd-grid');
    expect(grid?.style.getPropertyValue('--rows-wide')).not.toBe('');
    const wide = section(k, 'kit-widgets').querySelector<HTMLElement>('.bd-card[data-type="mentions"]');
    expect(wide?.dataset['size']).toBe('wide');
  });

  it('lists the eight rules and the line under them', () => {
    const k = open();
    const items = [...section(k, 'kit-rules').querySelectorAll('li')].map((li) => li.textContent);
    expect(items).toEqual([...RULES]);
    expect(items).toHaveLength(8);
    expect(section(k, 'kit-rules').textContent).toContain('She picks the content; the system picks the emphasis.');
  });

  it('switches the theme on the root, and puts the app’s back on leaving', () => {
    document.documentElement.dataset['theme'] = 'dark';
    const k = open();
    expect(pick(k, 'Theme', 'dark').getAttribute('aria-pressed')).toBe('true');
    pick(k, 'Theme', 'light').click();
    expect(document.documentElement.dataset['theme']).toBe('light');
    expect(pick(k, 'Theme', 'light').getAttribute('aria-pressed')).toBe('true');
    expect(pick(k, 'Theme', 'dark').getAttribute('aria-pressed')).toBe('false');
    pick(k, 'Theme', 'dark').click();
    expect(document.documentElement.dataset['theme']).toBe('dark');
    pick(k, 'Theme', 'light').click();
    k.dispose();
    expect(document.documentElement.dataset['theme']).toBe('dark');
  });

  it('hands the theme to the app when it is given the means', () => {
    const setTheme = vi.fn();
    const restoreTheme = vi.fn();
    const k = open({ setTheme, restoreTheme });
    pick(k, 'Theme', 'light').click();
    expect(setTheme).toHaveBeenCalledWith('light');
    k.dispose();
    expect(restoreTheme).toHaveBeenCalledOnce();
  });

  it('slides the pill to the pressed side the moment it is clicked', () => {
    const k = open();
    for (const [group, values] of [
      ['Theme', ['light', 'dark']],
      ['Width', ['narrow', 'wide']],
    ] as const) {
      const sw = k.el.querySelector<HTMLElement>(`[aria-label="${group}"]`)!;
      for (const v of [...values, ...values]) {
        pick(k, group, v).click();
        const pressed = [...sw.querySelectorAll('button')].findIndex((b) => b.getAttribute('aria-pressed') === 'true');
        expect(pressed).toBe(values.indexOf(v as never));
        expect(sw.style.getPropertyValue('--at')).toBe(String(pressed));
      }
    }
  });

  it('keeps the pressed side looking pressed under the pointer', () => {
    // The pressed colour must not lose to the hover reset, which the just-clicked button is under.
    const css = readFileSync(resolve(__dirname, '../src/app.css'), 'utf8');
    const rules = [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)].map((m) => ({ sel: m[1]!.trim(), body: m[2]! }));
    const hovers = rules.filter((r) => r.sel.includes('.kit-switch-opt:hover'));
    expect(hovers.length).toBeGreaterThan(0);
    for (const r of hovers) expect(r.body).not.toMatch(/(^|[;\s])color:/);
  });

  it('switches between narrow and wide', () => {
    const k = open();
    expect(k.el.classList.contains('kit-narrow')).toBe(false);
    pick(k, 'Width', 'narrow').click();
    expect(k.el.classList.contains('kit-narrow')).toBe(true);
    expect(pick(k, 'Width', 'narrow').getAttribute('aria-pressed')).toBe('true');
    pick(k, 'Width', 'wide').click();
    expect(k.el.classList.contains('kit-narrow')).toBe(false);
  });

  it('sends nothing for an ask, and says for a moment what it would have asked', () => {
    vi.useFakeTimers();
    const sent = vi.spyOn(WebSocket.prototype, 'send');
    const fetched = vi.spyOn(globalThis, 'fetch').mockImplementation(() => Promise.reject(new Error('no')));
    const k = open();
    const ask = k.el.querySelector<HTMLButtonElement>('.bd-ask');
    expect(ask).not.toBeNull();
    const words = (widgets.find((w) => Array.isArray(w['asks']))?.['asks'] as string[])[0];
    ask!.click();
    const note = k.el.querySelector<HTMLElement>('.kit-note')!;
    expect(note.hidden).toBe(false);
    expect(note.textContent).toBe(`Would ask: ${words}`);
    expect(sent).not.toHaveBeenCalled();
    expect(fetched).not.toHaveBeenCalled();
    vi.advanceTimersByTime(NOTE_MS);
    expect(note.hidden).toBe(true);
  });

  it('stops every timer and leaves when disposed', () => {
    vi.useFakeTimers();
    const k = open();
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    k.el.querySelector<HTMLButtonElement>('.bd-ask')!.click();
    k.dispose();
    expect(vi.getTimerCount()).toBe(0);
    expect(k.el.isConnected).toBe(false);
  });
});
