// @vitest-environment jsdom
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { keyStep, moved, slotAt } from '../src/arrange';
import {
  DEFAULT_ORDER,
  type HomeOptions,
  type Layout,
  addedBack,
  brandColor,
  changeLine,
  createHome,
  dayBoardStep,
  endsBuild,
  defaultLayout,
  frameKeeps,
  greeting,
  herOpensHome,
  isDefaultLayout,
  movedLine,
  pickedLine,
  ledeLine,
  returnsToHome,
  homeOf,
  localDate,
  sampleFor,
  shouldOpenHome,
  tileLabel,
  withLayout,
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

function setup(over: Partial<HomeOptions> & { ownRegion?: boolean } = {}, data: unknown = payload()) {
  const calls = { build: 0, connect: [] as string[], browse: [] as string[], reveal: [] as string[], said: [] as string[] };
  const fetchHome = vi.fn(async () => data);
  const { ownRegion, ...rest } = over;
  const home = createHome({
    fetchHome,
    onBuild: () => void calls.build++,
    onConnect: (id) => void calls.connect.push(id),
    onBrowse: (c) => void calls.browse.push(c),
    onReveal: (k) => void calls.reveal.push(k),
    now: () => MORNING,
    reducedMotion: () => false,
    // Without one, Home speaks through its own region.
    ...(ownRegion ? {} : { announce: (t: string) => void calls.said.push(t) }),
    ...rest,
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

describe('main.ts decisions', () => {
  const frame = { boardOpen: false, approvals: false, folders: false, homeShown: false };

  it('keeps the workspace up for a closed board only when something holds it, Home included', () => {
    expect(frameKeeps(frame)).toBe(false);
    expect(frameKeeps({ ...frame, homeShown: true })).toBe(true);
    expect(frameKeeps({ ...frame, boardOpen: true })).toBe(true);
    expect(frameKeeps({ ...frame, approvals: true })).toBe(true);
    expect(frameKeeps({ ...frame, folders: true })).toBe(true);
  });

  it('comes back to Home only when Settings opened from Home closes by itself', () => {
    expect(returnsToHome(true, 'settings', false)).toBe(true);
    expect(returnsToHome(false, 'settings', false)).toBe(false);
    // Replaced by something else: that has the stage now.
    expect(returnsToHome(true, 'settings', true)).toBe(false);
    // Home itself closing never reopens it.
    expect(returnsToHome(true, 'board', false)).toBe(false);
  });

  it('opens the day board Home asked for once it has widgets, and never over Settings', () => {
    const empty = { open: true, widgets: [] };
    const built = { open: true, widgets: [{}] };
    const putAway = { open: false, widgets: [{}] };
    expect(dayBoardStep(null, 'day', built, 'board')).toBe('forget');
    expect(dayBoardStep('day', 'other', built, 'board')).toBe('forget');
    expect(dayBoardStep('day', 'day', null, 'board')).toBe('wait');
    expect(dayBoardStep('day', 'day', empty, 'board')).toBe('wait');
    expect(dayBoardStep('day', 'day', built, 'board')).toBe('shown');
    expect(dayBoardStep('day', 'day', putAway, 'board')).toBe('open');
    expect(dayBoardStep('day', 'day', putAway, null)).toBe('open');
    expect(dayBoardStep('day', 'day', putAway, 'settings')).toBe('chip');
  });

  it('ends a build on its turn finishing, a refusal, or an error during it, and not otherwise', () => {
    expect(endsBuild({ type: 'turn.complete' }, null, 'day', true)).toBe(false);
    expect(endsBuild({ type: 'turn.complete' }, 'day', 'day', true)).toBe(true);
    expect(endsBuild({ type: 'turn.complete' }, 'day', 'other', true)).toBe(false);
    expect(endsBuild({ type: 'error', code: 'no_model' }, 'day', 'other', false)).toBe(true);
    expect(endsBuild({ type: 'error', code: 'listening' }, 'day', 'day', true)).toBe(false);
    expect(endsBuild({ type: 'error', code: 'model' }, 'day', 'day', true)).toBe(true);
    expect(endsBuild({ type: 'error', code: 'model' }, 'day', 'day', false)).toBe(false);
    expect(endsBuild({ type: 'board' }, 'day', 'day', true)).toBe(false);
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

  it('is a labelled section, and names her in the line under the greeting', async () => {
    const { home } = setup();
    expect(home.el.tagName).toBe('SECTION');
    expect(home.el.getAttribute('aria-label')).toBe('Home');
    await home.show();
    expect(home.el.querySelector('.home-lede')?.textContent).toBe(ledeLine('Trillion'));
    expect(ledeLine('Trillion')).toBe('Here’s what your briefing can show. Connect more and Trillion will see more.');
    expect(ledeLine('')).toBe('Here’s what your briefing can show. Connect more and she’ll see more.');
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

    // A read while building keeps it busy.
    await home.refresh();
    expect(b.disabled).toBe(true);
    expect(b.textContent).toBe('Building your briefing…');
    b.click();
    expect(calls.build).toBe(1);
    // Its board came: it rests.
    home.built();
    expect(b.disabled).toBe(false);
    expect(b.textContent).toBe('Build my briefing');

    const open = setup({}, payload({ briefing: 'open' }));
    await open.home.show();
    expect(open.home.el.querySelector('.home-build')?.textContent).toBe('Open today’s briefing');
  });

  it('rests Build when the briefing moves on, or after a while if nothing comes', async () => {
    vi.useFakeTimers();
    try {
      let data = payload();
      const { home } = setup({ fetchHome: async () => data, buildTimeoutMs: 1000 });
      await home.show();
      const b = home.el.querySelector<HTMLButtonElement>('.home-build')!;
      b.click();
      data = payload({ briefing: 'open' });
      await home.refresh();
      expect(b.disabled).toBe(false);
      expect(b.textContent).toBe('Open today’s briefing');
      b.click();
      expect(b.disabled).toBe(true);
      vi.advanceTimersByTime(1000);
      expect(b.disabled).toBe(false);
    } finally {
      vi.useRealTimers();
    }
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

  it('says a tile turned ready on coming back from Settings, through a region that stays in the page', async () => {
    let data = payload();
    const { home, calls } = setup({ fetchHome: async () => data, ownRegion: true });
    await home.show();
    tileEl(home.el, 'mentions').querySelector<HTMLButtonElement>('.home-connect')!.click();
    expect(calls.connect).toEqual(['slack']);
    // Settings takes the workspace: Home is out of the page while the sign-in finishes.
    home.el.remove();
    data = payload({ tiles: (payload()['tiles'] as Record<string, unknown>[]).map((t) => (t['id'] === 'mentions' ? { ...t, state: 'ready' } : t)) });
    await home.refresh();
    // Settings closes: Home is back and shown again.
    document.body.append(home.el);
    await home.show();
    const live = document.querySelector<HTMLElement>('.home-live')!;
    expect(live.textContent).toBe('Mentions is ready.');
    expect(home.el.contains(live)).toBe(false);
    expect(live.getAttribute('aria-live')).toBe('polite');
    // The tiles still arrive as on any show.
    expect(tileEl(home.el, 'mentions').classList.contains('home-in')).toBe(true);
  });

  it('reads once at startup when connections load right behind the first show', async () => {
    const { home, fetchHome } = setup();
    const shown = home.show();
    await home.refresh();
    await shown;
    expect(fetchHome).toHaveBeenCalledTimes(1);
    // Later, a refresh reads again.
    await home.refresh();
    expect(fetchHome).toHaveBeenCalledTimes(2);
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

// ── Arranging Home ──────────────────────────────────────────────────────

describe('arranging, pure', () => {
  const slots = [0, 1, 2, 3, 4].map((i) => ({ left: (i % 3) * 320, top: Math.floor(i / 3) * 320, width: 300, height: 300 }));

  it('drops on the slot under the pointer, else the nearest', () => {
    expect(slotAt({ x: 10, y: 10 }, slots)).toBe(0);
    expect(slotAt({ x: 650, y: 100 }, slots)).toBe(2);
    expect(slotAt({ x: 400, y: 500 }, slots)).toBe(4);
    // In the gap between two tiles: the nearer one.
    expect(slotAt({ x: 305, y: 150 }, slots)).toBe(0);
    expect(slotAt({ x: 318, y: 150 }, slots)).toBe(1);
    // Past the last tile, or far outside the grid.
    expect(slotAt({ x: 900, y: 500 }, slots)).toBe(4);
    expect(slotAt({ x: -500, y: -500 }, slots)).toBe(0);
  });

  it('moves one item in a list, clamped at the ends', () => {
    expect(moved(['a', 'b', 'c', 'd'], 0, 2)).toEqual(['b', 'c', 'a', 'd']);
    expect(moved(['a', 'b', 'c', 'd'], 3, 0)).toEqual(['d', 'a', 'b', 'c']);
    expect(moved(['a', 'b', 'c'], 1, 9)).toEqual(['a', 'c', 'b']);
    expect(moved(['a', 'b'], 5, 0)).toEqual(['a', 'b']);
  });

  it('steps back on left and up, on with right and down', () => {
    expect(keyStep('ArrowLeft')).toBe(-1);
    expect(keyStep('ArrowUp')).toBe(-1);
    expect(keyStep('ArrowRight')).toBe(1);
    expect(keyStep('ArrowDown')).toBe(1);
    expect(keyStep('a')).toBe(0);
  });

  it('takes a missing removed list as nothing removed', () => {
    expect(homeOf(payload())!.removed).toEqual([]);
    const d = homeOf(payload({ removed: [{ id: 'kpis', title: 'Revenue', state: 'connect', services: [] }, { id: 'inbox' }] }))!;
    // A tile also shown is not removed.
    expect(d.removed.map((t) => t.id)).toEqual(['kpis']);
  });

  it('draws a new layout with the meter over shown tiles only', () => {
    const d = homeOf(payload())!;
    const l = withLayout(d, ['inbox', 'next_up', 'tasks', 'due_soon'], ['mentions']);
    expect(l.tiles.map((t) => t.id)).toEqual(['inbox', 'next_up', 'tasks', 'due_soon']);
    expect(l.removed.map((t) => t.id)).toEqual(['mentions']);
    expect(l.total).toBe(4);
    expect(l.ready).toBe(2);
    // The suggestion was for the tile taken off.
    expect(l.next).toBeNull();
    const fewer = withLayout(d, ['inbox', 'mentions', 'due_soon'], ['tasks', 'next_up']);
    expect(fewer.ready).toBe(0);
    expect(fewer.removed.map((t) => t.id)).toEqual(['next_up', 'tasks']);
    // A tile named nowhere stays shown, after the rest.
    expect(withLayout(d, ['due_soon'], []).tiles.map((t) => t.id)).toEqual(['due_soon', 'next_up', 'inbox', 'tasks', 'mentions']);
  });

  it('knows the default layout, and adds a tile back at its default place', () => {
    const d = homeOf(payload())!;
    expect(isDefaultLayout(d)).toBe(true);
    expect(isDefaultLayout(withLayout(d, ['inbox', 'next_up', 'tasks', 'mentions', 'due_soon'], []))).toBe(false);
    expect(isDefaultLayout(withLayout(d, ['next_up', 'inbox', 'tasks', 'due_soon'], ['mentions']))).toBe(false);
    expect(defaultLayout(withLayout(d, ['due_soon', 'inbox'], ['next_up', 'tasks', 'mentions']))).toEqual({
      order: ['next_up', 'inbox', 'tasks', 'mentions', 'due_soon'],
      removed: [],
    });
    expect(addedBack(['next_up', 'tasks', 'due_soon'], 'inbox')).toEqual(['next_up', 'inbox', 'tasks', 'due_soon']);
    expect(addedBack(['due_soon', 'tasks'], 'next_up')).toEqual(['next_up', 'due_soon', 'tasks']);
    expect(addedBack(['tasks', 'next_up'], 'checks')).toEqual(['tasks', 'next_up', 'checks']);
    expect(DEFAULT_ORDER).toHaveLength(9);
  });

  it('says where a held tile is', () => {
    expect(pickedLine('Inbox', 3, 8)).toBe('Inbox, picked up, position 3 of 8');
    expect(movedLine(4, 8)).toBe('moved to position 4 of 8');
  });

  it('opens Home for her change only into a closed workspace with nothing else to show', () => {
    const f = { by: 'her', open: false, boardWidgets: false, approvals: false, folders: false, onboarding: false };
    expect(herOpensHome(f)).toBe(true);
    expect(herOpensHome({ ...f, by: undefined })).toBe(false);
    expect(herOpensHome({ ...f, open: true })).toBe(false);
    expect(herOpensHome({ ...f, boardWidgets: true })).toBe(false);
    expect(herOpensHome({ ...f, approvals: true })).toBe(false);
    expect(herOpensHome({ ...f, folders: true })).toBe(false);
    expect(herOpensHome({ ...f, onboarding: true })).toBe(false);
  });
});

describe('arranging Home', () => {
  type Tile = Record<string, unknown>;
  const ALL = payload()['tiles'] as Tile[];
  const counted = (ts: Tile[]) => ts.filter((t) => t['state'] === 'ready' || t['state'] === 'built_in').length;

  /** What the core answers for a layout, as the route would. */
  function served(order: readonly string[], removed: readonly string[], over: Record<string, unknown> = {}): Record<string, unknown> {
    const tiles = order.filter((id) => !removed.includes(id)).map((id) => ALL.find((t) => t['id'] === id)!);
    const gone = ALL.filter((t) => removed.includes(t['id'] as string));
    return payload({ tiles, removed: gone, ready: counted(tiles), total: tiles.length, ...over });
  }

  const IDS = ['next_up', 'inbox', 'tasks', 'mentions', 'due_soon'];

  // jsdom lays nothing out: each tile stands in a 3-column grid of 300px squares by its place.
  const realRect = Element.prototype.getBoundingClientRect;
  beforeAll(() => {
    Element.prototype.getBoundingClientRect = function (this: Element): DOMRect {
      const parent = this.parentElement;
      if (this.classList.contains('home-grid')) return { left: 0, top: 0, width: 940, height: 940, right: 940, bottom: 940, x: 0, y: 0 } as DOMRect;
      if (parent?.classList.contains('home-grid') && !this.classList.contains('home-lifted')) {
        const i = [...parent.children].filter((c) => !c.classList.contains('home-lifted')).indexOf(this);
        const left = (i % 3) * 320;
        const top = Math.floor(i / 3) * 320;
        return { left, top, width: 300, height: 300, right: left + 300, bottom: top + 300, x: left, y: top } as DOMRect;
      }
      return realRect.call(this);
    };
    return () => {
      Element.prototype.getBoundingClientRect = realRect;
    };
  });

  function arrange(data: unknown = payload(), save?: (l: Layout) => Promise<unknown>, over: Partial<HomeOptions> = {}) {
    const saveLayout = vi.fn(save ?? (async (l: Layout) => served(l.order, l.removed)));
    const s = setup({ saveLayout, ...over }, data);
    return { ...s, saveLayout };
  }

  const order = (root: HTMLElement): string[] => [...root.querySelectorAll<HTMLElement>('.home-grid > .home-tile')].map((t) => t.dataset['id']!);
  const grip = (root: HTMLElement, id: string) => tileEl(root, id).querySelector<HTMLButtonElement>('.home-grip')!;
  const pointer = (type: string, x: number, y: number) =>
    new PointerEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, pointerId: 7, isPrimary: true });
  const key = (target: Element, k: string) => {
    const e = new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true });
    target.dispatchEvent(e);
    return e;
  };

  it('gives each tile a grip and a ×, named for it, only when it can save', async () => {
    const { home } = arrange();
    await home.show();
    const inbox = tileEl(home.el, 'inbox');
    expect(inbox.querySelector('.home-grip')?.getAttribute('aria-label')).toBe('Move Inbox');
    expect(inbox.querySelector('.home-remove')?.getAttribute('aria-label')).toBe('Remove Inbox from Home');
    const help = document.getElementById(inbox.querySelector('.home-grip')!.getAttribute('aria-describedby')!);
    expect(help?.textContent).toContain('Escape');

    const plain = setup();
    await plain.home.show();
    expect(plain.home.el.querySelector('.home-grip, .home-remove, .home-add')).toBeNull();
  });

  it('drags a tile with the pointer through a placeholder, and saves where it drops', async () => {
    const { home, saveLayout } = arrange();
    await home.show();
    const g = grip(home.el, 'next_up');
    g.dispatchEvent(pointer('pointerdown', 150, 20));
    // A press alone is not a drag.
    window.dispatchEvent(pointer('pointermove', 152, 21));
    expect(home.el.querySelector('.home-placeholder')).toBeNull();
    // Over the third slot.
    window.dispatchEvent(pointer('pointermove', 790, 150));
    const lifted = tileEl(home.el, 'next_up');
    expect(lifted.classList.contains('home-lifted')).toBe(true);
    expect(lifted.style.transform).toContain('scale');
    const kids = [...home.el.querySelector('.home-grid')!.children].filter((c) => !c.classList.contains('home-lifted'));
    expect(kids.map((c) => (c.classList.contains('home-placeholder') ? '·' : (c as HTMLElement).dataset['id']))).toEqual(['inbox', 'tasks', '·', 'mentions', 'due_soon']);
    expect(saveLayout).not.toHaveBeenCalled();
    window.dispatchEvent(pointer('pointerup', 790, 150));
    expect(home.el.querySelector('.home-placeholder')).toBeNull();
    expect(lifted.classList.contains('home-lifted')).toBe(false);
    expect(order(home.el)).toEqual(['inbox', 'tasks', 'next_up', 'mentions', 'due_soon']);
    expect(saveLayout).toHaveBeenCalledWith({ order: ['inbox', 'tasks', 'next_up', 'mentions', 'due_soon'], removed: [] });
    await vi.waitFor(() => expect(home.el.querySelector<HTMLElement>('.home-reset-foot')!.hidden).toBe(false));
  });

  it('puts a dragged tile back on Escape, saves nothing, and keeps the workspace open', async () => {
    const { home, saveLayout } = arrange();
    await home.show();
    const behind = vi.fn();
    window.addEventListener('keydown', behind);
    try {
      grip(home.el, 'inbox').dispatchEvent(pointer('pointerdown', 470, 20));
      window.dispatchEvent(pointer('pointermove', 150, 500));
      expect(home.el.querySelector('.home-placeholder')).not.toBeNull();
      key(document.body, 'Escape');
      expect(behind).not.toHaveBeenCalled();
      expect(home.el.querySelector('.home-placeholder')).toBeNull();
      expect(order(home.el)).toEqual(IDS);
      // The pointer letting go afterwards does nothing more.
      window.dispatchEvent(pointer('pointerup', 150, 500));
      expect(saveLayout).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener('keydown', behind);
    }
  });

  it('moves a tile from the keyboard, saying where it is', async () => {
    const { home, calls, saveLayout } = arrange();
    await home.show();
    const g = grip(home.el, 'inbox');
    g.focus();
    key(g, 'Enter');
    expect(calls.said.at(-1)).toBe('Inbox, picked up, position 2 of 5');
    expect(tileEl(home.el, 'inbox').classList.contains('home-held')).toBe(true);
    key(g, 'ArrowRight');
    expect(calls.said.at(-1)).toBe('moved to position 3 of 5');
    key(g, 'ArrowDown');
    expect(calls.said.at(-1)).toBe('moved to position 4 of 5');
    expect(order(home.el)).toEqual(['next_up', 'tasks', 'mentions', 'inbox', 'due_soon']);
    // Moving it kept focus on its grip.
    expect(document.activeElement).toBe(g);
    expect(saveLayout).not.toHaveBeenCalled();
    key(g, ' ');
    expect(calls.said.at(-1)).toBe('dropped');
    expect(saveLayout).toHaveBeenCalledWith({ order: ['next_up', 'tasks', 'mentions', 'inbox', 'due_soon'], removed: [] });
    expect(tileEl(home.el, 'inbox').classList.contains('home-held')).toBe(false);
    // Nothing became ready: a move says nothing more once saved.
    await vi.waitFor(() => expect(home.el.querySelector<HTMLElement>('.home-reset-foot')!.hidden).toBe(false));
    expect(calls.said.at(-1)).toBe('dropped');
  });

  it('puts a keyboard-held tile back on Escape, and stops at the ends', async () => {
    const { home, calls, saveLayout } = arrange();
    await home.show();
    const g = grip(home.el, 'next_up');
    g.focus();
    key(g, ' ');
    key(g, 'ArrowLeft');
    expect(calls.said.at(-1)).toBe('moved to position 1 of 5');
    key(g, 'ArrowRight');
    key(g, 'ArrowRight');
    expect(order(home.el)[2]).toBe('next_up');
    const esc = key(g, 'Escape');
    expect(esc.defaultPrevented).toBe(true);
    expect(order(home.el)).toEqual(IDS);
    expect(calls.said.at(-1)).toBe('Next up put back at position 1 of 5');
    expect(saveLayout).not.toHaveBeenCalled();
  });

  it('removes a tile at once, with Undo for six seconds', async () => {
    vi.useFakeTimers();
    try {
      const { home, calls, saveLayout } = arrange();
      await home.show();
      tileEl(home.el, 'inbox').querySelector<HTMLButtonElement>('.home-remove')!.click();
      expect(order(home.el)).toEqual(['next_up', 'tasks', 'mentions', 'due_soon']);
      expect(saveLayout).toHaveBeenCalledWith({ order: ['next_up', 'tasks', 'mentions', 'due_soon'], removed: ['inbox'] });
      const toast = home.el.querySelector<HTMLElement>('.home-toast')!;
      expect(toast.hidden).toBe(false);
      expect(toast.querySelector('.home-toast-text')?.textContent).toBe('Inbox removed');
      expect(calls.said.at(-1)).toBe('Inbox removed');
      expect(home.el.querySelector('.home-add-open')?.textContent).toBe('Add widgets (1)');
      // Focus moved to the tile that took its place.
      expect(document.activeElement).toBe(grip(home.el, 'tasks'));
      expect(home.el.querySelector('.home-meter-count')?.textContent).toBe('2 of 4 ready');
      await vi.advanceTimersByTimeAsync(5000);
      toast.querySelector<HTMLButtonElement>('.home-undo')!.click();
      expect(order(home.el)).toEqual(IDS);
      expect(saveLayout).toHaveBeenLastCalledWith({ order: IDS, removed: [] });
      expect(toast.hidden).toBe(true);
      expect(home.el.querySelector('.home-add')).toBeNull();

      // Left alone, the toast goes after six seconds.
      tileEl(home.el, 'mentions').querySelector<HTMLButtonElement>('.home-remove')!.click();
      await vi.advanceTimersByTimeAsync(5900);
      expect(toast.hidden).toBe(false);
      await vi.advanceTimersByTimeAsync(200);
      expect(toast.hidden).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it('lists removed tiles under Add widgets, with badges, and adds one back', async () => {
    const { home, calls, saveLayout } = arrange(served(['next_up', 'tasks', 'due_soon'], ['inbox', 'mentions']));
    await home.show();
    const open = home.el.querySelector<HTMLButtonElement>('.home-add-open')!;
    expect(open.textContent).toBe('Add widgets (2)');
    expect(open.getAttribute('aria-expanded')).toBe('false');
    // At the end of the grid.
    expect(home.el.querySelector('.home-grid')!.lastElementChild?.classList.contains('home-add')).toBe(true);
    open.click();
    const panel = home.el.querySelector<HTMLElement>('.home-add-panel')!;
    expect(panel.hidden).toBe(false);
    const rows = [...panel.querySelectorAll<HTMLElement>('.home-add-row')];
    expect(rows.map((r) => r.querySelector('.home-add-row-title')?.textContent)).toEqual(['Inbox', 'Mentions']);
    expect(rows.map((r) => r.querySelector('.home-badge')?.textContent)).toEqual(['Needs your OK', 'Not connected']);
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Add Inbox to Home');
    rows[0]!.querySelector<HTMLButtonElement>('.home-add-one')!.click();
    expect(order(home.el)).toEqual(['next_up', 'inbox', 'tasks', 'due_soon']);
    expect(saveLayout).toHaveBeenCalledWith({ order: ['next_up', 'inbox', 'tasks', 'due_soon'], removed: ['mentions'] });
    expect(calls.said.at(-1)).toBe('Inbox added');
    // One left: the panel stays open on it.
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Add Mentions to Home');
    expect(home.el.querySelector('.home-add-open')?.textContent).toBe('Add widgets (1)');
    // Escape closes it, back to the dashed tile, and the workspace stays.
    const esc = key(document.activeElement!, 'Escape');
    expect(esc.defaultPrevented).toBe(true);
    expect(home.el.querySelector<HTMLElement>('.home-add-panel')!.hidden).toBe(true);
    expect(document.activeElement).toBe(home.el.querySelector('.home-add-open'));
  });

  it('resets Home from the panel, and from the foot line once the order is changed', async () => {
    const { home, saveLayout } = arrange(served(['due_soon', 'next_up', 'tasks'], ['inbox', 'mentions']));
    await home.show();
    // Something removed: Reset is in the panel, not the foot.
    expect(home.el.querySelector<HTMLElement>('.home-reset-foot')!.hidden).toBe(true);
    home.el.querySelector<HTMLButtonElement>('.home-add-open')!.click();
    home.el.querySelector<HTMLButtonElement>('.home-add-panel .home-reset')!.click();
    expect(order(home.el)).toEqual(IDS);
    expect(saveLayout).toHaveBeenLastCalledWith({ order: IDS, removed: [] });
    expect(home.el.querySelector('.home-add')).toBeNull();
    expect(home.el.querySelector<HTMLElement>('.home-reset-foot')!.hidden).toBe(true);

    const second = arrange(served(['inbox', 'next_up', 'tasks', 'mentions', 'due_soon'], []));
    await second.home.show();
    const foot = second.home.el.querySelector<HTMLButtonElement>('.home-reset-foot')!;
    expect(foot.hidden).toBe(false);
    expect(foot.textContent).toBe('Reset Home');
    foot.click();
    expect(order(second.home.el)).toEqual(IDS);
    expect(foot.hidden).toBe(true);
  });

  it('puts the layout back and says so when a save fails', async () => {
    const { home, calls } = arrange(payload(), async () => {
      throw new Error('HTTP 500');
    });
    await home.show();
    tileEl(home.el, 'mentions').querySelector<HTMLButtonElement>('.home-remove')!.click();
    expect(order(home.el)).not.toContain('mentions');
    await vi.waitFor(() => expect(order(home.el)).toEqual(IDS));
    expect(home.el.querySelector('.home-toast-text')?.textContent).toBe('Couldn’t save that change');
    expect(home.el.querySelector<HTMLElement>('.home-undo')!.hidden).toBe(true);
    expect(calls.said.at(-1)).toBe('Couldn’t save that change');
    expect(home.el.querySelector('.home-add')).toBeNull();
    expect(home.el.querySelector('.home-meter-count')?.textContent).toBe('2 of 5 ready');
  });

  it('redraws on a home frame without animation, and waits for a drag in progress', async () => {
    let data = payload();
    const { home } = arrange(undefined, undefined, { fetchHome: async () => data });
    await home.show();
    data = served(['due_soon', 'next_up', 'inbox', 'tasks'], ['mentions']);
    await home.changed();
    expect(order(home.el)).toEqual(['due_soon', 'next_up', 'inbox', 'tasks']);
    expect(home.el.querySelector('.home-in')).toBeNull();
    expect(home.el.querySelector('.home-add-open')?.textContent).toBe('Add widgets (1)');

    // A frame mid-drag: the drag finishes first, then Home reads.
    grip(home.el, 'tasks').dispatchEvent(pointer('pointerdown', 470, 340));
    window.dispatchEvent(pointer('pointermove', 470, 330));
    data = served(IDS, []);
    await home.changed();
    expect(tileEl(home.el, 'tasks').classList.contains('home-lifted')).toBe(true);
    expect(home.el.querySelector('.home-add')).not.toBeNull();
    window.dispatchEvent(pointer('pointerup', 470, 330));
    await vi.waitFor(() => expect(home.el.querySelector('.home-add')).toBeNull());
    expect(order(home.el)).toEqual(IDS);
  });

  it('does not read for a frame while off screen', async () => {
    const { home, fetchHome } = arrange();
    await home.show();
    home.el.remove();
    await home.changed();
    expect(fetchHome).toHaveBeenCalledTimes(1);
  });
});
