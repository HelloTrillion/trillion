import { icon } from './icons';

/**
 * The thread list (SUR-1). The pure part, grouping by day, is tested on its own;
 * the DOM part below it only draws and calls the API.
 */

export interface Thread {
  readonly id: string;
  readonly title: string | null;
  readonly lastMessageAt: number;
  /** A day conversation's date (YYYY-MM-DD); absent for a chat. */
  readonly day?: string;
}

export interface Group {
  readonly label: string;
  readonly threads: readonly Thread[];
}

const DAY = 86_400_000;

function midnight(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

export function groupByDay(threads: readonly Thread[], now: number): Group[] {
  const today = midnight(now);
  const yesterday = midnight(today - 1);
  const week = today - 7 * DAY;
  const buckets: [string, Thread[]][] = [['Today', []], ['Yesterday', []], ['Previous 7 days', []], ['Older', []]];
  for (const t of threads) {
    const i = t.lastMessageAt >= today ? 0 : t.lastMessageAt >= yesterday ? 1 : t.lastMessageAt >= week ? 2 : 3;
    buckets[i]![1].push(t);
  }
  return buckets.filter(([, list]) => list.length > 0).map(([label, list]) => ({ label, threads: list }));
}

/** What a row shows when a conversation has no title yet. */
export const untitled = (t: Thread): string => t.title?.trim() || 'New conversation';

/** Rows from the API. Anything malformed is dropped rather than shown. */
export function threadsOf(body: unknown): { threads: Thread[]; more: boolean } {
  const b = body as { conversations?: unknown; more?: unknown } | null;
  const rows: unknown[] = Array.isArray(b?.conversations) ? b.conversations : [];
  const threads = rows.flatMap((raw): Thread[] => {
    const r = raw as Record<string, unknown> | null;
    if (typeof r?.['id'] !== 'string' || typeof r['last_message_at'] !== 'number') return [];
    const t: Thread = { id: r['id'], title: typeof r['title'] === 'string' ? r['title'] : null, lastMessageAt: r['last_message_at'] };
    return [r['kind'] === 'day' && typeof r['day'] === 'string' ? { ...t, day: r['day'] } : t];
  });
  return { threads, more: b?.more === true };
}

/** A title change arriving over the socket, applied to the rows shown. */
export const retitle = (threads: readonly Thread[], id: string, title: string): Thread[] =>
  threads.map((t) => (t.id === id ? { ...t, title } : t));

export interface Cursor {
  readonly at: number;
  readonly id: string;
}

/** The core refuses a longer search. */
const QUERY_MAX = 200;

/** Where one page of the list comes from: page one, or the page after `before`. */
export function listPath(q: string, before: Cursor | null): string {
  const params = new URLSearchParams({ q: q.trim().slice(0, QUERY_MAX) });
  if (before) {
    params.set('before', String(before.at));
    params.set('before_id', before.id);
  }
  return `/api/conversations?${params.toString()}`;
}

/** The next page starts after the last row shown. */
export function cursorOf(threads: readonly Thread[]): Cursor | null {
  const last = threads[threads.length - 1];
  return last ? { at: last.lastMessageAt, id: last.id } : null;
}

/** A further page added on. A row can show up twice if it moved between pages; it stays once. */
export function appended(threads: readonly Thread[], page: readonly Thread[]): Thread[] {
  const seen = new Set(threads.map((t) => t.id));
  return [...threads, ...page.filter((t) => !seen.has(t.id))];
}

/** A row taken out (deleted), with where it was so a failure can put it back. */
export function without(
  threads: readonly Thread[],
  id: string,
): { threads: Thread[]; thread: Thread; index: number } | null {
  const index = threads.findIndex((t) => t.id === id);
  const thread = threads[index];
  if (!thread) return null;
  return { threads: [...threads.slice(0, index), ...threads.slice(index + 1)], thread, index };
}

export function putBack(threads: readonly Thread[], thread: Thread, index: number): Thread[] {
  if (threads.some((t) => t.id === thread.id)) return threads.slice();
  const at = Math.min(index, threads.length);
  return [...threads.slice(0, at), thread, ...threads.slice(at)];
}

/** The row a key moves to in the list, or null when the key is not one that moves. */
export function moved(ids: readonly string[], active: string | null, key: string): string | null {
  if (ids.length === 0) return null;
  const i = active === null ? -1 : ids.indexOf(active);
  if (i < 0) return ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(key) ? ids[0]! : null;
  if (key === 'ArrowDown') return ids[Math.min(i + 1, ids.length - 1)]!;
  if (key === 'ArrowUp') return ids[Math.max(i - 1, 0)]!;
  if (key === 'Home') return ids[0]!;
  if (key === 'End') return ids[ids.length - 1]!;
  return null;
}

/** When a row last moved: the time today, the date before that (with the year if it was another). */
export function rowTime(at: number, now: number): string {
  const d = new Date(at);
  if (at >= midnight(now)) return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  const thisYear = d.getFullYear() === new Date(now).getFullYear();
  return d.toLocaleDateString([], thisYear ? { month: 'short', day: 'numeric' } : { year: 'numeric', month: 'short', day: 'numeric' });
}

// ── Your day ─────────────────────────────────────────────────────────────

/** Today as the core dates a day conversation: YYYY-MM-DD, local. */
export function localDay(now: number): string {
  const d = new Date(now);
  const two = (n: number): string => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}`;
}

/** Today, short, as the day conversation's title has it: "Mon 26 Oct". */
export function shortDate(now: number): string {
  const d = new Date(now);
  return `${d.toLocaleDateString([], { weekday: 'short' })} ${d.getDate()} ${d.toLocaleDateString([], { month: 'short' })}`;
}

/** Today's day conversation among the rows, if it is there. */
export const todayIn = (threads: readonly Thread[], now: number): string | null =>
  threads.find((t) => t.day === localDay(now))?.id ?? null;

/** Nothing built yet: no board, or one with no widgets. */
export const needsBuild = (board: { readonly widgets: readonly unknown[] } | null): boolean =>
  board === null || board.widgets.length === 0;

export interface DayOpenerDeps {
  /** GET /api/conversations/day: `{id, created}`, or a rejection. */
  readonly fetchDay: () => Promise<unknown>;
  readonly current: () => string | null;
  readonly switchTo: (id: string) => void;
  /** Ask the core to build the board (`day.build`); `spoken` when the day was asked for out loud. */
  readonly build: (spoken: boolean) => void;
  readonly now?: () => number;
}

/** How an open ended. Superseded is not a failure: a newer open, or a switch, took over. */
export type DayResult = 'opened' | 'failed' | 'superseded';

export interface DayOpener {
  /**
   * The entry or her `day_open`: open today's day, building its board if it is empty.
   * `spoken`: it was asked for out loud, so the microphone reopens after the build.
   */
  open(opts?: { readonly spoken?: boolean }): Promise<DayResult>;
  /** The user went elsewhere: an open still waiting on the core, or on its board, is dropped. */
  cancel(): void;
  /** A session began (a switch or a reconnect): its board has not come yet. */
  session(): void;
  /** The session's board arrived (null for none). */
  board(board: { readonly widgets: readonly unknown[] } | null): void;
  /** Today's day conversation, once known; null once that day is over. */
  readonly id: string | null;
}

/**
 * Opening the day: switch to it, then, once its board is known, ask for a build when it
 * is empty. Armed once per open, so a reconnect or a later board never builds again.
 */
export function createDayOpener(d: DayOpenerDeps): DayOpener {
  const now = d.now ?? Date.now;
  // The day conversation last opened, and its date: after midnight it is not today's.
  let known: { readonly id: string; readonly day: string } | null = null;
  let armed: string | null = null;
  // Whether the armed open was asked for out loud.
  let spoken = false;
  let seen = false;
  let last: { readonly widgets: readonly unknown[] } | null = null;
  // Counts each open, so an older answer arriving after a newer one is ignored.
  let gen = 0;

  function settle(): void {
    if (armed === null || !seen) return;
    const mine = armed === d.current();
    armed = null;
    // A session that is not the day's (the switch did not land there) builds nothing.
    if (mine && needsBuild(last)) d.build(spoken);
  }

  return {
    async open(opts) {
      const mine = ++gen;
      const aloud = opts?.spoken === true;
      let body: unknown;
      try {
        body = await d.fetchDay();
      } catch {
        return mine === gen ? 'failed' : 'superseded';
      }
      if (mine !== gen) return 'superseded';
      const got = (body as { id?: unknown } | null)?.id;
      if (typeof got !== 'string' || !got) return 'failed';
      known = { id: got, day: localDay(now()) };
      if (d.current() === got) {
        armed = got;
        spoken = aloud;
        settle();
      } else {
        // The page's switch cancels whatever was waiting, this open included, so it is armed after.
        d.switchTo(got);
        seen = false;
        last = null;
        armed = got;
        spoken = aloud;
      }
      return 'opened';
    },
    cancel() {
      gen++;
      armed = null;
    },
    session() {
      seen = false;
      last = null;
    },
    board(board) {
      seen = true;
      last = board;
      settle();
    },
    get id() {
      return known && known.day === localDay(now()) ? known.id : null;
    },
  };
}

/** How long `day.open` waits for her to finish before opening the day anyway. */
export const DAY_OPEN_WAIT_MS = 15_000;
const DAY_OPEN_POLL_MS = 200;

/**
 * Run `then` once `idle()` holds, checked now and every moment after, or at `capMs`
 * whatever. `day.open` comes mid-reply ("Opening your day."): switching at once would
 * close the socket and cut her off. Returns a cancel.
 */
export function whenIdle(idle: () => boolean, then: () => void, capMs = DAY_OPEN_WAIT_MS): () => void {
  if (idle()) {
    then();
    return () => undefined;
  }
  const started = Date.now();
  const timer = setInterval(() => {
    if (!idle() && Date.now() - started < capMs) return;
    clearInterval(timer);
    then();
  }, DAY_OPEN_POLL_MS);
  return () => clearInterval(timer);
}

/** What Escape does, in one order: she stops first, then the thread list closes, then the workspace. */
export function escapeAction(s: {
  readonly busy: boolean;
  readonly speaking: boolean;
  readonly listening: boolean;
  readonly listOpen: boolean;
  readonly workspaceOpen: boolean;
}): 'interrupt' | 'list' | 'workspace' | null {
  if (s.busy || s.speaking || s.listening) return 'interrupt';
  if (s.listOpen) return 'list';
  if (s.workspaceOpen) return 'workspace';
  return null;
}

// ── The DOM part ─────────────────────────────────────────────────────────

export interface ThreadsDeps {
  readonly root: HTMLElement;
  /** GET /api/conversations with these params; resolves to the parsed JSON, and rejects on failure. */
  readonly fetchPage: (params: { q: string; before: Cursor | null }) => Promise<unknown>;
  readonly rename: (id: string, title: string) => Promise<boolean>;
  readonly remove: (id: string) => Promise<boolean>;
  readonly restore: (id: string) => Promise<boolean>;
  readonly current: () => string | null;
  readonly pick: (id: string) => void;
  /** The open conversation was deleted. */
  readonly deletedCurrent: () => void;
  readonly now: () => number;
  /** The "Your day" row was chosen. False if the day could not be opened. */
  readonly openDay: () => Promise<DayResult>;
  /** Today's day conversation, when the page knows it. */
  readonly dayId: () => string | null;
}

export interface ThreadList {
  open(): void;
  /** Escape is the page's to route (see escapeAction); it calls this. */
  close(): void;
  readonly isOpen: boolean;
  retitle(id: string, title: string): void;
  /** A routine built this conversation while the user was elsewhere: marked until opened. */
  markUnread(id: string): void;
  /** Opened: the mark goes. */
  markRead(id: string): void;
  isUnread(id: string): boolean;
  /** Listeners hear of a close before the list hides, so focus can still be moved out of it. */
  onToggle(listener: (open: boolean) => void): void;
}

const SEARCH_MS = 200;
const TOAST_MS = 8000;
/** Left after the pointer or focus leaves a paused toast, at the least. */
const TOAST_RESUME_MS = 2000;
const MENU_ID = 'thread-menu';

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  // Always textContent: titles are the user's or her words, never markup.
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(cls: string, text: string): HTMLButtonElement {
  const b = el('button', cls, text);
  b.type = 'button';
  return b;
}

/** A live region's text, emptied first and set a moment later, so saying the same thing twice is heard twice. */
function liveText(region: HTMLElement): (text: string) => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  return (text) => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    region.textContent = '';
    if (text)
      timer = setTimeout(() => {
        timer = null;
        region.textContent = text;
      }, 50);
  };
}

/** A small dot, read out as "unread" with its row's name. */
function unreadMark(): HTMLElement {
  const mark = el('span', 'thread-unread');
  mark.setAttribute('role', 'img');
  mark.setAttribute('aria-label', 'unread');
  return mark;
}

let uid = 0;
const nextId = (what: string): string => `thread-${what}-${++uid}`;

export function createThreads(d: ThreadsDeps): ThreadList {
  const { root } = d;
  const listeners: ((open: boolean) => void)[] = [];
  let isOpen = false;
  let threads: Thread[] = [];
  let more = false;
  let loading = false;
  // Counts each load, so an older answer arriving after a newer one is ignored.
  let gen = 0;
  let activeId: string | null = null;
  let renaming: string | null = null;
  let searchTimer: ReturnType<typeof setTimeout> | null = null;
  // The page's own: set by a ready notice, cleared on open, and gone on reload.
  const unread = new Set<string>();

  const searchWrap = el('label', 'panel-search threads-search');
  const search = el('input');
  search.type = 'search';
  search.placeholder = 'Search conversations';
  search.setAttribute('aria-label', 'Search conversations');
  search.autocomplete = 'off';
  search.spellcheck = false;
  searchWrap.append(search);
  const list = el('ul', 'threads-list');
  list.setAttribute('role', 'listbox');
  list.setAttribute('aria-label', 'Conversations');

  // Live regions stay in the page, only out of sight while empty, so what they say is heard.
  const status = el('div', 'threads-status');
  const statusLine = el('span');
  statusLine.setAttribute('role', 'status');
  const setStatus = liveText(statusLine);
  const retry = button('threads-retry', 'Retry');
  retry.hidden = true;
  retry.addEventListener('click', () => void load(false));
  status.append(statusLine, retry);
  // The end of the list: once it scrolls into view, the next page loads.
  const sentinel = el('li', 'threads-sentinel');
  sentinel.setAttribute('role', 'presentation');
  // Your day sits above everything, search included: it is not one of the threads.
  const dayRow = button('thread-day', '');
  const dayName = el('span', 'thread-name', 'Your day');
  const dayDate = el('small', 'thread-time');
  const dayMark = unreadMark();
  dayMark.hidden = true;
  dayRow.append(icon('day', 16), dayName, dayMark, dayDate);
  dayRow.addEventListener('click', () => {
    dayRow.disabled = true;
    void d
      .openDay()
      .catch((): DayResult => 'failed')
      .then((result) => {
        dayRow.disabled = false;
        if (result === 'opened') close();
        else if (result === 'failed') say("Couldn't open your day", false);
      });
  });
  root.append(dayRow, searchWrap, status, list);

  /** Today's date on the row, and marked when today's day is the open conversation. */
  function drawDay(): void {
    const now = d.now();
    dayDate.textContent = shortDate(now);
    const today = d.dayId() ?? todayIn(threads, now);
    const on = today !== null && today === d.current();
    if (on) dayRow.setAttribute('aria-current', 'true');
    else dayRow.removeAttribute('aria-current');
    // Only today's: a day routine's line that arrives after midnight names yesterday's
    // day conversation, which is marked on its own row in the list, not here.
    dayMark.hidden = today === null || !unread.has(today);
  }

  function say(text: string, canRetry = false): void {
    setStatus(text);
    retry.hidden = !canRetry;
    status.classList.toggle('visually-hidden', text === '');
  }
  say('');

  // After the list, not in it, so an Undo outlives closing the list.
  const toast = el('div', 'threads-toast visually-hidden');
  const toastLine = el('span');
  toastLine.setAttribute('role', 'status');
  const setToast = liveText(toastLine);
  const undo = button('threads-undo', 'Undo');
  undo.hidden = true;
  toast.append(toastLine, undo);
  root.after(toast);
  let toastTimer: ReturnType<typeof setTimeout> | null = null;
  let toastEnds = 0;
  let toastPaused = false;
  let toastShown = false;
  let restoring = false;

  const observer =
    typeof IntersectionObserver === 'undefined'
      ? null
      : new IntersectionObserver(
          (seen) => {
            if (seen.some((e) => e.isIntersecting) && more && !loading && isOpen) void load(true);
          },
          { root: list },
        );

  async function load(next: boolean): Promise<void> {
    const mine = ++gen;
    const before = next ? cursorOf(threads) : null;
    loading = true;
    if (!next && threads.length === 0) say('Loading…');
    let body: unknown;
    try {
      body = await d.fetchPage({ q: search.value, before });
    } catch {
      if (mine !== gen) return;
      loading = false;
      say("Couldn't load your conversations.", true);
      return;
    }
    if (mine !== gen) return;
    loading = false;
    const page = threadsOf(body);
    threads = next ? appended(threads, page.threads) : page.threads;
    more = page.more;
    draw();
  }

  function rows(): HTMLLIElement[] {
    return [...list.querySelectorAll<HTMLLIElement>('li[role="option"]')];
  }

  function rowOf(id: string): HTMLLIElement | null {
    return rows().find((li) => li.dataset['id'] === id) ?? null;
  }

  function draw(): void {
    closeMenu(false);
    if (renaming) return; // a redraw would take the box from under the user's fingers
    const hadFocus = list.contains(document.activeElement);
    const current = d.current();
    if (!threads.some((t) => t.id === activeId)) {
      activeId = (threads.find((t) => t.id === current) ?? threads[0])?.id ?? null;
    }
    observer?.unobserve(sentinel);
    const items: HTMLLIElement[] = [];
    for (const g of groupByDay(threads, d.now())) {
      const group = el('li', 'threads-group');
      group.setAttribute('role', 'group');
      const label = el('div', 'panel-group-label', g.label);
      label.id = nextId('group');
      label.setAttribute('role', 'presentation');
      group.setAttribute('aria-labelledby', label.id);
      const inner = el('ul', 'threads-group-rows');
      inner.setAttribute('role', 'none');
      inner.append(...g.threads.map((t) => row(t, current)));
      group.append(label, inner);
      items.push(group);
    }
    if (more) items.push(sentinel);
    list.replaceChildren(...items);
    drawDay();
    if (more) observer?.observe(sentinel);
    say(threads.length ? '' : search.value.trim() ? 'No conversations match' : 'No conversations yet');
    if (hadFocus) focusActive();
  }

  function row(t: Thread, current: string | null): HTMLLIElement {
    const li = el('li', 'thread-row');
    li.setAttribute('role', 'option');
    li.dataset['id'] = t.id;
    // Selection follows the keyboard; the open conversation is marked as current.
    li.setAttribute('aria-selected', String(t.id === activeId));
    if (t.id === current) li.setAttribute('aria-current', 'true');
    li.tabIndex = t.id === activeId ? 0 : -1;
    const name = el('span', 'thread-name', untitled(t));
    name.id = nextId('name');
    const when = el('small', 'thread-time', rowTime(t.lastMessageAt, d.now()));
    when.id = nextId('time');
    const mark = unread.has(t.id) ? unreadMark() : null;
    if (mark) mark.id = nextId('unread');
    li.setAttribute('aria-labelledby', [name.id, mark?.id, when.id].filter(Boolean).join(' '));
    const dots = button('thread-more', '⋯');
    dots.tabIndex = -1;
    dots.setAttribute('aria-label', `More for ${untitled(t)}`);
    dots.setAttribute('aria-haspopup', 'menu');
    dots.setAttribute('aria-expanded', 'false');
    dots.addEventListener('click', (ev) => {
      ev.stopPropagation();
      menu(t, li);
    });
    if (t.day) li.append(icon('day', 14));
    li.append(name);
    if (mark) li.append(mark);
    li.append(when, dots);
    li.addEventListener('click', () => {
      if (renaming !== t.id) choose(t.id);
    });
    li.addEventListener('contextmenu', (ev) => {
      ev.preventDefault();
      menu(t, li);
    });
    return li;
  }

  function setActive(id: string): void {
    activeId = id;
    for (const li of rows()) {
      const on = li.dataset['id'] === id;
      li.tabIndex = on ? 0 : -1;
      li.setAttribute('aria-selected', String(on));
    }
    focusActive();
  }

  function focusActive(): void {
    const li = activeId ? rowOf(activeId) : null;
    li?.focus();
    li?.scrollIntoView({ block: 'nearest' });
  }

  function choose(id: string): void {
    d.pick(id);
    close();
  }

  // ── The row menu: beside the list, not in it, placed at its row ──
  let openMenu: { done: () => void } | null = null;

  function closeMenu(refocus: boolean): void {
    if (!openMenu) return;
    const { done } = openMenu;
    openMenu = null;
    done();
    if (refocus) focusActive();
  }

  function menu(t: Thread, li: HTMLLIElement): void {
    closeMenu(false);
    setActive(t.id);
    const dots = li.querySelector<HTMLButtonElement>('.thread-more');
    const box = el('div', 'thread-menu');
    box.id = MENU_ID;
    box.setAttribute('role', 'menu');
    box.setAttribute('aria-label', untitled(t));
    const items = [button('thread-menu-item', 'Rename'), button('thread-menu-item', 'Delete')];
    for (const item of items) {
      item.setAttribute('role', 'menuitem');
      item.tabIndex = -1;
    }
    const [renameItem, deleteItem] = items as [HTMLButtonElement, HTMLButtonElement];
    renameItem.addEventListener('click', () => {
      closeMenu(false);
      rename(t);
    });
    deleteItem.addEventListener('click', () => {
      closeMenu(false);
      void remove(t.id);
    });
    box.addEventListener('keydown', (ev) => {
      const at = items.indexOf(document.activeElement as HTMLButtonElement);
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        items[(at + (ev.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length]?.focus();
      } else if (ev.key === 'Escape' || ev.key === 'Tab') {
        closeMenu(true);
      } else return;
      ev.preventDefault();
      ev.stopPropagation();
    });
    box.append(...items);
    const r = li.getBoundingClientRect();
    const base = root.getBoundingClientRect();
    box.style.top = `${r.bottom - base.top - 2}px`;
    box.style.right = `${base.right - r.right + 4}px`;
    root.append(box);
    dots?.setAttribute('aria-expanded', 'true');
    dots?.setAttribute('aria-controls', MENU_ID);
    const outside = (ev: Event): void => {
      if (!box.contains(ev.target as Node)) closeMenu(false);
    };
    // Placed at its row, it would drift off it as the list scrolls: it closes instead.
    const scrolled = (): void => closeMenu(false);
    document.addEventListener('pointerdown', outside, true);
    list.addEventListener('scroll', scrolled, { passive: true });
    openMenu = {
      done: () => {
        document.removeEventListener('pointerdown', outside, true);
        list.removeEventListener('scroll', scrolled);
        dots?.setAttribute('aria-expanded', 'false');
        dots?.removeAttribute('aria-controls');
        box.remove();
      },
    };
    renameItem.focus();
  }

  // ── Rename ──
  function rename(t: Thread): void {
    const li = rowOf(t.id);
    const name = li?.querySelector('.thread-name');
    if (!li || !name) return;
    renaming = t.id;
    const box = el('input', 'thread-rename');
    box.value = t.title ?? '';
    box.maxLength = 500;
    box.setAttribute('aria-label', 'Conversation name');
    let finished = false;
    const end = (): void => {
      finished = true;
      renaming = null;
    };
    // Escape keeps focus on the row; a blur leaves it wherever the user went.
    const cancel = (refocus: boolean): void => {
      if (finished) return;
      end();
      draw();
      if (refocus) focusActive();
    };
    box.addEventListener('click', (ev) => ev.stopPropagation());
    box.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') {
        cancel(true);
      } else if (ev.key === 'Enter' && !ev.isComposing) {
        const title = box.value.trim();
        if (!title || title === t.title) {
          cancel(true);
        } else {
          end();
          box.disabled = true;
          void d
            .rename(t.id, title)
            .catch(() => false)
            .then((ok) => {
              if (ok) threads = retitle(threads, t.id, title);
              draw();
              if (!ok) say("Couldn't rename it");
              focusActive();
            });
        }
      } else return;
      ev.preventDefault();
      ev.stopPropagation();
    });
    box.addEventListener('blur', () => cancel(false));
    name.replaceWith(box);
    box.focus();
    box.select();
  }

  // ── Delete, with undo ──
  async function remove(id: string): Promise<void> {
    const out = without(threads, id);
    if (!out) return;
    const hadFocus = list.contains(document.activeElement);
    threads = out.threads;
    activeId = threads[Math.min(out.index, threads.length - 1)]?.id ?? null;
    draw();
    if (hadFocus) {
      if (activeId) focusActive();
      else search.focus();
    }
    const ok = await d.remove(id).catch(() => false);
    if (!ok) {
      threads = putBack(threads, out.thread, out.index);
      activeId = out.thread.id;
      const focusHere = hadFocus && (list.contains(document.activeElement) || document.activeElement === search);
      draw();
      say("Couldn't delete it");
      if (focusHere) focusActive();
      return;
    }
    showToast(id);
    // Checked now, not before: if the core said so over the socket first, the page has moved on.
    if (id === d.current()) d.deletedCurrent();
  }

  function armToast(ms: number): void {
    if (toastTimer !== null) clearTimeout(toastTimer);
    toastTimer = null;
    toastEnds = Date.now() + ms;
    // While the pointer or focus is on it, it waits, so Undo is not taken away mid-reach.
    if (!toastPaused) toastTimer = setTimeout(hideToast, ms);
  }

  function showToast(id: string): void {
    toastShown = true;
    setToast('Conversation deleted');
    undo.hidden = false;
    toast.classList.remove('visually-hidden');
    undo.onclick = () => {
      // Waiting on the core: the toast stays until it answers.
      undo.hidden = true;
      restoring = true;
      if (toastTimer !== null) clearTimeout(toastTimer);
      toastTimer = null;
      void d
        .restore(id)
        .catch(() => false)
        .then((ok) => {
          restoring = false;
          if (ok) {
            hideToast();
            if (isOpen) void load(false);
          } else {
            setToast("Couldn't bring it back");
            armToast(TOAST_RESUME_MS * 2);
          }
        });
    };
    toastPaused = toast.matches(':hover, :focus-within');
    armToast(TOAST_MS);
  }

  function hideToast(): void {
    if (toastTimer !== null) clearTimeout(toastTimer);
    toastTimer = null;
    toastShown = false;
    toastPaused = false;
    const hadFocus = toast.contains(document.activeElement);
    setToast('');
    undo.hidden = true;
    undo.onclick = null;
    toast.classList.add('visually-hidden');
    if (hadFocus && isOpen) search.focus();
  }

  function pauseToast(): void {
    toastPaused = true;
    if (toastTimer !== null) clearTimeout(toastTimer);
    toastTimer = null;
  }
  function resumeToast(): void {
    if (toast.matches(':hover, :focus-within')) return;
    toastPaused = false;
    if (toastShown && !restoring) armToast(Math.max(toastEnds - Date.now(), TOAST_RESUME_MS));
  }
  toast.addEventListener('pointerenter', pauseToast);
  toast.addEventListener('focusin', pauseToast);
  toast.addEventListener('pointerleave', resumeToast);
  // After the focus has moved, so :focus-within is up to date.
  toast.addEventListener('focusout', () => setTimeout(resumeToast, 0));

  // ── Keys and search. Escape that is not used here goes on to the page (escapeAction). ──
  list.addEventListener('keydown', (ev) => {
    if (ev.target instanceof HTMLInputElement) return;
    const to = moved(
      threads.map((t) => t.id),
      activeId,
      ev.key,
    );
    if (to !== null) {
      ev.preventDefault();
      setActive(to);
    } else if (ev.key === 'Enter' && activeId) {
      ev.preventDefault();
      choose(activeId);
    } else if ((ev.key === 'ContextMenu' || (ev.key === 'F10' && ev.shiftKey)) && activeId) {
      ev.preventDefault();
      const t = threads.find((x) => x.id === activeId);
      const li = rowOf(activeId);
      if (t && li) menu(t, li);
    }
  });

  search.addEventListener('keydown', (ev) => {
    if (ev.key === 'ArrowDown' && threads.length) {
      ev.preventDefault();
      setActive(activeId ?? threads[0]!.id);
    } else if (ev.key === 'Enter' && activeId) {
      ev.preventDefault();
      choose(activeId);
    } else if (ev.key === 'Escape' && search.value) {
      // The first Escape clears the search; the next one goes on and closes the list.
      ev.preventDefault();
      ev.stopPropagation();
      search.value = '';
      activeId = null;
      void load(false);
    }
  });

  search.addEventListener('input', () => {
    if (searchTimer !== null) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      searchTimer = null;
      activeId = null;
      void load(false);
    }, SEARCH_MS);
  });

  function fire(): void {
    for (const l of listeners) l(isOpen);
  }

  function open(): void {
    if (isOpen) return;
    isOpen = true;
    root.hidden = false;
    activeId = null;
    drawDay();
    void load(false);
    search.focus();
    fire();
  }

  function close(): void {
    if (!isOpen) return;
    isOpen = false;
    closeMenu(false);
    if (searchTimer !== null) clearTimeout(searchTimer);
    searchTimer = null;
    renaming = null;
    gen++; // whatever is still loading is not wanted now
    loading = false;
    fire();
    root.hidden = true;
  }

  return {
    open,
    close,
    get isOpen() {
      return isOpen;
    },
    retitle(id, title) {
      threads = retitle(threads, id, title);
      if (isOpen) draw();
    },
    markUnread(id) {
      if (unread.has(id)) return;
      unread.add(id);
      if (isOpen) draw();
      else drawDay();
    },
    markRead(id) {
      if (!unread.delete(id)) return;
      if (isOpen) draw();
      else drawDay();
    },
    isUnread: (id) => unread.has(id),
    onToggle(listener) {
      listeners.push(listener);
    },
  };
}
