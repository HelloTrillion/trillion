// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PickerMissing, pickFolder } from '../src/desktop';
import { escapeAction } from '../src/threads';
import {
  type FolderFrame,
  type ShareRow,
  type SharesState,
  PRIVACY,
  accessText,
  baseName,
  createFolderPanel,
  createShares,
  createSharedFolders,
  footText,
  joinPath,
  nameProblem,
  parseFrame,
  samePath,
  sharesSummary,
  shownText,
  stateText,
  upTarget,
  whoText,
} from '../src/folders';
import type { Host } from '../src/panel';

const flush = async (): Promise<void> => {
  for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0));
};

const json = (body: unknown, status = 200): Response => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

const share = (over: Partial<ShareRow> = {}): ShareRow => ({
  id: 'f_1',
  name: 'Website project',
  path: '/Users/sam/Documents/Clients/Website',
  access: 'change',
  scope: 'everyone',
  routine: null,
  state: null,
  run_only: false,
  ...over,
});

const DOCS: FolderFrame = {
  path: '/Users/sam/Documents',
  crumbs: [
    { label: 'Home', path: '/Users/sam' },
    { label: 'Documents', path: '/Users/sam/Documents' },
  ],
  folders: ['Clients', 'Taxes'],
  more: 0,
  shared: null,
  places: null,
};

const CLIENTS: FolderFrame = {
  path: '/Users/sam/Documents/Clients',
  crumbs: [...DOCS.crumbs, { label: 'Clients', path: '/Users/sam/Documents/Clients' }],
  folders: ['Website', 'Shop'],
  more: 3,
  shared: null,
  places: null,
};

const PLACES: FolderFrame = {
  path: null,
  crumbs: [],
  folders: [],
  more: 0,
  shared: null,
  places: [
    { label: 'Home', path: '/Users/sam', kind: 'home' },
    { label: 'Documents', path: '/Users/sam/Documents', kind: 'folder' },
    { label: 'Backup', path: '/Volumes/Backup', kind: 'drive' },
  ],
};

interface Call {
  method: string;
  path: string;
  body: unknown;
}

function fakeHost(root: HTMLElement): Host {
  const host = {
    open: false,
    showModal() {
      host.open = true;
    },
    close() {
      host.open = false;
    },
    replaceChildren: (...nodes: (Node | string)[]) => root.replaceChildren(...nodes),
    setAttribute: (name: string, value: string) => root.setAttribute(name, value),
    addEventListener: () => undefined,
  };
  return host;
}

function server(initial: SharesState = { shares: [], problem: null }) {
  let state = initial;
  const calls: Call[] = [];
  const browse = new Map<string, FolderFrame>([
    ['/Users/sam/Documents', DOCS],
    ['/Users/sam/Documents/Clients', CLIENTS],
    ['places', PLACES],
  ]);
  const api = (method: string, path: string, body?: unknown): Promise<Response> => {
    calls.push({ method, path, body });
    const b = (body ?? {}) as Record<string, string>;
    if (path === '/api/folders' && method === 'GET') return Promise.resolve(json(state));
    if (path === '/api/folders' && method === 'POST') {
      if (b['path'] === '/Users/sam/Documents/Taxes') return Promise.resolve(json({ error: 'refused', detail: 'that folder holds your keys' }, 400));
      const row = share({ id: b['id'] ?? 'f_new', name: b['name'] ?? 'x', path: b['path'] ?? state.shares.find((s) => s.id === b['id'])?.path ?? '', access: (b['access'] ?? 'read') as ShareRow['access'], scope: b['scope'] ?? 'everyone' });
      state = { ...state, shares: [...state.shares.filter((s) => s.id !== row.id), row] };
      return Promise.resolve(json(state));
    }
    if (path.startsWith('/api/folders/') && method === 'DELETE') {
      const id = decodeURIComponent(path.slice('/api/folders/'.length));
      state = { ...state, shares: state.shares.filter((s) => s.id !== id) };
      return Promise.resolve(json(state));
    }
    if (path === '/api/folders/browse') {
      const f = browse.get(b['path'] ?? b['place'] ?? '');
      return Promise.resolve(f ? json({ type: 'folders', ...f }) : json({ error: 'refused', detail: 'that folder is outside what the browser can open' }, 400));
    }
    if (path === '/api/routines') return Promise.resolve(json({ routines: [{ id: 'r1', name: 'Morning brief' }] }));
    return Promise.resolve(json({ error: 'not_found' }, 404));
  };
  return { api, calls, get state() {
    return state;
  } };
}

function panel(initial?: SharesState) {
  const s = server(initial);
  const shares = createShares(s.api);
  const shown = vi.fn();
  const p = createFolderPanel({ api: s.api, shares, routines: async () => [{ id: 'r1', title: 'Morning brief' }], conversation: () => 'c1', shown });
  document.body.append(p.el, p.live);
  return { ...s, shares, p, shown };
}

const rows = (root: HTMLElement): HTMLButtonElement[] => [...root.querySelectorAll<HTMLButtonElement>('.fo-row')];
const names = (root: HTMLElement): string[] => rows(root).map((b) => b.querySelector('.fo-row-name')?.textContent ?? '');
const live = (_root: HTMLElement): string => document.body.querySelector('[role="status"]')?.textContent ?? '';
const key = (target: Element, k: string): void => void target.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true }));
const button = (root: HTMLElement, text: string): HTMLButtonElement =>
  [...root.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent === text) as HTMLButtonElement;

afterEach(() => {
  document.body.replaceChildren();
  delete (window as unknown as { __TAURI__?: unknown }).__TAURI__;
  vi.restoreAllMocks();
});

describe('shared folders, in words', () => {
  it('says the access, who, and why a share reaches nothing', () => {
    expect(accessText('read')).toBe('Read only');
    expect(accessText('change')).toBe('Read and change');
    expect(whoText(share())).toBe('Everyone');
    expect(whoText(share({ scope: 'routine:r1', routine: { id: 'r1', title: 'Morning brief' } }))).toBe('Only “Morning brief”');
    expect(whoText(share({ scope: 'routine:r9', routine: null }))).toBe('A routine that is gone');
    expect(stateText(share({ state: 'not found' }))).toBe('Not found');
    expect(stateText(share({ state: 'moved or replaced by a link' }))).toBe('Moved or replaced by a link');
    expect(stateText(share())).toBe('');
  });

  it('sums them up from the routes, not the session', () => {
    expect(sharesSummary(null)).toBe('');
    expect(sharesSummary({ shares: [], problem: null })).toBe('None');
    expect(sharesSummary({ shares: [share(), share({ id: 'f_2' })], problem: null })).toBe('2 shared');
  });

  it('tells the footer what her own turns reach, and how to share one when none', () => {
    expect(footText({ shares: [share(), share({ id: 'f_2', name: 'Gone', state: 'not found' }), share({ id: 'f_3', name: 'Brief', scope: 'routine:r1' })], problem: null })).toBe(
      'Files: Website project. Every change asks first.',
    );
    // Not read yet: nothing is guessed.
    expect(footText(null)).toBe('');
    const none = footText({ shares: [], problem: null });
    expect(none).toContain('Settings → Shared folders, or ask her');
    expect(none).not.toContain('--grant');
  });

  it('compares paths as the core does: case-blind on macOS and Windows, exact elsewhere', () => {
    expect(samePath('/Users/sam/Notes', '/Users/sam/notes')).toBe(true);
    expect(samePath('C:\\Users\\sam\\Notes', 'c:\\users\\sam\\notes')).toBe(true);
    expect(samePath('/home/sam/Notes', '/home/sam/notes')).toBe(false);
    expect(samePath('/home/sam/Notes', '/home/sam/Notes')).toBe(true);
  });

  it('joins and splits paths with their own separator', () => {
    expect(joinPath('/Users/sam', 'Documents')).toBe('/Users/sam/Documents');
    expect(joinPath('/', 'mnt')).toBe('/mnt');
    expect(joinPath('C:\\Users\\sam', 'Documents')).toBe('C:\\Users\\sam\\Documents');
    expect(joinPath('D:\\', 'Projects')).toBe('D:\\Projects');
    expect(baseName('/Users/sam/Documents/')).toBe('Documents');
    expect(baseName('C:\\Users\\sam')).toBe('sam');
  });

  it('goes up to the folder above, or to the places list from a place', () => {
    expect(upTarget(CLIENTS)).toEqual({ path: '/Users/sam/Documents' });
    expect(upTarget({ ...DOCS, crumbs: [{ label: 'Backup', path: '/Volumes/Backup' }], path: '/Volumes/Backup' })).toEqual({ place: 'places' });
    expect(upTarget(PLACES)).toBeNull();
  });

  it('checks a frame field by field', () => {
    expect(parseFrame({ type: 'board' })).toBeNull();
    const f = parseFrame({ type: 'folders', path: null, crumbs: [], folders: [], more: 0, shared: null, places: [{ label: 'Home', path: '/h', kind: 'weird' }, { label: 'x' }] });
    expect(f?.places).toEqual([{ label: 'Home', path: '/h', kind: 'folder' }]);
    expect(parseFrame({ type: 'folders', path: '/a', crumbs: [{ label: 'a', path: '/a' }], folders: ['b', 3, ''], more: -2, shared: 'f_1', places: null })).toEqual({
      path: '/a',
      crumbs: [{ label: 'a', path: '/a' }],
      folders: ['b'],
      more: 0,
      shared: 'f_1',
      places: null,
    });
  });

  it('checks a name before it is sent', () => {
    expect(nameProblem('  ')).toBe('Give it a name.');
    expect(nameProblem('x'.repeat(61))).toContain('60');
    expect(nameProblem('Website')).toBe('');
  });

  it('says in the live region what is shown', () => {
    expect(shownText(DOCS)).toBe('Showing Documents: 2 folders.');
    expect(shownText({ ...CLIENTS, folders: ['a'], more: 0 })).toBe('Showing Clients: 1 folder.');
    expect(shownText(PLACES)).toBe('Showing your places: 3 places.');
  });
});

describe('the Folders panel', () => {
  it('is drawn from a frame: breadcrumb, rows with a folder icon, the more line, its actions', () => {
    const { p, shown } = panel();
    expect(p.el.hidden).toBe(true);
    p.take(CLIENTS);
    expect(p.el.hidden).toBe(false);
    expect(shown).toHaveBeenCalledWith(true);
    const crumbs = [...p.el.querySelectorAll('.fo-crumb')].map((c) => c.textContent);
    expect(crumbs).toEqual(['Places', 'Home', 'Documents', 'Clients']);
    expect(p.el.querySelector('[aria-current="location"]')?.textContent).toBe('Clients');
    expect(names(p.el)).toEqual(['Website', 'Shop']);
    expect(rows(p.el)[0]?.querySelector('svg.icon')).not.toBeNull();
    expect(p.el.querySelector('.fo-more')?.textContent).toBe('and 3 more');
    expect(button(p.el, 'Share this folder…').hidden).toBe(false);
    expect(button(p.el, 'Close')).toBeTruthy();
    expect(live(p.el)).toBe('Showing Clients: 5 folders.');
  });

  it('sets folder names as text, never markup', () => {
    const { p } = panel();
    p.take({ ...DOCS, folders: ['<img src=x onerror=alert(1)>'] });
    expect(p.el.querySelector('img')).toBeNull();
    expect(names(p.el)).toEqual(['<img src=x onerror=alert(1)>']);
  });

  it('tags the folders already shared, and the one shown', async () => {
    const { p, shares } = panel({ shares: [share()], problem: null });
    await shares.load();
    p.take(CLIENTS);
    expect(rows(p.el)[0]?.querySelector('.fo-row-tag')?.textContent).toBe('Shared as Website project');
    expect(rows(p.el)[1]?.querySelector('.fo-row-tag')).toBeNull();
    p.take({ ...CLIENTS, path: share().path, crumbs: [...CLIENTS.crumbs, { label: 'Website', path: share().path }], folders: [], more: 0, shared: 'f_1' });
    expect(p.el.querySelector('.fo-tag')?.textContent).toBe('Shared as Website project');
    expect(p.el.querySelector('.fo-empty')?.textContent).toBe('No folders in here.');
  });

  it('opens a row through the browse route, with this conversation, and draws the answer once', async () => {
    const { p, calls } = panel();
    p.take(DOCS);
    const before = p.el.querySelector('.fo-rows');
    rows(p.el)[0]?.click();
    await flush();
    expect(calls.filter((c) => c.path === '/api/folders/browse')).toEqual([
      { method: 'POST', path: '/api/folders/browse', body: { path: '/Users/sam/Documents/Clients', conversation: 'c1' } },
    ]);
    expect(names(p.el)).toEqual(['Website', 'Shop']);
    expect(document.activeElement).toBe(rows(p.el)[0]);
    // The session pushes the same frame: nothing is drawn again.
    const drawn = rows(p.el)[0];
    p.take(CLIENTS);
    expect(rows(p.el)[0]).toBe(drawn);
    expect(p.el.querySelector('.fo-rows')).toBe(before);
  });

  it('opens a crumb and a place', async () => {
    const { p, calls } = panel();
    p.take(CLIENTS);
    button(p.el, 'Documents').click();
    await flush();
    expect(names(p.el)).toEqual(['Clients', 'Taxes']);
    button(p.el, 'Places').click();
    await flush();
    expect(names(p.el)).toEqual(['Home', 'Documents', 'Backup']);
    expect(rows(p.el)[2]?.querySelector('svg path')).not.toBeNull();
    expect(button(p.el, 'Share this folder…').hidden).toBe(true);
    expect(live(p.el)).toBe('Showing your places: 3 places.');
    rows(p.el)[1]?.click();
    await flush();
    expect(calls.at(-1)?.body).toEqual({ path: '/Users/sam/Documents', conversation: 'c1' });
    expect(live(p.el)).toBe('Showing Documents: 2 folders.');
  });

  it('says a refusal plainly and stays where it was', async () => {
    const { p } = panel();
    p.take({ ...DOCS, folders: ['Nope'] });
    rows(p.el)[0]?.click();
    await flush();
    expect(p.el.querySelector('.fo-problem')?.textContent).toBe('That folder is outside what the browser can open.');
    expect(names(p.el)).toEqual(['Nope']);
  });

  it('moves by keyboard: arrows between rows, Enter opens, Backspace goes up, Escape closes', async () => {
    const { p, shown } = panel();
    p.take(CLIENTS, { focus: true });
    const [first, second] = rows(p.el);
    expect(document.activeElement).toBe(first);
    key(first as HTMLElement, 'ArrowDown');
    expect(document.activeElement).toBe(second);
    expect(second?.tabIndex).toBe(0);
    expect(first?.tabIndex).toBe(-1);
    key(second as HTMLElement, 'ArrowDown');
    expect(document.activeElement).toBe(second);
    key(second as HTMLElement, 'ArrowUp');
    expect(document.activeElement).toBe(first);
    // Enter on a row is its button's own click.
    key(first as HTMLElement, 'Backspace');
    await flush();
    expect(names(p.el)).toEqual(['Clients', 'Taxes']);
    // Up lands on the folder it came from.
    expect(document.activeElement?.textContent).toBe('Clients');
    // Escape goes on to the page, which calls escape() in its turn.
    const outside = vi.fn();
    window.addEventListener('keydown', outside);
    key(document.activeElement as HTMLElement, 'Escape');
    window.removeEventListener('keydown', outside);
    expect(outside).toHaveBeenCalledTimes(1);
    expect(p.isOpen).toBe(true);
    p.escape();
    expect(p.isOpen).toBe(false);
    expect(p.el.hidden).toBe(true);
    expect(shown).toHaveBeenLastCalledWith(false);
  });

  it('keeps Escape’s order: she stops first, then the thread list, then the folders, then the workspace', () => {
    const idle = { busy: false, speaking: false, listening: false, listOpen: false, foldersOpen: false, workspaceOpen: false };
    expect(escapeAction({ ...idle, speaking: true, foldersOpen: true, workspaceOpen: true })).toBe('interrupt');
    expect(escapeAction({ ...idle, listOpen: true, foldersOpen: true })).toBe('list');
    expect(escapeAction({ ...idle, foldersOpen: true, workspaceOpen: true })).toBe('folders');
    expect(escapeAction({ ...idle, workspaceOpen: true })).toBe('workspace');
  });

  it('hands focus to where the page says before it hides', () => {
    const s = server();
    const after = document.createElement('button');
    let seen: Element | null = null;
    const p = createFolderPanel({ api: s.api, shares: createShares(s.api), routines: async () => [], conversation: () => null, focusAfter: () => after, shown: (open) => void (open || (seen = document.activeElement)) });
    document.body.append(p.el, p.live, after);
    p.take(DOCS, { focus: true });
    expect(p.el.contains(document.activeElement)).toBe(true);
    p.close();
    expect(document.activeElement).toBe(after);
    expect(seen).toBe(after);
  });

  it('announces the first frame from a live region outside the hidden panel', () => {
    const { p } = panel();
    expect(p.el.contains(p.live)).toBe(false);
    expect(p.live.getAttribute('aria-live')).toBe('polite');
    p.take(PLACES);
    expect(p.live.textContent).toBe('Showing your places: 3 places.');
  });

  it('draws the later of two clicks when the first answers last', async () => {
    const s = server();
    const waits: ((r: Response) => void)[] = [];
    const api = (method: string, path: string, body?: unknown): Promise<Response> =>
      path === '/api/folders/browse' ? new Promise<Response>((res) => waits.push(res)) : s.api(method, path, body);
    const p = createFolderPanel({ api, shares: createShares(api), routines: async () => [], conversation: () => null });
    document.body.append(p.el, p.live);
    p.take(DOCS);
    rows(p.el)[0]?.click(); // Clients
    rows(p.el)[1]?.click(); // Taxes
    waits[1]?.(json({ type: 'folders', ...DOCS, path: '/Users/sam/Documents/Taxes', folders: ['2025'] }));
    await flush();
    waits[0]?.(json({ type: 'folders', ...CLIENTS }));
    await flush();
    expect(names(p.el)).toEqual(['2025']);
  });

  it('tries again without the conversation when its session is gone', async () => {
    const s = server();
    const api = (method: string, path: string, body?: unknown): Promise<Response> =>
      path === '/api/folders/browse' && (body as Record<string, unknown>)['conversation'] ? Promise.resolve(json({ error: 'not_found' }, 404)) : s.api(method, path, body);
    const p = createFolderPanel({ api, shares: createShares(api), routines: async () => [], conversation: () => 'c-gone' });
    document.body.append(p.el, p.live);
    expect(await p.openPlaces()).toBe('');
    expect(names(p.el)).toEqual(['Home', 'Documents', 'Backup']);
  });

  it('opens anyway, saying why, when Add a folder’s places list is refused', async () => {
    const api = (): Promise<Response> => Promise.resolve(json({ error: 'refused', detail: 'the places list took too long' }, 400));
    const p = createFolderPanel({ api, shares: createShares(api), routines: async () => [], conversation: () => null });
    document.body.append(p.el, p.live);
    expect(await p.openPlaces()).toBe('The places list took too long.');
    expect(p.isOpen).toBe(true);
    expect(p.el.querySelector('.fo-problem')?.textContent).toBe('The places list took too long.');
    expect(button(p.el, 'Share this folder…').hidden).toBe(true);
  });

  it('leaves Backspace to a field being typed in', () => {
    const { p, calls } = panel();
    p.take(CLIENTS);
    button(p.el, 'Share this folder…').click();
    const name = p.el.querySelector<HTMLInputElement>('.fo-form input') as HTMLInputElement;
    key(name, 'Backspace');
    expect(calls.some((c) => c.path === '/api/folders/browse')).toBe(false);
  });

  it('shares this folder from its form: name prefilled, access, who; errors inline', async () => {
    const { p, calls, shares } = panel();
    await shares.load();
    p.take(DOCS);
    button(p.el, 'Share this folder…').click();
    await flush();
    const form = p.el.querySelector('.fo-form') as HTMLElement;
    expect(form.querySelector('.fo-form-path')?.textContent).toBe('/Users/sam/Documents');
    const name = form.querySelector<HTMLInputElement>('[data-field="name"]') as HTMLInputElement;
    const access = form.querySelector<HTMLSelectElement>('[data-field="access"]') as HTMLSelectElement;
    const who = form.querySelector<HTMLSelectElement>('[data-field="who"]') as HTMLSelectElement;
    expect(name.value).toBe('Documents');
    expect([...access.options].map((o) => o.textContent)).toEqual(['Read only', 'Read and change']);
    expect(access.value).toBe('read');
    expect([...who.options].map((o) => o.textContent)).toEqual(['Everyone', 'Only “Morning brief”']);
    // Nothing is sent until the user submits.
    expect(calls.some((c) => c.method === 'POST' && c.path === '/api/folders')).toBe(false);
    name.value = '';
    button(form, 'Share').click();
    await flush();
    expect(form.querySelector('.fo-error')?.textContent).toBe('Give it a name.');
    name.value = 'Docs';
    access.value = 'change';
    who.value = 'routine:r1';
    button(form, 'Share').click();
    await flush();
    expect(calls.filter((c) => c.method === 'POST' && c.path === '/api/folders').map((c) => c.body)).toEqual([
      { path: '/Users/sam/Documents', name: 'Docs', access: 'change', scope: 'routine:r1' },
    ]);
    expect(p.el.querySelector('.fo-form')).toBeNull();
    expect(p.el.querySelector('.fo-tag')?.textContent).toBe('Shared as Docs');
    expect(live(p.el)).toBe('Shared as Docs.');
  });

  it('shows the server’s refusal in the form', async () => {
    const { p } = panel();
    p.take({ ...DOCS, path: '/Users/sam/Documents/Taxes', crumbs: [...DOCS.crumbs, { label: 'Taxes', path: '/Users/sam/Documents/Taxes' }] });
    button(p.el, 'Share this folder…').click();
    button(p.el.querySelector('.fo-form') as HTMLElement, 'Share').click();
    await flush();
    expect(p.el.querySelector('.fo-error')?.textContent).toBe('That folder holds your keys.');
    expect(p.el.querySelector('.fo-error')?.getAttribute('role')).toBe('alert');
  });

  it('closes the form, not the panel, on the first Escape', () => {
    const { p } = panel();
    p.take(DOCS);
    button(p.el, 'Share this folder…').click();
    p.escape();
    expect(p.el.querySelector('.fo-form')).toBeNull();
    expect(p.isOpen).toBe(true);
    p.escape();
    expect(p.isOpen).toBe(false);
  });

  it('opens at the places list for Add a folder', async () => {
    const { p, calls } = panel();
    await p.openPlaces();
    expect(calls.at(-1)).toEqual({ method: 'POST', path: '/api/folders/browse', body: { place: 'places', conversation: 'c1' } });
    expect(p.isOpen).toBe(true);
    expect(names(p.el)).toEqual(['Home', 'Documents', 'Backup']);
  });
});

describe('Settings → Shared folders', () => {
  let browseSays = '';
  afterEach(() => {
    browseSays = '';
  });
  function settings(initial: SharesState, pick: (() => Promise<string | null>) | null = null) {
    const s = server(initial);
    const shares = createShares(s.api);
    const root = document.createElement('div');
    document.body.append(root);
    const host = fakeHost(root);
    const browse = vi.fn(async () => browseSays);
    const changed = vi.fn();
    const section = createSharedFolders(host, { shares, routines: async () => [{ id: 'r1', title: 'Morning brief' }], pick, browse, changed });
    return { ...s, root, section, browse, changed, shares };
  }

  it('shows one card per share: name, path, access, who, its state and This run only', async () => {
    const { root, section } = settings({
      shares: [
        share(),
        share({ id: 'f_2', name: 'Notes', path: '/Users/sam/Notes', access: 'read', scope: 'routine:r1', routine: { id: 'r1', title: 'Morning brief' }, state: 'not found' }),
        share({ id: 'f_3', name: 'srv', path: '/srv', run_only: true }),
      ],
      problem: null,
    });
    await section.open();
    const cards = [...root.querySelectorAll<HTMLElement>('.folders-card')];
    expect(cards.map((c) => c.querySelector('strong')?.textContent)).toEqual(['Website project', 'Notes', 'srv']);
    expect(cards[0]?.querySelector('.folders-path')?.textContent).toBe('/Users/sam/Documents/Clients/Website');
    expect(cards[0]?.textContent).toContain('Read and change · Everyone');
    expect(cards[1]?.textContent).toContain('Read only · Only “Morning brief”');
    expect(cards[1]?.querySelector('.folders-state')?.textContent).toBe('Not found');
    expect(cards[0]?.querySelector('.folders-run')).toBeNull();
    expect(cards[2]?.querySelector('.folders-run')?.textContent).toBe('This run only');
    expect(root.querySelector('.folders-privacy')?.textContent).toBe(PRIVACY);
    expect(root.querySelector('.folders-damaged')).toBeNull();
    expect(section.summary()).toBe('3 shared');
  });

  it('says the file is damaged, and where', async () => {
    const line = 'Your shared folders file is damaged, so no folders are shared. It is at /Users/sam/.trillion/folders.json.';
    const { root, section } = settings({ shares: [], problem: line });
    await section.open();
    expect(root.querySelector('.folders-damaged')?.textContent).toBe(line);
    expect(section.summary()).toBe('None');
  });

  it('changes a share: name, access and who', async () => {
    const { root, section, calls, changed } = settings({ shares: [share()], problem: null });
    await section.open();
    button(root, 'Change').click();
    await flush();
    const form = root.querySelector('.fo-form') as HTMLElement;
    const name = form.querySelector<HTMLInputElement>('[data-field="name"]') as HTMLInputElement;
    expect(name.value).toBe('Website project');
    expect((form.querySelector('[data-field="access"]') as HTMLSelectElement).value).toBe('change');
    name.value = 'Site';
    (form.querySelector('[data-field="access"]') as HTMLSelectElement).value = 'read';
    (form.querySelector('[data-field="who"]') as HTMLSelectElement).value = 'routine:r1';
    button(form, 'Save').click();
    await flush();
    expect(calls.filter((c) => c.method === 'POST').map((c) => c.body)).toEqual([{ id: 'f_1', name: 'Site', access: 'read', scope: 'routine:r1' }]);
    expect(root.querySelector('.fo-form')).toBeNull();
    expect(root.querySelector('.folders-card strong')?.textContent).toBe('Site');
    expect(changed).toHaveBeenCalled();
  });

  it('removes a share at once, with a JSON body', async () => {
    const { root, section, calls } = settings({ shares: [share()], problem: null });
    await section.open();
    button(root, 'Remove').click();
    await flush();
    expect(calls.filter((c) => c.method === 'DELETE')).toEqual([{ method: 'DELETE', path: '/api/folders/f_1', body: {} }]);
    expect(root.querySelector('.folders-card')).toBeNull();
    expect(root.querySelector('.settings-empty')?.textContent).toContain('by asking her');
    expect(section.summary()).toBe('None');
  });

  it('adds with the system picker in the desktop app, then the same form', async () => {
    const pick = vi.fn(async () => '/Users/sam/Projects/Garden');
    const { root, section, calls, browse } = settings({ shares: [], problem: null }, pick);
    await section.open();
    button(root, 'Add a folder').click();
    await flush();
    expect(pick).toHaveBeenCalled();
    expect(browse).not.toHaveBeenCalled();
    const form = root.querySelector('.folders-add .fo-form') as HTMLElement;
    expect(form.querySelector('.fo-form-path')?.textContent).toBe('/Users/sam/Projects/Garden');
    expect((form.querySelector('[data-field="name"]') as HTMLInputElement).value).toBe('Garden');
    button(form, 'Share').click();
    await flush();
    expect(calls.filter((c) => c.method === 'POST').map((c) => c.body)).toEqual([{ path: '/Users/sam/Projects/Garden', name: 'Garden', access: 'read', scope: 'everyone' }]);
    expect(root.querySelector('.folders-card strong')?.textContent).toBe('Garden');
  });

  it('does nothing when the picker is cancelled, and falls back to the panel when the shell lacks it', async () => {
    const cancelled = settings({ shares: [], problem: null }, async () => null);
    await cancelled.section.open();
    button(cancelled.root, 'Add a folder').click();
    await flush();
    expect(cancelled.root.querySelector('.fo-form')).toBeNull();
    expect(cancelled.browse).not.toHaveBeenCalled();
    document.body.replaceChildren();
    const old = settings({ shares: [], problem: null }, async () => {
      throw new PickerMissing('Command pick_folder not found');
    });
    await old.section.open();
    button(old.root, 'Add a folder').click();
    await flush();
    expect(old.browse).toHaveBeenCalled();
  });

  it('says what went wrong when the picker itself failed', async () => {
    const { root, section, browse } = settings({ shares: [], problem: null }, async () => {
      throw new Error('the folder picker is already open');
    });
    await section.open();
    button(root, 'Add a folder').click();
    await flush();
    expect(browse).not.toHaveBeenCalled();
    expect(root.querySelector('.folders-add [role="alert"]')?.textContent).toBe('The folder picker is already open.');
  });

  it('opens the picker once for two quick clicks', async () => {
    let done: (p: string | null) => void = () => undefined;
    const pick = vi.fn(() => new Promise<string | null>((res) => (done = res)));
    const { root, section } = settings({ shares: [], problem: null }, pick);
    await section.open();
    button(root, 'Add a folder').click();
    button(root, 'Add a folder').click();
    expect(pick).toHaveBeenCalledTimes(1);
    done(null);
    await flush();
    button(root, 'Add a folder').click();
    expect(pick).toHaveBeenCalledTimes(2);
  });

  it('shows why the panel could not open the places list', async () => {
    browseSays = 'Trillion could not be reached.';
    const { root, section } = settings({ shares: [], problem: null }, null);
    await section.open();
    button(root, 'Add a folder').click();
    await flush();
    expect(root.querySelector('.folders-add [role="alert"]')?.textContent).toBe('Trillion could not be reached.');
  });

  it('opens the Folders panel at the places list in a browser', async () => {
    const { root, section, browse } = settings({ shares: [], problem: null }, null);
    await section.open();
    button(root, 'Add a folder').click();
    await flush();
    expect(browse).toHaveBeenCalledTimes(1);
    expect(root.querySelector('.fo-form')).toBeNull();
  });
});

describe('the desktop picker', () => {
  it('is null at once outside the app', async () => {
    expect(await pickFolder()).toBeNull();
  });

  it('asks the shell for pick_folder in the app', async () => {
    const invoke = vi.fn(async () => '/Users/sam/Projects');
    (window as unknown as { __TAURI__: unknown }).__TAURI__ = { core: { invoke }, event: { listen: async () => () => {} } };
    expect(await pickFolder()).toBe('/Users/sam/Projects');
    expect(invoke).toHaveBeenCalledWith('pick_folder', {});
    invoke.mockResolvedValueOnce(null as unknown as string);
    expect(await pickFolder()).toBeNull();
  });

  it('tells a missing command apart from the picker’s own error', async () => {
    const invoke = vi.fn(async (): Promise<unknown> => {
      throw 'Command pick_folder not found';
    });
    (window as unknown as { __TAURI__: unknown }).__TAURI__ = { core: { invoke }, event: { listen: async () => () => {} } };
    await expect(pickFolder()).rejects.toBeInstanceOf(PickerMissing);
    invoke.mockImplementationOnce(async () => {
      throw 'the folder picker is already open';
    });
    const err = await pickFolder().catch((e: unknown) => e);
    expect(err).not.toBeInstanceOf(PickerMissing);
    expect((err as Error).message).toBe('the folder picker is already open');
  });
});
