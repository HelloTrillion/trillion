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
    expect(pillWords(activeTask(s)!)).toBe('Open example.org?');
    expect(pillWords(s.tasks[0]!)).toBe('Browsing example.com');
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
    expect(boxes.map((x) => x.checked)).toEqual([false, false, true]);
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
