// @vitest-environment jsdom
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import type { SupernovaTier } from '@hellotrillion/galaxy';
import { Celebrations, WAIT_MS, celebrationOf, createCelebrationsSettings, saveCelebrations, savedCelebrations } from '../src/supernova';
import type { ServerMsg } from '../src/view';

const notice = (extra: Record<string, unknown> = {}): ServerMsg => ({
  type: 'notice', code: 'schedule', id: 'n1', kind: 'ready', title: 'Done', text: 'Done.', ...extra,
});

function setup(on = true) {
  const fired: SupernovaTier[] = [];
  let now = 1000;
  let enabled = on;
  const c = new Celebrations((t) => fired.push(t), () => enabled, () => now);
  return {
    c,
    fired,
    advance: (ms: number) => { now += ms; },
    turn: (v: boolean) => { enabled = v; },
  };
}

afterEach(() => {
  localStorage.clear();
  document.body.replaceChildren();
});

describe('reading a celebration off a message (GAL-9)', () => {
  it('only from a scheduled notice, only a known tier', () => {
    expect(celebrationOf(notice({ celebrate: 'large' }))).toBe('large');
    expect(celebrationOf(notice({ celebrate: null }))).toBeNull();
    expect(celebrationOf(notice())).toBeNull();
    expect(celebrationOf(notice({ celebrate: 'enormous' }))).toBeNull();
    // Anything else that says it is one is not: a reply, a tool's card, another notice.
    expect(celebrationOf({ type: 'reply.sentence', celebrate: 'large' })).toBeNull();
    expect(celebrationOf({ type: 'confirm.request', celebrate: 'large' })).toBeNull();
    expect(celebrationOf({ type: 'notice', code: 'compacted', celebrate: 'large' })).toBeNull();
  });
});

describe('firing it', () => {
  it('fires at once on a free page', () => {
    const { c, fired } = setup();
    c.take(notice({ celebrate: 'medium' }), false);
    expect(fired).toEqual(['medium']);
    c.take(notice(), false);
    expect(fired).toEqual(['medium']); // once
  });

  it('never over a live reply: it waits for the end (VOI-17)', () => {
    const { c, fired } = setup();
    c.take(notice({ celebrate: 'small' }), true);
    c.take({ type: 'reply.sentence' }, true);
    expect(fired).toEqual([]);
    c.settle(false);
    expect(fired).toEqual(['small']);
  });

  it('two waiting behind one reply are one, the larger', () => {
    const { c, fired } = setup();
    c.take(notice({ celebrate: 'small' }), true);
    c.take(notice({ id: 'n2', celebrate: 'large' }), true);
    c.take(notice({ id: 'n3', celebrate: 'medium' }), true);
    c.settle(false);
    expect(fired).toEqual(['large']);
  });

  it('one that waited too long is dropped', () => {
    const { c, fired, advance } = setup();
    c.take(notice({ celebrate: 'large' }), true);
    advance(WAIT_MS + 1);
    c.settle(false);
    expect(fired).toEqual([]);
  });

  it('turned off, never fires, even one already waiting', () => {
    const off = setup(false);
    off.c.take(notice({ celebrate: 'large' }), false);
    expect(off.fired).toEqual([]);
    const later = setup();
    later.c.take(notice({ celebrate: 'large' }), true);
    later.turn(false);
    later.c.settle(false);
    expect(later.fired).toEqual([]);
  });
});

describe('the Celebrations setting', () => {
  it('is on until turned off, and kept in this browser', () => {
    expect(savedCelebrations()).toBe(true);
    saveCelebrations(false);
    expect(savedCelebrations()).toBe(false);
    saveCelebrations(true);
    expect(savedCelebrations()).toBe(true);
  });

  it('is a switch in its own Settings section', () => {
    const h = Object.assign(document.createElement('div'), { open: false, showModal() {}, close() {} });
    document.body.append(h);
    let changed = 0;
    const c = createCelebrationsSettings(h, () => { changed++; });
    c.open();
    const box = h.querySelector<HTMLInputElement>('#celebrations-on');
    expect(box?.getAttribute('role')).toBe('switch');
    expect(box?.checked).toBe(true);
    expect(c.summary()).toBe('On');
    expect(h.textContent).toContain('Never a sound');
    box!.checked = false;
    box!.dispatchEvent(new Event('change'));
    expect(savedCelebrations()).toBe(false);
    expect(c.summary()).toBe('Off');
    expect(changed).toBe(1);
  });
});

describe('by construction', () => {
  it('the page asks the galaxy for one in one place: the celebrations feature', () => {
    const src = resolve(__dirname, '../src');
    const files = [...readdirSync(src).map((f) => resolve(src, f)), ...readdirSync(resolve(src, 'features')).map((f) => resolve(src, 'features', f))].filter((f) => f.endsWith('.ts'));
    const callers = files.filter((f) => /\.supernova\(/.test(readFileSync(f, 'utf8')));
    expect(callers.map((f) => f.slice(src.length + 1).replace(/\\/g, '/'))).toEqual(['features/celebrations.ts']);
    const feature = readFileSync(resolve(src, 'features/celebrations.ts'), 'utf8');
    expect(feature.match(/\.supernova\(/g)).toHaveLength(1);
    expect(feature).toContain('new Celebrations((tier) => galaxy.supernova(tier), savedCelebrations)');
  });
});

describe('the celebrations feature', () => {
  it('fires the notice’s tier once the page has taken it in, never over a live reply', async () => {
    const { feature } = await import('../src/features/celebrations');
    const fired: SupernovaTier[] = [];
    let replying = true;
    const handlers: ((m: ServerMsg) => void)[] = [];
    const host = Object.assign(document.createElement('div'), { open: false, showModal() {}, close() {} });
    const added: string[] = [];
    feature.setup({
      panel: { host: () => host, add: (s: { id: string }) => void added.push(s.id) } as never,
      api: async () => new Response('{}'),
      send: () => true,
      refresh: () => undefined,
      openConversation: () => undefined,
      on: (h) => void handlers.push(h),
      galaxy: { supernova: (t) => void fired.push(t) },
      replying: () => replying,
    });
    expect(added).toEqual(['celebrations']);
    const tick = () => new Promise((r) => setTimeout(r, 5));
    handlers.forEach((h) => h(notice({ celebrate: 'large' })));
    await tick();
    expect(fired).toEqual([]);
    replying = false;
    handlers.forEach((h) => h({ type: 'reply.done' }));
    await tick();
    expect(fired).toEqual(['large']);
  });
});
