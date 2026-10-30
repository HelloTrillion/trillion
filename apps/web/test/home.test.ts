// @vitest-environment jsdom
import { beforeAll, describe, expect, it, vi } from 'vitest';
import {
  type HomeData,
  type HomeOptions,
  brandColor,
  changeLine,
  createHome,
  greeting,
  homeOf,
  localDate,
  sampleFor,
  shouldOpenHome,
  tileLabel,
} from '../src/home';

beforeAll(() => {
  // jsdom measures nothing; the sample charts only need something to observe with.
  globalThis.ResizeObserver ??= class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver;
});

// Friday 30 October 2026, 8:15 am, local.
const MORNING = new Date(2026, 9, 30, 8, 15);

const svc = (id: string, title: string, state: 'ready' | 'asks_first' | 'connect', extra: Record<string, unknown> = {}) => ({
  id,
  title,
  color: '336699',
  glyph: title[0],
  how: 'sign_in',
  category: 'Tasks and projects',
  state,
  settings_key: state === 'connect' ? '' : id,
  ...extra,
});

function payload(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    user_name: 'Sam',
    agent_name: 'Trillion',
    briefing: 'build',
    ready: 2,
    total: 5,
    next: { tile: 'mentions', service: 'slack', text: 'Connect Slack next: one sign-in adds Mentions' },
    tiles: [
      {
        id: 'next_up',
        title: 'Next up',
        type: 'next_up',
        what: 'Your next meeting, and when to leave for it',
        state: 'ready',
        services: [svc('google-calendar', 'Google Calendar', 'ready', { how: 'google', settings_key: 'google:calendar' })],
        more: 0,
      },
      {
        id: 'inbox',
        title: 'Inbox',
        type: 'inbox',
        what: "Mail that's waiting on your reply",
        state: 'asks_first',
        services: [svc('gmail', 'Gmail', 'asks_first', { how: 'google', settings_key: 'google:gmail' })],
        more: 0,
      },
      { id: 'tasks', title: 'Reminders', type: 'tasks', what: 'Your reminders and timed asks for today', state: 'built_in', services: [], more: 0 },
      {
        id: 'mentions',
        title: 'Mentions',
        type: 'mentions',
        what: 'Where people need you in Slack',
        state: 'connect',
        services: [svc('slack', 'Slack', 'connect', { how: 'slack', category: 'Chat and messages' })],
        more: 0,
      },
      {
        id: 'due_soon',
        title: 'Due soon',
        type: 'due_soon',
        what: "What's due today and tomorrow",
        state: 'connect',
        // Every service comes; the fourth is the first not shown, and of another kind.
        services: [
          svc('linear', 'Linear', 'connect'),
          svc('todoist', 'Todoist', 'connect'),
          svc('jira', 'Jira', 'connect'),
          svc('notion', 'Notion', 'connect', { category: 'Docs and files' }),
          svc('clickup', 'ClickUp', 'connect'),
          svc('monday', 'monday.com', 'connect'),
          svc('airtable', 'Airtable', 'connect'),
        ],
        more: 4,
      },
    ],
    ...over,
  };
}

function setup(over: Partial<HomeOptions> = {}, data: unknown = payload()) {
  const calls = { build: 0, connect: [] as string[], browse: [] as string[], reveal: [] as string[], said: [] as string[] };
  const fetchHome = vi.fn(async () => data);
  const home = createHome({
    fetchHome,
    onBuild: () => void calls.build++,
    onConnect: (id) => void calls.connect.push(id),
    onBrowse: (c) => void calls.browse.push(c),
    onReveal: (k) => void calls.reveal.push(k),
    now: () => MORNING,
    reducedMotion: () => false,
    announce: (t) => void calls.said.push(t),
    ...over,
  });
  document.body.replaceChildren(home.el);
  return { home, calls, fetchHome };
}

const tileEl = (root: HTMLElement, id: string): HTMLElement => root.querySelector<HTMLElement>(`.home-tile[data-id="${id}"]`)!;
const buttons = (root: Element): HTMLButtonElement[] => [...root.querySelectorAll<HTMLButtonElement>('button')];

describe('words', () => {
  it('greets by the hour, with the name when there is one', () => {
    expect(greeting(8, 'Sam')).toBe('Good morning, Sam');
    expect(greeting(11, 'Sam')).toBe('Good morning, Sam');
    expect(greeting(12, 'Sam')).toBe('Good afternoon, Sam');
    expect(greeting(16, 'Sam')).toBe('Good afternoon, Sam');
    expect(greeting(17, 'Sam')).toBe('Good evening, Sam');
    expect(greeting(23, '')).toBe('Good evening');
    expect(greeting(7, '  ')).toBe('Good morning');
  });

  it('labels each tile for a screen reader', () => {
    const d = homeOf(payload())!;
    const byId = (id: string) => d.tiles.find((t) => t.id === id)!;
    expect(tileLabel(byId('next_up'))).toBe('Next up: ready, from Google Calendar');
    expect(tileLabel(byId('mentions'))).toBe('Mentions: not connected. Connect Slack');
    expect(tileLabel(byId('due_soon'))).toBe('Due soon: not connected. Connect Linear, Todoist, Jira or 4 more');
    expect(tileLabel(byId('tasks'))).toBe('Reminders: built in');
    expect(tileLabel(byId('inbox'))).toBe('Inbox: needs your OK. Gmail asks you each time');
  });

  it('leaves out what is malformed rather than drawing it wrong', () => {
    expect(homeOf(null)).toBeNull();
    expect(homeOf({ tiles: 'no' })).toBeNull();
    const d = homeOf({ tiles: [{ id: 'x', state: 'odd', services: [{ nope: 1 }, { id: 's' }] }, 'junk'] })!;
    expect(d.tiles).toHaveLength(1);
    expect(d.tiles[0]!.state).toBe('connect');
    expect(d.tiles[0]!.services.map((s) => s.id)).toEqual(['s']);
    expect(d.briefing).toBe('build');
    expect(d.next).toBeNull();
  });

  it('takes only a real colour for the border', () => {
    expect(brandColor('336699')).toBe(`#${'336699'}`);
    expect(brandColor('red; background: url(x)')).toBe('');
    expect(brandColor('')).toBe('');
  });

  it('says what changed after a refresh, and nothing for the first draw', () => {
    const before = homeOf(payload())!;
    const after = homeOf(
      payload({ tiles: (payload()['tiles'] as Record<string, unknown>[]).map((t) => (t['id'] === 'mentions' ? { ...t, state: 'ready' } : t)) }),
    )!;
    expect(changeLine(null, after)).toBe('');
    expect(changeLine(before, before)).toBe('');
    expect(changeLine(before, after)).toBe('Mentions is ready.');
  });

  it('has a sample for every tile type', () => {
    for (const t of ['next_up', 'free_time', 'inbox', 'tasks', 'mentions', 'due_soon', 'kpis', 'files', 'checks']) {
      expect(sampleFor(t), t).toBeDefined();
    }
  });
});

describe('once a day', () => {
  it('opens on the first load of a date, and not the second', () => {
    expect(shouldOpenHome(null, '2026-10-30', true, false, false)).toBe(true);
    expect(shouldOpenHome('2026-10-29', '2026-10-30', true, false, false)).toBe(true);
    expect(shouldOpenHome('2026-10-30', '2026-10-30', true, false, false)).toBe(false);
  });

  it('stays shut when turned off, while onboarding, or over an open board', () => {
    expect(shouldOpenHome(null, '2026-10-30', false, false, false)).toBe(false);
    expect(shouldOpenHome(null, '2026-10-30', true, true, false)).toBe(false);
    expect(shouldOpenHome(null, '2026-10-30', true, false, true)).toBe(false);
  });

  it('counts days by the local date', () => {
    expect(localDate(new Date(2026, 0, 5, 23, 59))).toBe('2026-01-05');
    expect(localDate(MORNING)).toBe('2026-10-30');
  });
});

describe('Home', () => {
  it('draws the header, the meter, the tiles and the foot', async () => {
    const { home } = setup();
    await home.show();
    const el = home.el;
    expect(el.querySelector('.home-hello')?.textContent).toBe('Good morning, Sam');
    expect(el.querySelector('.home-date')?.textContent).toContain('October');
    expect(el.querySelector('.home-build')?.textContent).toBe('Build my briefing');
    const meter = el.querySelector('meter')!;
    expect(meter.value).toBe(2);
    expect(meter.max).toBe(5);
    expect(el.querySelector('.home-meter-count')?.textContent).toBe('2 of 5 ready');
    expect(el.querySelector('.home-next-text')?.textContent).toBe('Connect Slack next: one sign-in adds Mentions');
    expect(el.querySelectorAll('.home-grid > li')).toHaveLength(5);
    expect(el.querySelector('.home-foot')?.textContent).toBe('Want something else on your board? Just ask her.');
  });

  it('greets without a name when there is none', async () => {
    const { home } = setup({}, payload({ user_name: '' }));
    await home.show();
    expect(home.el.querySelector('.home-hello')?.textContent).toBe('Good morning');
  });

  it('gives each state its badge and its foot', async () => {
    const { home } = setup();
    await home.show();
    const el = home.el;
    const badge = (id: string) => tileEl(el, id).querySelector('.home-badge')?.textContent;
    expect(badge('next_up')).toBe('Ready');
    expect(badge('tasks')).toBe('Built in');
    expect(badge('inbox')).toBe('Needs your OK');
    expect(badge('mentions')).toBe('Not connected');
    expect(tileEl(el, 'next_up').querySelector('.home-from')?.textContent).toBe('From Google Calendar');
    expect(tileEl(el, 'next_up').getAttribute('aria-label')).toBe('Next up: ready, from Google Calendar');
    expect(tileEl(el, 'next_up').style.getPropertyValue('--brand')).not.toBe('');
    expect(tileEl(el, 'inbox').querySelector('.home-why')?.textContent).toBe('Gmail asks you each time, so the briefing leaves it out');
    expect(buttons(tileEl(el, 'inbox')).map((b) => b.textContent)).toEqual(['Let her read it without asking']);
    expect(buttons(tileEl(el, 'mentions')).map((b) => b.textContent?.trim())).toEqual(['SConnect Slack']);
    expect(buttons(tileEl(el, 'due_soon')).map((b) => b.getAttribute('aria-label') ?? b.textContent)).toEqual([
      'Connect Linear',
      'Connect Todoist',
      'Connect Jira',
      '4 more ways to fill Due soon',
    ]);
    expect(tileEl(el, 'due_soon').querySelector('.home-more')?.textContent).toBe('+4 more');
  });

  it('labels every preview Sample and keeps it from screen readers and the keyboard', async () => {
    const { home } = setup();
    await home.show();
    const previews = [...home.el.querySelectorAll<HTMLElement>('.home-preview')];
    expect(previews).toHaveLength(5);
    for (const p of previews) {
      expect(p.getAttribute('aria-hidden')).toBe('true');
      expect(p.inert).toBe(true);
      expect(p.querySelector('.home-sample')?.textContent).toBe('Sample');
      // The sample's own widget is drawn inside.
      expect(p.querySelector('.bk')).not.toBeNull();
      for (const f of p.querySelectorAll<HTMLElement>('a, button, input')) expect(f.tabIndex).toBe(-1);
    }
  });

  it('connects each service by its catalogue id', async () => {
    const { home, calls } = setup();
    await home.show();
    const el = home.el;
    buttons(tileEl(el, 'mentions'))[0]!.click();
    for (const b of tileEl(el, 'due_soon').querySelectorAll<HTMLButtonElement>('.home-connect')) b.click();
    el.querySelector<HTMLButtonElement>('.home-connect-next')!.click();
    expect(calls.connect).toEqual(['slack', 'linear', 'todoist', 'jira', 'slack']);
  });

  it('opens Connected tools at the kind of the first service not shown, for "+N more"', async () => {
    const { home, calls } = setup();
    await home.show();
    tileEl(home.el, 'due_soon').querySelector<HTMLButtonElement>('.home-more')!.click();
    expect(calls.browse).toEqual(['Docs and files']);
  });

  it('opens the connection that asks first at its card', async () => {
    const { home, calls } = setup();
    await home.show();
    tileEl(home.el, 'inbox').querySelector<HTMLButtonElement>('.home-reveal')!.click();
    expect(calls.reveal).toEqual(['google:gmail']);
  });

  it('builds the briefing, or opens it once built', async () => {
    const { home, calls } = setup();
    await home.show();
    const b = home.el.querySelector<HTMLButtonElement>('.home-build')!;
    b.click();
    expect(calls.build).toBe(1);
    // Busy until Home reads again: a second tap does not build twice.
    expect(b.disabled).toBe(true);
    b.click();
    expect(calls.build).toBe(1);

    const open = setup({}, payload({ briefing: 'open' }));
    await open.home.show();
    expect(open.home.el.querySelector('.home-build')?.textContent).toBe('Open today’s briefing');
  });

  it('says when everything is connected, with no button', async () => {
    const { home } = setup({}, payload({ next: null, ready: 5 }));
    await home.show();
    expect(home.el.querySelector('.home-next')?.textContent).toBe('Everything’s connected');
    expect(home.el.querySelector('.home-connect-next')).toBeNull();
  });

  it('drops the suggestion, and keeps the meter, when only asks-first tiles are left', async () => {
    const { home } = setup({}, payload({ next: null, ready: 4 }));
    await home.show();
    const next = home.el.querySelector<HTMLElement>('.home-next')!;
    expect(next.hidden).toBe(true);
    expect(next.textContent).toBe('');
    expect(home.el.querySelector('.home-meter-count')?.textContent).toBe('4 of 5 ready');
  });

  it('still connects a service that is set up but not yet running', async () => {
    const tiles = (payload()['tiles'] as Record<string, unknown>[]).map((t) =>
      t['id'] === 'mentions' ? { ...t, services: [svc('slack', 'Slack', 'connect', { settings_key: 'chat:slack' })] } : t,
    );
    const { home, calls } = setup({}, payload({ tiles }));
    await home.show();
    tileEl(home.el, 'mentions').querySelector<HTMLButtonElement>('.home-connect')!.click();
    expect(calls.connect).toEqual(['slack']);
  });

  it('shows quiet placeholders while it waits, then says so if it fails, and tries again', async () => {
    let fail = true;
    let release: () => void = () => {};
    const fetchHome = vi.fn(
      () =>
        new Promise<unknown>((resolve, reject) => {
          release = () => (fail ? reject(new Error('down')) : resolve(payload()));
        }),
    );
    const { home } = setup({ fetchHome });
    const shown = home.show();
    expect(home.el.querySelectorAll('.home-tile-wait')).toHaveLength(9);
    expect(home.el.querySelector('.home-build')).not.toBeNull();
    release();
    await shown;
    expect(home.el.querySelector('.home-failed-text')?.textContent).toBe('Couldn’t check what’s connected.');
    // The header and the button stay.
    expect(home.el.querySelector('.home-build')?.textContent).toBe('Build my briefing');
    fail = false;
    home.el.querySelector<HTMLButtonElement>('.home-retry')!.click();
    release();
    await vi.waitFor(() => expect(home.el.querySelectorAll('.home-grid > .home-tile')).toHaveLength(5));
    expect(fetchHome).toHaveBeenCalledTimes(2);
  });

  it('says which tile turned ready after a refresh', async () => {
    let data = payload();
    const { home, calls } = setup({ fetchHome: async () => data });
    await home.show();
    expect(calls.said).toEqual([]);
    data = payload({ tiles: (payload()['tiles'] as Record<string, unknown>[]).map((t) => (t['id'] === 'mentions' ? { ...t, state: 'ready' } : t)) });
    await home.refresh();
    expect(calls.said).toEqual(['Mentions is ready.']);
    expect(tileEl(home.el, 'mentions').dataset['state']).toBe('ready');
  });

  it('does not read while it is off screen', async () => {
    const { home, fetchHome } = setup();
    await home.show();
    home.el.remove();
    await home.refresh();
    expect(fetchHome).toHaveBeenCalledTimes(1);
  });

  it('staggers the tiles in, unless motion is reduced', async () => {
    const moving = setup();
    await moving.home.show();
    const tiles = [...moving.home.el.querySelectorAll<HTMLElement>('.home-tile')];
    expect(tiles.every((t) => t.classList.contains('home-in'))).toBe(true);
    expect(tiles.map((t) => t.style.getPropertyValue('--i'))).toEqual(['0', '1', '2', '3', '4']);

    const still = setup({ reducedMotion: () => true });
    await still.home.show();
    expect(still.home.el.querySelector('.home-in')).toBeNull();
  });

  it('puts nothing from the route in as markup', async () => {
    const { home } = setup({}, payload({ user_name: '<img src=x onerror=alert(1)>' }));
    await home.show();
    expect(home.el.querySelector('img:not(.connected-icon)')).toBeNull();
    expect(home.el.querySelector('.home-hello')?.textContent).toContain('<img');
  });
});
