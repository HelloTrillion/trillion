// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { Helper, HelperList } from '../src/helpers';
import { LONG_PRESS_MS, createRail, herLabel, railLabel } from '../src/rail';
import type { Api } from '../src/settings';

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

const flush = async (): Promise<void> => {
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0));
};

const NOW = new Date(2026, 10, 5, 9, 30).getTime();

const own = (over: Partial<Helper> = {}): Helper => ({
  id: 'own:aaaa',
  kind: 'own',
  name: 'Inbox Monitor',
  job: 'Watches the inbox',
  state: 'idle',
  doing: null,
  since_ms: null,
  last_run: null,
  next_ms: null,
  waiting: [],
  conversation_id: 'c-inbox',
  paused: false,
  icon: 'envelope',
  color: 'blue',
  ...over,
});

const routine = (over: Partial<Helper> = {}): Helper => own({ id: 'routine:bbbb', kind: 'routine', name: 'Morning brief', conversation_id: null, ...over });

const list = (helpers: Helper[]): HelperList => ({ helpers, needs_you: helpers.filter((h) => h.state === 'needs_you').length });

function rail(answer: (method: string, path: string) => Response = () => json({ error: 'not_found' }, 404)) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const api: Api = async (method, path, body) => {
    calls.push({ method, path, body });
    return answer(method, path);
  };
  const opened: string[] = [];
  const said: string[] = [];
  const asked: string[] = [];
  const details: string[] = [];
  const lists: HelperList[] = [];
  let her = 0;
  let panel = 0;
  const nav = document.createElement('nav');
  nav.setAttribute('aria-label', 'Helpers');
  document.body.append(nav);
  const r = createRail(nav, {
    api,
    announce: (text) => said.push(text),
    open: (id) => opened.push(id),
    her: () => (her += 1),
    newHelper: (text) => asked.push(text),
    details: (id) => details.push(id),
    panel: () => (panel += 1),
    update: (l) => lists.push(l),
    now: () => NOW,
    capName: () => null,
  });
  return { r, nav, calls, opened, said, asked, details, lists, her: () => her, panel: () => panel };
}

const pics = (nav: HTMLElement): HTMLButtonElement[] => [...nav.querySelectorAll<HTMLButtonElement>('.rail-pic')];
const labels = (nav: HTMLElement): (string | null)[] => pics(nav).map((b) => b.getAttribute('aria-label'));
const pic = (nav: HTMLElement, id: string): HTMLButtonElement => nav.querySelector<HTMLButtonElement>(`[data-id="${id}"]`)!;
const menu = (): HTMLElement | null => document.querySelector('.rail-menu');
const item = (text: string): HTMLButtonElement => [...document.querySelectorAll<HTMLButtonElement>('.rail-menu-item')].find((b) => b.textContent === text)!;
const key = (target: Element, k: string, more: KeyboardEventInit = {}): void => {
  target.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, ...more }));
};

afterEach(() => {
  document.body.replaceChildren();
  vi.useRealTimers();
});

describe('the rail', () => {
  it('shows her first, then the user’s own helpers in the list’s order, then +; nothing else', () => {
    const { r, nav } = rail();
    r.name('Ada');
    r.update(list([own({ id: 'own:1', name: 'Invoices' }), routine(), own({ id: 'own:2', name: 'Inbox Monitor' }), routine({ id: 'watcher:email', kind: 'watcher' })]));
    expect(labels(nav)).toEqual(['Ada', 'Invoices', 'Inbox Monitor', 'New helper']);
    expect(pics(nav).every((b) => b.tagName === 'BUTTON' && b.type === 'button')).toBe(true);
    expect(pics(nav)[1]!.title).toBe('Invoices');
    // Pictures only: no names written out.
    expect(nav.textContent).toBe('');
    r.update(list([own({ id: 'own:2', name: 'Inbox Monitor' }), own({ id: 'own:3', name: 'Receipts' })]));
    expect(labels(nav)).toEqual(['Ada', 'Inbox Monitor', 'Receipts', 'New helper']);
  });

  it('rings the picture whose chat is showing, and hers for any other', () => {
    const { r, nav } = rail();
    r.update(list([own({ id: 'own:1', conversation_id: 'c-1' }), own({ id: 'own:2', conversation_id: 'c-2' })]));
    const current = (): (string | null)[] => pics(nav).map((b) => b.getAttribute('aria-current'));
    r.select('c-2');
    expect(current()).toEqual([null, null, 'page', null]);
    r.select('c-hers');
    expect(current()).toEqual(['page', null, null, null]);
    r.select(null);
    expect(current()).toEqual(['page', null, null, null]);
    r.select('c-1');
    expect(current()).toEqual([null, 'page', null, null]);
  });

  it('dots a helper working or needing you, says so in its name, and dims one paused', () => {
    const { r, nav } = rail();
    r.update(list([own({ id: 'own:1', state: 'working', name: 'A' }), own({ id: 'own:2', state: 'needs_you', name: 'B' }), own({ id: 'own:3', state: 'paused', paused: true, name: 'C' })]));
    expect(pics(nav).slice(1, 4).map((b) => [b.dataset['state'], b.querySelector<HTMLElement>('.rail-dot')!.dataset['state']])).toEqual([
      ['working', 'working'],
      ['needs_you', 'needs_you'],
      ['paused', 'paused'],
    ]);
    expect(labels(nav).slice(1, 4)).toEqual(['A, working', 'B, needs you', 'C, paused']);
    const css = readFileSync(resolve(__dirname, '../src/app.css'), 'utf8');
    expect(css).toMatch(/\.rail-dot\[data-state='working'\] \{[^}]*display: block[^}]*var\(--color-accent\)/);
    expect(css).toMatch(/\.rail-dot\[data-state='needs_you'\] \{[^}]*display: block[^}]*var\(--color-amber\)/);
    expect(css).toMatch(/\.rail-pic\[data-state='paused'\] \.look[^{]*\{[^}]*opacity/);
    expect(css).toMatch(/\.rail-pic\[aria-current='page'\] \{[^}]*var\(--color-accent\)/);
  });

  it('names a helper’s picture: its name, and its state when not at rest', () => {
    expect(railLabel(own(), NOW)).toBe('Inbox Monitor');
    expect(railLabel(own({ state: 'needs_you' }), NOW)).toBe('Inbox Monitor, needs you');
    expect(railLabel(own({ state: 'capped' }), NOW, 'Anthropic')).toBe('Inbox Monitor, paused: Anthropic is at its cap');
    expect(herLabel('Trillion', 0)).toBe('Trillion');
    expect(herLabel('Trillion', 2)).toBe('Trillion, 2 need you in Routines and watchers');
  });

  it('dots her when a routine or watcher needs you', () => {
    const { r, nav } = rail();
    r.update(list([routine({ state: 'needs_you' }), own({ state: 'needs_you' })]));
    expect(pics(nav)[0]!.querySelector<HTMLElement>('.rail-dot')!.dataset['state']).toBe('needs_you');
    expect(labels(nav)[0]).toBe('Trillion, 1 needs you in Routines and watchers');
    r.update(list([routine()]));
    expect(pics(nav)[0]!.querySelector<HTMLElement>('.rail-dot')!.dataset['state']).toBe('idle');
  });

  it('opens a helper’s chat on a tap, and goes back to her on hers', async () => {
    const { r, nav, opened, her } = rail();
    r.update(list([own({ id: 'own:1', conversation_id: 'c-1' })]));
    pic(nav, 'own:1').click();
    await flush();
    expect(opened).toEqual(['c-1']);
    pics(nav)[0]!.click();
    expect(her()).toBe(1);
  });

  it('asks the core for a chat when the list gives none', async () => {
    const { r, nav, opened, calls } = rail((method, path) =>
      method === 'POST' && path === '/api/helpers/own%3A1/conversation' ? json({ conversation_id: 'c-new' }) : json({}, 404),
    );
    r.update(list([own({ id: 'own:1', conversation_id: null })]));
    pic(nav, 'own:1').click();
    await flush();
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual(['POST /api/helpers/own%3A1/conversation']);
    expect(opened).toEqual(['c-new']);
  });

  it('+ asks her for a helper in the panel’s one field', () => {
    const { nav, asked } = rail();
    const add = pics(nav).at(-1)!;
    add.click();
    expect(add.getAttribute('aria-expanded')).toBe('true');
    const field = document.querySelector<HTMLInputElement>('#rail-new-text')!;
    expect(document.activeElement).toBe(field);
    expect(document.querySelector('label[for="rail-new-text"]')?.textContent).toBe('What should it do?');
    field.value = 'Watch my invoices';
    document.querySelector<HTMLFormElement>('#rail-new')!.requestSubmit();
    expect(asked).toEqual(['Watch my invoices']);
    expect(document.querySelector('#rail-new')).toBeNull();
    expect(add.getAttribute('aria-expanded')).toBe('false');
  });

  it('opens a helper’s menu on a right-click and Shift+F10, and Escape closes it back on its picture', () => {
    const { r, nav } = rail();
    r.update(list([own({ id: 'own:1' })]));
    const b = pic(nav, 'own:1');
    const ev = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });
    b.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(true);
    expect(menu()?.getAttribute('role')).toBe('menu');
    expect([...menu()!.querySelectorAll('[role="menuitem"]')].map((i) => i.textContent)).toEqual(['Change picture', 'Pause', 'Details', 'Delete']);
    expect(document.activeElement).toBe(item('Change picture'));
    key(document.activeElement!, 'ArrowDown');
    expect(document.activeElement).toBe(item('Pause'));
    key(document.activeElement!, 'Escape');
    expect(menu()).toBeNull();
    expect(document.activeElement).toBe(b);
    key(b, 'F10', { shiftKey: true });
    expect(menu()).not.toBeNull();
    key(document.activeElement!, 'Escape');
    key(b, 'ContextMenu');
    expect(menu()).not.toBeNull();
  });

  it('opens the menu on a long press, and the press opens no chat', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { r, nav, opened } = rail();
    r.update(list([own({ id: 'own:1' })]));
    const b = pic(nav, 'own:1');
    const down = new Event('pointerdown', { bubbles: true }) as PointerEvent;
    Object.assign(down, { pointerType: 'touch', clientX: 5, clientY: 5 });
    b.dispatchEvent(down);
    vi.advanceTimersByTime(LONG_PRESS_MS);
    expect(menu()).not.toBeNull();
    b.dispatchEvent(new Event('pointerup', { bubbles: true }));
    b.click();
    vi.useRealTimers();
    await flush();
    expect(opened).toEqual([]);
  });

  it('pauses and resumes through the helper’s routes, and draws the list they answer with', async () => {
    const after = list([own({ id: 'own:1', state: 'paused', paused: true })]);
    const { r, nav, calls, lists } = rail(() => json(after));
    r.update(list([own({ id: 'own:1' })]));
    pic(nav, 'own:1').dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
    item('Pause').click();
    await flush();
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual(['POST /api/helpers/own%3A1/pause']);
    expect(lists).toEqual([after]);
    expect(menu()).toBeNull();
    r.update(after);
    pic(nav, 'own:1').dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
    item('Resume').click();
    await flush();
    expect(calls.at(-1)?.path).toBe('/api/helpers/own%3A1/resume');
  });

  it('says what went wrong when a route fails', async () => {
    const { r, nav, said } = rail(() => json({ error: 'nope' }, 409));
    r.update(list([own({ id: 'own:1' })]));
    pic(nav, 'own:1').dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
    item('Pause').click();
    await flush();
    expect(said).toEqual(["That didn't work. It may have just finished or been removed."]);
  });

  it('opens its details in the panel', () => {
    const { r, nav, details } = rail();
    r.update(list([own({ id: 'own:1' })]));
    pic(nav, 'own:1').dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
    item('Details').click();
    expect(details).toEqual(['own:1']);
    expect(menu()).toBeNull();
  });

  it('deletes only after the panel’s confirm: Keep it is focused first, Delete it deletes', async () => {
    const { r, nav, calls } = rail(() => json(list([])));
    r.update(list([own({ id: 'own:1', name: 'Invoices' })]));
    const b = pic(nav, 'own:1');
    b.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
    item('Delete').click();
    const confirm = document.querySelector<HTMLElement>('.rail-confirm')!;
    expect(confirm.getAttribute('role')).toBe('alertdialog');
    expect(confirm.textContent).toContain('Delete Invoices? Its routines stop. Its chat stays in History.');
    expect(document.activeElement?.textContent).toBe('Keep it');
    (document.activeElement as HTMLButtonElement).click();
    expect(document.querySelector('.rail-confirm')).toBeNull();
    expect(document.activeElement).toBe(b);
    expect(calls).toEqual([]);
    b.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
    item('Delete').click();
    [...document.querySelectorAll<HTMLButtonElement>('.rail-confirm button')].find((x) => x.textContent === 'Delete it')!.click();
    await flush();
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual(['DELETE /api/helpers/own%3A1']);
  });

  it('changes its picture with the panel’s picker, saved at each tap', async () => {
    const { r, nav, calls } = rail(() => json(list([own({ id: 'own:1', icon: 'star', color: 'blue' })])));
    r.update(list([own({ id: 'own:1' })]));
    pic(nav, 'own:1').dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
    item('Change picture').click();
    expect(menu()).toBeNull();
    const picker = document.querySelector<HTMLElement>('.look-picker')!;
    expect(picker.getAttribute('aria-label')).toBe('Picture for Inbox Monitor');
    picker.querySelector<HTMLButtonElement>('[data-icon="star"]')!.click();
    await flush();
    expect(calls).toEqual([{ method: 'PUT', path: '/api/helpers/own%3A1/look', body: { icon: 'star', color: 'blue' } }]);
    key(document.activeElement!, 'Escape');
    expect(document.querySelector('.look-picker')).toBeNull();
    expect(document.activeElement).toBe(pic(nav, 'own:1'));
  });

  it('gives her picture one item: the panel, with her routines and watchers', () => {
    const { nav, panel } = rail();
    pics(nav)[0]!.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
    expect([...menu()!.querySelectorAll('[role="menuitem"]')].map((i) => i.textContent)).toEqual(['Routines and watchers']);
    item('Routines and watchers').click();
    expect(panel()).toBe(1);
    expect(menu()).toBeNull();
  });

  it('closes a menu whose helper is gone, and a tap outside closes one', () => {
    const { r, nav } = rail();
    r.update(list([own({ id: 'own:1' })]));
    pic(nav, 'own:1').dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
    r.update(list([]));
    expect(menu()).toBeNull();
    pics(nav)[0]!.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    expect(menu()).toBeNull();
  });

  it('is one tab stop, moved along by the arrows', () => {
    const { r, nav } = rail();
    r.update(list([own({ id: 'own:1' }), own({ id: 'own:2' })]));
    expect(pics(nav).map((b) => b.tabIndex)).toEqual([0, -1, -1, -1]);
    pics(nav)[0]!.focus();
    key(pics(nav)[0]!, 'ArrowDown');
    expect(document.activeElement).toBe(pic(nav, 'own:1'));
    expect(pics(nav).map((b) => b.tabIndex)).toEqual([-1, 0, -1, -1]);
    key(document.activeElement!, 'End');
    expect(document.activeElement).toBe(pics(nav).at(-1));
  });
});

describe('the rail in the page', () => {
  const html = readFileSync(resolve(__dirname, '../index.html'), 'utf8');

  it('is in the sidebar, first, in place of the Helpers button', () => {
    const aside = html.slice(html.indexOf('<aside'), html.indexOf('</aside>'));
    expect(aside).toMatch(/<nav class="rail" id="rail" aria-label="Helpers"><\/nav>/);
    expect(aside.indexOf('id="rail"')).toBeLessThan(aside.indexOf('id="hide-sidebar"'));
    expect(html).not.toContain('helpers-btn');
  });

  it('is a strip across the top of the sheet on a phone', () => {
    const css = readFileSync(resolve(__dirname, '../src/app.css'), 'utf8');
    const phone = css.slice(css.lastIndexOf('@media (max-width: 767px)'));
    expect(phone).toMatch(/\.rail \{[^}]*position: static;[^}]*flex-direction: row;/);
  });
});
