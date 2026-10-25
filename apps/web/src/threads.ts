/**
 * The thread list (SUR-1). The pure part, grouping by day, is tested on its own;
 * the DOM part below it only draws and calls the API.
 */

export interface Thread {
  readonly id: string;
  readonly title: string | null;
  readonly lastMessageAt: number;
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
    return typeof r?.['id'] === 'string' && typeof r['last_message_at'] === 'number'
      ? [{ id: r['id'], title: typeof r['title'] === 'string' ? r['title'] : null, lastMessageAt: r['last_message_at'] }]
      : [];
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
}

export interface ThreadList {
  open(): void;
  close(): void;
  readonly isOpen: boolean;
  retitle(id: string, title: string): void;
  /** Listeners hear of a close before the list hides, so focus can still be moved out of it. */
  onToggle(listener: (open: boolean) => void): void;
}

const SEARCH_MS = 200;
const TOAST_MS = 8000;

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
  let toastTimer: ReturnType<typeof setTimeout> | null = null;

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
  const status = el('p', 'threads-status');
  status.setAttribute('role', 'status');
  const statusText = el('span');
  const retry = button('threads-retry', 'Retry');
  retry.hidden = true;
  retry.addEventListener('click', () => void load(false));
  status.append(statusText, retry);
  // The end of the list: once it scrolls into view, the next page loads.
  const sentinel = el('li', 'threads-sentinel');
  sentinel.setAttribute('role', 'presentation');
  root.append(searchWrap, status, list);

  // After the list, not in it, so an Undo outlives closing the list.
  const toast = el('div', 'threads-toast');
  toast.setAttribute('role', 'status');
  toast.hidden = true;
  const toastText = el('span', '', 'Conversation deleted');
  const undo = button('threads-undo', 'Undo');
  toast.append(toastText, undo);
  root.after(toast);

  function say(text: string, canRetry = false): void {
    statusText.textContent = text;
    retry.hidden = !canRetry;
    status.hidden = text === '';
  }
  say('');

  const observer =
    typeof IntersectionObserver === 'undefined'
      ? null
      : new IntersectionObserver((seen) => {
          if (seen.some((e) => e.isIntersecting) && more && !loading && isOpen) void load(true);
        }, { root: list });

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

  function rowOf(id: string): HTMLLIElement | null {
    for (const li of list.querySelectorAll<HTMLLIElement>('li[role="option"]')) if (li.dataset['id'] === id) return li;
    return null;
  }

  function draw(): void {
    if (renaming) return; // a redraw would take the box from under the user's fingers
    const hadFocus = list.contains(document.activeElement);
    const current = d.current();
    if (!threads.some((t) => t.id === activeId)) {
      activeId = (threads.find((t) => t.id === current) ?? threads[0])?.id ?? null;
    }
    observer?.unobserve(sentinel);
    const rows: HTMLLIElement[] = [];
    for (const g of groupByDay(threads, d.now())) {
      const label = el('li', 'panel-group-label threads-group', g.label);
      label.setAttribute('role', 'presentation');
      rows.push(label, ...g.threads.map((t) => row(t, current)));
    }
    if (more) rows.push(sentinel);
    list.replaceChildren(...rows);
    if (more) observer?.observe(sentinel);
    say(threads.length ? '' : search.value.trim() ? 'No conversations match' : 'No conversations yet');
    if (hadFocus) focusActive();
  }

  function row(t: Thread, current: string | null): HTMLLIElement {
    const li = el('li', 'thread-row');
    li.setAttribute('role', 'option');
    li.dataset['id'] = t.id;
    li.setAttribute('aria-selected', String(t.id === current));
    li.tabIndex = t.id === activeId ? 0 : -1;
    const name = el('span', 'thread-name', untitled(t));
    const when = el('small', 'thread-time', rowTime(t.lastMessageAt, d.now()));
    const dots = button('thread-more', '⋯');
    dots.tabIndex = -1;
    dots.setAttribute('aria-label', `More for ${untitled(t)}`);
    dots.setAttribute('aria-haspopup', 'menu');
    dots.addEventListener('click', (ev) => {
      ev.stopPropagation();
      menu(t, li);
    });
    li.append(name, when, dots);
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
    for (const li of list.querySelectorAll<HTMLLIElement>('li[role="option"]')) {
      li.tabIndex = li.dataset['id'] === id ? 0 : -1;
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

  // ── The row menu ──
  let openMenu: { box: HTMLElement; done: () => void } | null = null;

  function closeMenu(refocus: boolean): void {
    if (!openMenu) return;
    openMenu.done();
    openMenu = null;
    if (refocus) focusActive();
  }

  function menu(t: Thread, li: HTMLLIElement): void {
    closeMenu(false);
    activeId = t.id;
    const box = el('div', 'thread-menu');
    box.setAttribute('role', 'menu');
    box.setAttribute('aria-label', untitled(t));
    const items = [button('thread-menu-item', 'Rename'), button('thread-menu-item', 'Delete')];
    for (const item of items) {
      item.setAttribute('role', 'menuitem');
      item.tabIndex = -1;
    }
    const [renameItem, deleteItem] = items as [HTMLButtonElement, HTMLButtonElement];
    renameItem.addEventListener('click', (ev) => {
      ev.stopPropagation();
      closeMenu(false);
      rename(t);
    });
    deleteItem.addEventListener('click', (ev) => {
      ev.stopPropagation();
      closeMenu(false);
      void remove(t.id);
    });
    box.addEventListener('click', (ev) => ev.stopPropagation());
    box.addEventListener('keydown', (ev) => {
      ev.stopPropagation();
      const at = items.indexOf(document.activeElement as HTMLButtonElement);
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault();
        items[(at + (ev.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length]?.focus();
      } else if (ev.key === 'Escape') {
        ev.preventDefault();
        closeMenu(true);
      } else if (ev.key === 'Tab') {
        ev.preventDefault();
        closeMenu(true);
      }
    });
    box.append(...items);
    li.append(box);
    const outside = (ev: Event): void => {
      if (!box.contains(ev.target as Node)) closeMenu(false);
    };
    document.addEventListener('pointerdown', outside, true);
    openMenu = {
      box,
      done: () => {
        document.removeEventListener('pointerdown', outside, true);
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
      ev.stopPropagation();
      if (ev.key === 'Escape') {
        ev.preventDefault();
        cancel(true);
      } else if (ev.key === 'Enter' && !ev.isComposing) {
        ev.preventDefault();
        const title = box.value.trim();
        if (!title || title === t.title) {
          cancel(true);
          return;
        }
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
    if (hadFocus) (activeId ? focusActive() : search.focus());
    const ok = await d.remove(id).catch(() => false);
    if (!ok) {
      threads = putBack(threads, out.thread, out.index);
      draw();
      say("Couldn't delete it");
      return;
    }
    showToast(id);
    // Checked now, not before: if the core said so over the socket first, the page has moved on.
    if (id === d.current()) d.deletedCurrent();
  }

  function showToast(id: string): void {
    if (toastTimer !== null) clearTimeout(toastTimer);
    toastText.textContent = 'Conversation deleted';
    undo.hidden = false;
    toast.hidden = false;
    undo.onclick = () => {
      undo.hidden = true;
      void d
        .restore(id)
        .catch(() => false)
        .then((ok) => {
          if (ok) {
            hideToast();
            if (isOpen) void load(false);
          } else {
            toastText.textContent = "Couldn't bring it back";
          }
        });
    };
    toastTimer = setTimeout(hideToast, TOAST_MS);
  }

  function hideToast(): void {
    if (toastTimer !== null) clearTimeout(toastTimer);
    toastTimer = null;
    toast.hidden = true;
    undo.onclick = null;
  }

  // ── Keys and search ──
  list.addEventListener('keydown', (ev) => {
    if (ev.target instanceof HTMLInputElement) return;
    const ids = threads.map((t) => t.id);
    const to = moved(ids, activeId, ev.key);
    if (to !== null) {
      ev.preventDefault();
      setActive(to);
    } else if (ev.key === 'Enter' && activeId) {
      ev.preventDefault();
      choose(activeId);
    } else if (ev.key === 'Escape') {
      ev.preventDefault();
      ev.stopPropagation();
      close();
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
    } else if (ev.key === 'Escape') {
      ev.preventDefault();
      ev.stopPropagation();
      // The first Escape clears the search, the next one closes.
      if (search.value) {
        search.value = '';
        void load(false);
      } else close();
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
    onToggle(listener) {
      listeners.push(listener);
    },
  };
}
