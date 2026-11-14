// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { type BrowserState, activeTask, createHerBrowser, keyInput, pagePoint, pillTarget, pillWords, reason, stateWords } from '../src/features/her-browser';
import type { Host } from '../src/panel';

function host(): Host & { children: Node[]; isOpen: boolean } {
  const h = {
    isOpen: false,
    get open() {
      return h.isOpen;
    },
    children: [] as Node[],
    showModal() {
      h.isOpen = true;
    },
    close() {
      h.isOpen = false;
    },
    replaceChildren(...nodes: (Node | string)[]) {
      h.children = nodes.filter((n): n is Node => typeof n !== 'string');
    },
    setAttribute() {},
    addEventListener() {},
  };
  return h;
}

function state(over: Partial<BrowserState> = {}): BrowserState {
  return {
    about: 'Her own browser, separate from yours.',
    available: true,
    installed: true,
    version: '158',
    size_mb: 150,
    installing: false,
    install_error: null,
    settings: { act: false, stronger: false, search: 'duckduckgo', live: true },
    engines: [{ id: 'duckduckgo', name: 'DuckDuckGo' }],
    sites: [],
    tasks: [],
    free: null,
    ...over,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('her browser: the small rules', () => {
  it('picks the task that needs the user first', () => {
    const s = state({
      tasks: [
        { id: 'a', state: 'working', mode: 'research', origin: 'https://example.com' },
        { id: 'b', state: 'needs_you', mode: 'research', ask: { kind: 'chip', id: 'x', text: 'Open example.org?', origin: null, pending_id: null } },
      ],
    });
    expect(activeTask(s)?.id).toBe('b');
    expect(pillWords(activeTask(s)!)).toBe('Needs you · Open example.org?');
    expect(pillWords(s.tasks[0]!)).toBe('Browsing example.com');
    // The page the picture shows, not where the task began.
    expect(pillWords(s.tasks[0]!, 'https://www.bing.com/search?q=x')).toBe('Browsing www.bing.com');
    expect(pillWords({ id: 'c', state: 'needs_you', mode: 'act', ask: { kind: 'card', id: 'k', text: 'Send to httpbin.org', origin: null, pending_id: 'p' } })).toBe('Needs you · Send to httpbin.org?');
    expect(activeTask(state())).toBeNull();
  });

  it('maps a click on the letterboxed picture to the page and keeps it inside', () => {
    const box = { left: 10, top: 20, width: 640, height: 400 };
    expect(pagePoint(330, 220, box, { width: 1280, height: 800 })).toEqual({ x: 640, y: 400 });
    expect(pagePoint(-50, 900, box, { width: 1280, height: 800 })).toEqual({ x: 0, y: 800 });
    // A wide box: the picture sits in its middle, with bands at the sides.
    const wide = { left: 0, top: 0, width: 1000, height: 400 };
    expect(pagePoint(500, 200, wide, { width: 1280, height: 800 })).toEqual({ x: 640, y: 400 });
    expect(pagePoint(180, 0, wide, { width: 1280, height: 800 })).toEqual({ x: 0, y: 0 });
  });

  it('the pill speaks for her browser open with no task, and the title bar says the state', () => {
    expect(pillTarget(state())).toBeNull();
    expect(pillTarget(state({ free: { id: 'x', state: 'working', mode: 'research' } }))).toEqual({ id: 'free', words: 'Her browser is open', needsYou: false });
    expect(stateWords(null, false)).toBe('Nothing running');
    expect(stateWords({ id: 'a', state: 'needs_you', mode: 'research' }, false)).toBe('Needs you');
    expect(stateWords({ id: 'a', state: 'working', mode: 'research', in_control: true }, false)).toBe('You have control');
  });

  it('sends plain keys and letters, never a shortcut', () => {
    expect(keyInput({ key: 'Enter', ctrlKey: false, metaKey: false, altKey: false })).toEqual({ kind: 'key', key: 'Enter' });
    expect(keyInput({ key: 'a', ctrlKey: false, metaKey: false, altKey: false })).toEqual({ kind: 'text', text: 'a' });
    expect(keyInput({ key: 'q', ctrlKey: false, metaKey: true, altKey: false })).toBeNull();
    expect(keyInput({ key: 'F5', ctrlKey: false, metaKey: false, altKey: false })).toBeNull();
  });

  it('says an error in plain words, never a stack trace', async () => {
    expect(await reason(json({ error: 'her browser isn’t set up yet' }, 409))).toBe('her browser isn’t set up yet');
    expect(await reason(new Response('Traceback (most recent call last): ...', { status: 500 }))).toBe('That didn’t work. Try again in a moment.');
  });
});

describe('Settings → Her browser', () => {
  it('offers to set it up, and says it is never your browser', async () => {
    const section = host();
    const calls: string[] = [];
    const api = async (method: string, path: string) => {
      calls.push(`${method} ${path}`);
      return json(state({ installed: false }));
    };
    const b = createHerBrowser(section, api, document.body);
    await b.section();
    const form = section.children[0] as HTMLElement;
    expect(form.textContent).toContain('never your browser');
    const get = [...form.querySelectorAll('button')].find((x) => x.textContent?.startsWith('Get her a browser of her own (about 150 MB)'));
    expect(get).toBeTruthy();
    get!.click();
    // Said at once, before the server answers.
    expect((section.children[0] as HTMLElement).textContent).toContain('Getting her browser…');
    await flush();
    expect(calls).toContain('POST /api/browser/install');
    expect(b.summary()).toBe('Not set up');
  });

  it('lists signed-in sites with Remove, and the switches start off', async () => {
    const section = host();
    const api = async () => json(state({ sites: ['library.org'] }));
    const b = createHerBrowser(section, api, document.body);
    await b.section();
    const form = section.children[0] as HTMLElement;
    expect(form.textContent).toContain('library.org');
    expect([...form.querySelectorAll('button')].some((x) => x.textContent === 'Remove')).toBe(true);
    const boxes = [...form.querySelectorAll('input[type=checkbox]')] as HTMLInputElement[];
    // Act mode, the stronger model and opening any site start off; the live picture on.
    expect(boxes.map((x) => x.checked)).toEqual([false, false, false, true]);
    expect(form.textContent).toContain('Let her open any site without asking');
    expect(form.textContent).toContain('Pages on this computer and your network stay refused');
    expect(b.summary()).toBe('Signed in to 1');
  });
});

describe('the view', () => {
  it('shows a card whole, says it is on screen, and approves only on a click', async () => {
    const calls: string[] = [];
    const task = {
      id: 'abcdef0123456789',
      state: 'needs_you' as const,
      mode: 'act' as const,
      url: 'https://example.com/form',
      ask: { kind: 'card' as const, id: 'k', text: 'Send to example.com', origin: 'https://example.com', pending_id: 'p1' },
    };
    const api = async (method: string, path: string) => {
      calls.push(`${method} ${path}`);
      if (path.endsWith('/card')) return json({ summary: 'Send to example.com', detail: 'POST https://example.com/enquiry\n  name: Kim', expires_at: 0 });
      return json(state({ tasks: [task] }));
    };
    const b = createHerBrowser(host(), api, document.body);
    await b.view(task.id);
    const wrap = b.overlay;
    expect(wrap.querySelector('pre')?.textContent).toBe('POST https://example.com/enquiry\n  name: Kim');
    expect(calls).toContain('POST /api/browser/tasks/abcdef0123456789/cards/p1/shown');
    expect(calls.some((c) => c === 'POST /api/browser/tasks/abcdef0123456789/cards/p1')).toBe(false);
    const approve = [...wrap.querySelectorAll('button')].find((x) => x.textContent === 'Approve and send');
    approve!.click();
    await flush();
    expect(calls).toContain('POST /api/browser/tasks/abcdef0123456789/cards/p1');
  });

  it('offers Take control while she works, and I’m done once the user has it', async () => {
    let inControl = false;
    const api = async () =>
      json(state({ tasks: [{ id: 'abcdef0123456789', state: 'working', mode: 'research', url: 'https://example.com/', in_control: inControl }] }));
    const b = createHerBrowser(host(), api, document.body);
    await b.view('abcdef0123456789');
    let labels = [...b.overlay.querySelectorAll('button')].map((x) => x.textContent);
    expect(labels).toContain('Take control');
    expect(labels).toContain('Stop');
    inControl = true;
    await b.view('abcdef0123456789');
    labels = [...b.overlay.querySelectorAll('button')].map((x) => x.textContent);
    expect(labels).toContain('I’m done');
    expect(labels).toContain('Keep me signed in here');
  });
});

describe('the overlay', () => {
  it('opens above everything, closes on Esc, and closing never stops the task', async () => {
    // One app has one of these; the tests above left theirs open.
    for (const d of document.querySelectorAll('.her-browser-overlay')) d.remove();
    const calls: string[] = [];
    const api = async (method: string, path: string) => {
      calls.push(`${method} ${path}`);
      return json(state({ tasks: [{ id: 'abcdef0123456789', state: 'working', mode: 'research', url: 'https://example.com/', origin: 'https://example.com' }] }));
    };
    const b = createHerBrowser(host(), api, document.body);
    await b.view('abcdef0123456789');
    expect(b.overlay.hasAttribute('open')).toBe(true);
    expect(b.overlay.querySelector('.her-browser-site-name')?.textContent).toBe('example.com');
    expect(b.overlay.querySelector('.her-browser-state')?.textContent).toBe('Working');
    b.overlay.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(b.overlay.hasAttribute('open')).toBe(false);
    expect(calls.some((c) => c.includes('/stop'))).toBe(false);
  });
});

describe('a card in the window', () => {
  it('is drawn once, however many draws overlap, and is reported on screen once', async () => {
    for (const d of document.querySelectorAll('.her-browser-overlay')) d.remove();
    const calls: string[] = [];
    const task = {
      id: 'abcdef0123456789',
      state: 'needs_you' as const,
      mode: 'act' as const,
      url: 'https://httpbin.org/forms/post',
      ask: { kind: 'card' as const, id: 'k', text: 'Send to httpbin.org', origin: 'https://httpbin.org', pending_id: 'p9' },
    };
    const api = async (method: string, path: string) => {
      calls.push(`${method} ${path}`);
      if (path.endsWith('/card')) {
        await flush();
        return json({ summary: 'Send to httpbin.org', detail: 'POST https://httpbin.org/post\n  custname: Kim', expires_at: 0 });
      }
      return json(state({ tasks: [task] }));
    };
    const b = createHerBrowser(host(), api, document.body);
    await Promise.all([b.view(task.id), b.view(task.id), b.refresh()]);
    await flush();
    const approve = [...b.overlay.querySelectorAll('button')].filter((x) => x.textContent === 'Approve and send');
    expect(approve).toHaveLength(1);
    expect(b.overlay.querySelectorAll('pre')).toHaveLength(1);
    expect(calls.filter((c) => c.endsWith('/cards/p9/shown'))).toHaveLength(1);
  });
});

describe('when a task has ended', () => {
  it('the window says how it ended, never “Starting…”', async () => {
    for (const d of document.querySelectorAll('.her-browser-overlay')) d.remove();
    expect(stateWords({ id: 'a', state: 'done', mode: 'act', outcome: 'expired' }, false)).toBe('Ran out of time: nothing was sent');
    expect(stateWords({ id: 'a', state: 'done', mode: 'act', outcome: 'declined' }, false)).toBe('Declined: nothing was sent');
    const api = async () => json(state({ tasks: [{ id: 'abcdef0123456789', state: 'done', mode: 'act', outcome: 'answered' }] }));
    const b = createHerBrowser(host(), api, document.body);
    await b.view('abcdef0123456789');
    expect(b.overlay.querySelector('.her-browser-state')?.textContent).toBe('Finished');
    expect(b.overlay.querySelector('.her-browser-waiting')?.textContent).toBe('This task has ended.');
    const labels = [...b.overlay.querySelectorAll('button')].map((x) => x.textContent);
    expect(labels).not.toContain('Take control');
    expect(labels).not.toContain('Stop');
  });
});

describe('Settings → Her browser while a helper works', () => {
  // A helper at work: the page polls every few seconds, and a poll must never rebuild
  // the section under the user.
  const working = { id: 'abcdef0123456789', state: 'working' as const, mode: 'research' as const, url: 'https://example.com/' };
  const engines = [
    { id: 'bing', name: 'Bing' },
    { id: 'google', name: 'Google' },
  ];

  function setup() {
    for (const d of document.querySelectorAll('.her-browser-overlay, .her-browser-pill')) d.remove();
    const section = host();
    // In the document, as in the app, so the focus can be inside it.
    const place = document.createElement('div');
    document.body.append(place);
    const replace = section.replaceChildren;
    section.replaceChildren = (...nodes: (Node | string)[]) => {
      replace(...nodes);
      place.replaceChildren(...nodes);
    };
    const puts: unknown[] = [];
    let settings = { act: false, stronger: false, search: 'bing', live: true, open_any: false };
    const api = async (method: string, path: string, body?: unknown) => {
      if (method === 'PUT' && path === '/api/browser/settings') {
        puts.push(body);
        settings = { ...settings, ...(body as object) };
      }
      return json(state({ engines, settings, tasks: [working] }));
    };
    const b = createHerBrowser(section, api, document.body);
    return { section, b, puts };
  }

  it('keeps an open search engine select through a poll, and saves the choice', async () => {
    const { section, b, puts } = setup();
    await b.section();
    const form = section.children[0] as HTMLElement;
    const pick = form.querySelector('select') as HTMLSelectElement;
    expect(pick.value).toBe('bing');
    pick.focus();
    expect(document.activeElement).toBe(pick);
    await b.refresh();
    await b.refresh();
    // The same form and the same select: nothing was rebuilt under the user.
    expect(section.children[0]).toBe(form);
    expect(form.querySelector('select')).toBe(pick);
    expect(pick.isConnected).toBe(true);
    pick.value = 'google';
    pick.dispatchEvent(new Event('change'));
    await flush();
    await flush();
    expect(puts).toEqual([{ search: 'google' }]);
    // What the user did is drawn: the new choice shows.
    expect((section.children[0] as HTMLElement).querySelector('select')?.value).toBe('google');
  });

  it('keeps the checkboxes through a poll, and saves each change', async () => {
    const { section, b, puts } = setup();
    await b.section();
    const form = section.children[0] as HTMLElement;
    const first = form.querySelectorAll('input[type=checkbox]')[0] as HTMLInputElement;
    first.focus();
    await b.refresh();
    expect(section.children[0]).toBe(form);
    expect(document.activeElement).toBe(first);
    first.checked = true;
    first.dispatchEvent(new Event('change'));
    await flush();
    await flush();
    expect(puts).toEqual([{ act: true }]);
    // Not focused, and nothing changed: a poll still leaves it be.
    const now = section.children[0] as HTMLElement;
    (document.activeElement as HTMLElement | null)?.blur();
    await b.refresh();
    expect(section.children[0]).toBe(now);
    const any = [...now.querySelectorAll('label')].find((x) => x.textContent?.includes('open any site'))?.querySelector('input') as HTMLInputElement;
    any.checked = true;
    any.dispatchEvent(new Event('change'));
    await flush();
    await flush();
    expect(puts).toEqual([{ act: true }, { open_any: true }]);
  });

  it('draws the section again on a poll when what it shows changed', async () => {
    for (const d of document.querySelectorAll('.her-browser-overlay, .her-browser-pill')) d.remove();
    const section = host();
    let sites: string[] = [];
    const api = async () => json(state({ sites, tasks: [working] }));
    const b = createHerBrowser(section, api, document.body);
    await b.section();
    const form = section.children[0];
    await b.refresh();
    expect(section.children[0]).toBe(form);
    sites = ['library.org'];
    await b.refresh();
    expect(section.children[0]).not.toBe(form);
    expect((section.children[0] as HTMLElement).textContent).toContain('library.org');
  });
});

describe('Settings → Her browser: the Brave Search API key', () => {
  const working = { id: 'abcdef0123456789', state: 'working' as const, mode: 'research' as const, url: 'https://example.com/' };
  const engines = [
    { id: 'bing', name: 'Bing' },
    { id: 'brave_api', name: 'Brave Search API (your key)' },
  ];
  const brave = (saved: boolean) => ({ engine: 'brave_api', saved, keychain: true, key_link: 'https://api-dashboard.search.brave.com/' });

  function setup(search: string, saved = false) {
    for (const d of document.querySelectorAll('.her-browser-overlay, .her-browser-pill')) d.remove();
    const section = host();
    const place = document.createElement('div');
    document.body.append(place);
    const replace = section.replaceChildren;
    section.replaceChildren = (...nodes: (Node | string)[]) => {
      replace(...nodes);
      place.replaceChildren(...nodes);
    };
    const calls: { method: string; path: string; body: unknown }[] = [];
    const now = { saved, sites: [] as string[] };
    const api = async (method: string, path: string, body?: unknown) => {
      if (method !== 'GET') calls.push({ method, path, body });
      if (path === '/api/browser/brave-key') now.saved = method === 'PUT';
      return json(state({ engines, settings: { act: false, stronger: false, search, live: true }, sites: now.sites, brave: brave(now.saved), tasks: [working] }));
    };
    const b = createHerBrowser(section, api, document.body);
    return { section, b, calls, now };
  }

  const keyField = (section: Host & { children: Node[] }) => (section.children[0] as HTMLElement).querySelector('#her-browser-brave-key') as HTMLInputElement | null;

  it('shows a key field with Save, a link and the note when the API engine is chosen', async () => {
    const { section, b } = setup('brave_api');
    await b.section();
    const field = keyField(section);
    expect(field).not.toBeNull();
    expect(field?.type).toBe('password');
    const text = (section.children[0] as HTMLElement).textContent ?? '';
    expect(text).toContain('Searches go to Brave with your key');
    expect(text).toContain('monthly limit');
    const link = (section.children[0] as HTMLElement).querySelector('a[href="https://api-dashboard.search.brave.com/"]');
    expect(link).not.toBeNull();
  });

  it('Save sends the key to the keychain route, then forgets it here; Remove deletes it', async () => {
    const { section, b, calls } = setup('brave_api');
    await b.section();
    const field = keyField(section)!;
    field.value = 'BSA-test-key-0000';
    field.dispatchEvent(new Event('input'));
    const save = [...(section.children[0] as HTMLElement).querySelectorAll('button')].find((x) => x.textContent === 'Save')!;
    save.click();
    await flush();
    await flush();
    expect(calls).toEqual([{ method: 'PUT', path: '/api/browser/brave-key', body: { key: 'BSA-test-key-0000' } }]);
    // Saved: the field is empty again and the section says a key is kept.
    expect(keyField(section)?.value).toBe('');
    expect((section.children[0] as HTMLElement).textContent).toContain('A key is saved in your keychain');
    const remove = [...(section.children[0] as HTMLElement).querySelectorAll('button')].find((x) => x.textContent === 'Remove')!;
    remove.click();
    await flush();
    await flush();
    expect(calls[1]).toEqual({ method: 'DELETE', path: '/api/browser/brave-key', body: {} });
  });

  it('keeps a half-typed key through a poll', async () => {
    const { section, b, now } = setup('brave_api');
    await b.section();
    const form = section.children[0] as HTMLElement;
    const field = keyField(section)!;
    field.focus();
    field.value = 'BSA-half';
    field.dispatchEvent(new Event('input'));
    // Something the section shows changes while the user types: the poll leaves it be.
    now.sites = ['library.org'];
    await b.refresh();
    expect(section.children[0]).toBe(form);
    expect(document.activeElement).toBe(field);
    expect(field.value).toBe('BSA-half');
    // Even once the focus has gone, a redraw keeps what was typed.
    field.blur();
    await b.refresh();
    expect(section.children[0]).not.toBe(form);
    expect(keyField(section)?.value).toBe('BSA-half');
  });

  it('with another engine and a key saved, offers only Remove', async () => {
    const { section, b } = setup('bing', true);
    await b.section();
    expect(keyField(section)).toBeNull();
    expect((section.children[0] as HTMLElement).textContent).toContain('A Brave Search API key is saved');
  });
});

describe('a chip in the window', () => {
  function chipTask(kind: 'chip' | 'signin') {
    return {
      id: 'abcdef0123456789',
      state: 'needs_you' as const,
      mode: 'research' as const,
      url: 'https://www.bing.com/search?q=x',
      ask: { kind, id: 'a1b2c3', text: kind === 'chip' ? 'Open www.pcmag.com?' : 'Use your sign-in for library.org?', origin: null, pending_id: null },
    };
  }

  it('offers Allow, Always allow and Not now, and Always allow says so to the server', async () => {
    for (const d of document.querySelectorAll('.her-browser-overlay')) d.remove();
    const calls: { method: string; path: string; body: unknown }[] = [];
    const api = async (method: string, path: string, body?: unknown) => {
      calls.push({ method, path, body });
      return json(state({ tasks: [chipTask('chip')] }));
    };
    const b = createHerBrowser(host(), api, document.body);
    await b.view('abcdef0123456789');
    const buttons = [...b.overlay.querySelectorAll('.her-browser-ask button')];
    expect(buttons.map((x) => x.textContent)).toEqual(['Allow', 'Always allow', 'Not now']);
    expect((buttons[1] as HTMLElement).title).toContain('nothing is sent without your OK');
    (buttons[1] as HTMLButtonElement).click();
    await flush();
    expect(calls).toContainEqual({ method: 'POST', path: '/api/browser/tasks/abcdef0123456789/asks/a1b2c3', body: { answer: 'always' } });
  });

  it('never offers Always allow for a saved sign-in', async () => {
    for (const d of document.querySelectorAll('.her-browser-overlay')) d.remove();
    const api = async () => json(state({ tasks: [chipTask('signin')] }));
    const b = createHerBrowser(host(), api, document.body);
    await b.view('abcdef0123456789');
    const labels = [...b.overlay.querySelectorAll('.her-browser-ask button')].map((x) => x.textContent);
    expect(labels).toEqual(['Use it', 'Not now']);
  });
});
