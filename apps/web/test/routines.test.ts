// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import type { Host } from '../src/panel';
import { createReminders, landsText, routineNextText, type RemindersState, type Routine } from '../src/reminders';

const flush = (): Promise<void> => new Promise((r) => setTimeout(r, 0));

const STATE: RemindersState = { active: [], recent: [], checked_ms: 1_000_000, tick_s: 15, now_ms: 1_005_000 };

const revenue: Routine = {
  id: 'b1b2c3d4',
  name: 'Monday revenue',
  when: 'Every Mon at 8:00 am',
  next_ms: 1,
  next: 'Mon 26 Oct, 8:00 am',
  target: 'own',
  conversation_id: 'r1',
  paused: false,
  notes: '',
};
const myDay: Routine = { ...revenue, id: 'c1c2c3d4', name: 'Build my day', when: 'Every weekday at 8:00 am', target: 'day', conversation_id: null };

function fakeHost(root: HTMLElement): Host & { closes: number } {
  const listeners: (() => void)[] = [];
  const host = {
    open: false,
    closes: 0,
    showModal() {
      host.open = true;
    },
    close() {
      host.open = false;
      host.closes++;
      for (const l of listeners) l();
    },
    replaceChildren: (...nodes: (Node | string)[]) => root.replaceChildren(...nodes),
    setAttribute: (name: string, value: string) => root.setAttribute(name, value),
    addEventListener: (_: 'close', l: () => void) => listeners.push(l),
  };
  return host;
}

const json = (body: unknown, status = 200): Response => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

function setup(start: Routine[]) {
  const root = document.createElement('div');
  document.body.append(root);
  let routines = start;
  const calls: string[] = [];
  const opened: string[] = [];
  const host = fakeHost(root);
  const api = (method: string, path: string): Promise<Response> => {
    calls.push(`${method} ${path}`);
    if (path === '/api/schedules') return Promise.resolve(json(STATE));
    if (path === '/api/routines') return Promise.resolve(json({ routines }));
    const m = /^\/api\/routines\/([a-f0-9]+)(?:\/(pause|resume))?$/.exec(path);
    if (!m) return Promise.resolve(json({ error: 'not_found' }, 404));
    const [, id, what] = m;
    if (method === 'DELETE') routines = routines.filter((r) => r.id !== id);
    else routines = routines.map((r) => (r.id === id ? { ...r, paused: what === 'pause' } : r));
    return Promise.resolve(json({ routines }));
  };
  const section = createReminders(host, api, () => undefined, (id) => opened.push(id));
  const card = (name: string): HTMLElement =>
    [...root.querySelectorAll<HTMLElement>('.reminders-card')].find((c) => c.querySelector('strong')?.textContent === name)!;
  const press = (within: HTMLElement, text: string): void =>
    [...within.querySelectorAll('button')].find((b) => b.textContent === text)!.click();
  return { root, host, calls, opened, section, card, press };
}

afterEach(() => {
  document.body.replaceChildren();
});

describe('routines in words', () => {
  it('says its next run, or that it is paused, and where it lands', () => {
    expect(routineNextText(revenue)).toBe('Next: Mon 26 Oct, 8:00 am');
    expect(routineNextText({ ...revenue, paused: true })).toBe('Paused');
    expect(landsText(myDay)).toBe('Your day');
    expect(landsText(revenue)).toBe('Its own conversation');
    expect(landsText({ ...revenue, conversation_id: null })).toBe('Its own conversation, made on its first run');
  });
});

describe('the Routines group', () => {
  it('lists each routine with when it runs, its next run and where it lands', async () => {
    const { root, host, card, section } = setup([revenue, myDay]);
    await section.open();
    expect(root.querySelector('h3[data-focus="routines"]')?.textContent).toBe('Routines');
    const own = card('Monday revenue');
    expect(own.textContent).toContain('Every Mon at 8:00 am');
    expect(own.textContent).toContain('Next: Mon 26 Oct, 8:00 am');
    expect(own.querySelector('.reminders-open')?.textContent).toBe('Open');
    const day = card('Build my day');
    expect(day.textContent).toContain('Your day');
    expect(day.querySelector('.reminders-open')).toBeNull();
    host.close();
  });

  it('opens its conversation, closing Settings first', async () => {
    const { host, card, opened, section } = setup([revenue]);
    await section.open();
    card('Monday revenue').querySelector<HTMLButtonElement>('.reminders-open')!.click();
    expect(host.closes).toBe(1);
    expect(opened).toEqual(['r1']);
  });

  it('pauses and resumes', async () => {
    const { host, card, press, calls, section } = setup([revenue]);
    await section.open();
    press(card('Monday revenue'), 'Pause');
    await flush();
    expect(calls).toContain('POST /api/routines/b1b2c3d4/pause');
    expect(card('Monday revenue').textContent).toContain('Paused');
    press(card('Monday revenue'), 'Resume');
    await flush();
    expect(calls).toContain('POST /api/routines/b1b2c3d4/resume');
    expect(card('Monday revenue').textContent).toContain('Next: Mon 26 Oct, 8:00 am');
    host.close();
  });

  it('deletes only once confirmed, and Keep it leaves it be', async () => {
    const { root, host, card, press, calls, section } = setup([revenue]);
    await section.open();
    press(card('Monday revenue'), 'Delete');
    expect(calls.filter((c) => c.startsWith('DELETE'))).toEqual([]);
    expect(card('Monday revenue').textContent).toContain('Delete “Monday revenue”? It won\'t run again.');
    // Keyed like Coming up's, so focus can find them again across a redraw.
    const keys = [...card('Monday revenue').querySelectorAll<HTMLElement>('.connected-confirm button')].map((b) => b.dataset.focus);
    expect(keys).toEqual(['keep:b1b2c3d4', 'drop:b1b2c3d4']);
    press(card('Monday revenue'), 'Keep it');
    expect(card('Monday revenue').querySelector('.connected-confirm')).toBeNull();
    press(card('Monday revenue'), 'Delete');
    press(card('Monday revenue'), 'Delete it');
    await flush();
    expect(calls.filter((c) => c.startsWith('DELETE'))).toEqual(['DELETE /api/routines/b1b2c3d4']);
    expect(root.querySelector('.reminders-card')).toBeNull();
    expect(root.textContent).toContain('None yet.');
    expect(document.activeElement?.getAttribute('data-focus')).toBe('routines');
    host.close();
  });
});
