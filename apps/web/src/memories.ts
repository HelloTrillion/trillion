/**
 * Settings → Memories (MEM-6): what she remembers about you, where each came from and
 * when, with a way to correct one and a way to forget one.
 *
 * Editing goes through /api/memories: the new wording is saved as something you said
 * (MEM-6a) and the old one is kept, marked as replaced (MEM-7). Forgetting never deletes
 * anything from here. The page asks the session to propose `memory_forget`, and the core
 * puts up the same card it raises when she proposes it; only a yes on that card forgets
 * (MEM-3, SPN-2, SPN-14). Memory text is only ever set as text.
 */

import { icon } from './icons';
import type { Host } from './panel';
import type { Api } from './settings';
import { age } from './when';

// ── What the server sends ────────────────────────────────────────────────

export type MemoryType = 'user' | 'feedback' | 'project' | 'reference';
export type Source = 'user_statement' | 'imported_claim' | 'agent_inference';

export interface Memory {
  readonly id: string;
  readonly type: MemoryType;
  readonly text: string;
  readonly source: Source;
  readonly moves: boolean;
  readonly version: number;
  readonly created_ms: number;
  readonly as_of_ms: number;
  /** The memory this one corrected, kept as history. */
  readonly supersedes: string | null;
}

export interface MemoriesState {
  readonly memories: readonly Memory[];
  readonly max_text: number;
  /** The files and the index disagreed when Trillion started (MEM-6b). */
  readonly diverged: boolean;
  readonly unreadable: number;
}

// ── Pure helpers ────────────────────────────────────────────────────────

export const TYPES: readonly MemoryType[] = ['user', 'feedback', 'project', 'reference'];

const TYPE_LABELS: Readonly<Record<MemoryType, string>> = {
  user: 'About you',
  feedback: 'How you like things done',
  project: 'Projects',
  reference: 'Where things are',
};

export function typeLabel(t: MemoryType): string {
  return TYPE_LABELS[t] ?? t;
}

const SOURCES: Readonly<Record<Source, string>> = {
  user_statement: 'You said this',
  imported_claim: 'From something she read',
  agent_inference: 'Her own guess',
};

/** A type or source as the Memories screen words it, from a raw value; null for one it doesn't know. */
export function memoryWords(kind: 'type' | 'source', value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const table: Readonly<Record<string, string>> = kind === 'type' ? TYPE_LABELS : SOURCES;
  return Object.hasOwn(table, value) ? (table[value] ?? null) : null;
}

/** Where it came from, in words: who said it, and whether it corrected an earlier one. */
export function sourceText(m: Pick<Memory, 'source' | 'supersedes'>): string {
  const from = SOURCES[m.source] ?? 'Unknown source';
  return m.supersedes ? `${from} · corrects an earlier one` : from;
}

/** When it was saved: "just now", "2 h ago", "yesterday", "Oct 12". */
export function whenText(ms: number, now: number): string {
  return `Saved ${age(new Date(ms), new Date(now))}`;
}

const fold = (s: string): string => s.toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '');

/** Every word of the search appears in the memory's text or its group's name. */
export function matches(m: Pick<Memory, 'text' | 'type'>, query: string): boolean {
  const q = fold(query.trim());
  if (!q) return true;
  const hay = fold(`${m.text} ${typeLabel(m.type)}`);
  return q.split(/\s+/).every((w) => hay.includes(w));
}

export interface Group {
  readonly type: MemoryType;
  readonly label: string;
  readonly items: readonly Memory[];
}

/** The memories that match, in their groups (in a fixed order), newest first; empty groups left out. */
export function groupMemories(list: readonly Memory[], query: string): Group[] {
  const groups: Group[] = [];
  for (const type of TYPES) {
    const items = list
      .filter((m) => m.type === type && matches(m, query))
      .sort((a, b) => b.as_of_ms - a.as_of_ms || (b.id < a.id ? -1 : b.id > a.id ? 1 : 0));
    if (items.length) groups.push({ type, label: typeLabel(type), items });
  }
  return groups;
}

/** The line under the section's name in the Settings rail. */
export function memoriesSummary(s: MemoriesState | null): string {
  if (!s) return '';
  const n = s.memories.length;
  return n ? `${n} remembered` : 'Nothing yet';
}

/** How many a search found, said for the live region. */
export function countText(shown: number, total: number, query: string): string {
  if (!query.trim()) return total === 1 ? '1 memory' : `${total} memories`;
  if (!shown) return 'Nothing matches.';
  return `${shown} of ${total} match`;
}

const EDIT_ERRORS: Readonly<Record<string, string>> = {
  unchanged: 'Nothing was changed.',
  empty: 'A memory needs some words. To remove it, use Forget.',
  too_long: 'That is too long for one memory. Keep it to one fact.',
  not_found: 'That memory is gone or was changed somewhere else.',
  changed: 'That memory changed since this page showed it. It has been read again.',
  disk: 'The memory file could not be written. Is the disk full?',
  full: 'Memory is full.',
};

export function editError(code: string): string {
  return EDIT_ERRORS[code] ?? 'That did not save. Try again.';
}

/** What the page says after asking to forget: the card is up, or why it is not. */
export function askedText(status: string, reason: string | null): string {
  if (status === 'confirmation_required') return 'Approve the card in the conversation to forget it.';
  if (reason === 'precondition_failed') return 'That memory is already forgotten or replaced.';
  return 'It could not be asked about. Try again.';
}

/** What came of the card, once it is settled. */
export function resolvedText(status: string): string {
  if (status === 'succeeded') return 'Forgotten. Its file is kept, marked as forgotten.';
  if (status === 'cancelled' || status === 'rejected') return 'Kept. Nothing was forgotten.';
  if (status === 'refused') return 'Not forgotten: it changed after you asked.';
  return 'Not forgotten.';
}

// ── The screen ───────────────────────────────────────────────────────────

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(text: string, cls = '', onClick?: () => void): HTMLButtonElement {
  const b = el('button', cls, text);
  b.type = 'button';
  if (onClick) b.addEventListener('click', onClick);
  return b;
}

/** A memory's text as a short name for a button's label. */
function short(text: string): string {
  const t = text.replace(/\s+/g, ' ').trim();
  return t.length > 60 ? `${t.slice(0, 59)}…` : t;
}

export interface Memories {
  open(): Promise<void>;
  summary(): string;
  /** A message from the session: the forget card's ask and its outcome. */
  handle(msg: { readonly type: string; readonly [key: string]: unknown }): void;
}

export function createMemories(
  host: Host,
  api: Api,
  /** Sends one message to the session; false when it is not connected. */
  send: (body: Record<string, unknown>) => boolean,
  changed: () => void,
  /** The notes-from-chats switch (MEM-4), shown above the list. */
  notes?: { readonly element: HTMLElement; open(): Promise<void> },
): Memories {
  let last: MemoriesState | null = null;
  let loadError = '';
  let query = '';
  let editing = '';
  let draft = '';
  let editProblem = '';
  let busy = false;
  // Memory id → the card asking about it, while the card waits.
  const waiting = new Map<string, string>();

  const form = el('form', 'settings connected memories');
  form.addEventListener('submit', (ev) => ev.preventDefault());
  host.setAttribute('aria-labelledby', 'memories-title');
  const title = el('h2', '', 'Memories');
  title.id = 'memories-title';
  const note = el(
    'p',
    'settings-note',
    'What she remembers between conversations, and where each came from. A correction keeps the old wording as history. Forgetting asks first, on a card in the conversation.',
  );
  const searchWrap = el('label', 'connected-search');
  const search = el('input');
  search.type = 'search';
  search.placeholder = 'Search memories';
  search.setAttribute('aria-label', 'Search memories');
  search.autocomplete = 'off';
  search.spellcheck = false;
  searchWrap.append(icon('search', 16), search);
  // One polite live region for counts and outcomes (SUR-6).
  const status = el('p', 'settings-hint memories-status');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  const body = el('div', 'memories-body');
  form.append(title, note, ...(notes ? [notes.element] : []), searchWrap, status, body);
  host.replaceChildren(form);

  const say = (text: string): void => {
    status.textContent = text;
  };

  search.addEventListener('input', () => {
    query = search.value;
    draw();
    if (last) {
      const shown = groupMemories(last.memories, query).reduce((n, g) => n + g.items.length, 0);
      say(countText(shown, last.memories.length, query));
    }
  });

  async function load(): Promise<void> {
    try {
      const r = await api('GET', '/api/memories');
      if (!r.ok) throw new Error(String(r.status));
      last = (await r.json()) as MemoriesState;
      loadError = '';
      // A card for a memory no longer shown has nothing left to wait for here.
      for (const id of waiting.keys()) if (!last.memories.some((m) => m.id === id)) waiting.delete(id);
    } catch {
      loadError = last ? '' : 'Memories could not be loaded.';
    }
    changed();
  }

  function focusOn(key: string): void {
    (body.querySelector(`[data-focus="${CSS.escape(key)}"]`) as HTMLElement | null)?.focus();
  }

  function startEdit(m: Memory): void {
    editing = m.id;
    draft = m.text;
    editProblem = '';
    draw();
    const area = body.querySelector('textarea');
    if (area) {
      area.focus();
      area.setSelectionRange(area.value.length, area.value.length);
    }
  }

  function stopEdit(m: Memory): void {
    editing = '';
    editProblem = '';
    draw();
    focusOn(`edit:${m.id}`);
  }

  async function save(m: Memory): Promise<void> {
    if (busy) return;
    busy = true;
    editProblem = '';
    try {
      const r = await api('PUT', `/api/memories/${encodeURIComponent(m.id)}`, { text: draft, version: m.version });
      if (r.ok) {
        const next = (await r.json()) as MemoriesState & { id: string; duplicate: boolean };
        last = next;
        editing = '';
        busy = false;
        changed();
        draw();
        say(next.duplicate ? 'She already remembered that, so the old one now points to it.' : 'Saved as something you said. The old wording is kept as history.');
        focusOn(`edit:${next.id}`);
        return;
      }
      let code = '';
      try {
        code = String(((await r.json()) as { error?: unknown }).error ?? '');
      } catch {
        // Not JSON: the generic line.
      }
      editProblem = editError(code);
      if (code === 'changed' || code === 'not_found') {
        await load();
        editing = '';
      }
    } catch {
      editProblem = 'Trillion could not be reached.';
    }
    busy = false;
    draw();
    if (editing) body.querySelector('textarea')?.focus();
    else say(editProblem);
  }

  function forget(m: Memory): void {
    if (waiting.has(m.id)) return;
    if (!send({ type: 'memory.forget', id: m.id })) {
      say('Not connected to Trillion. Try again in a moment.');
      return;
    }
    say('Asking…');
  }

  function row(m: Memory, now: number): HTMLElement {
    const box = el('div', 'connected-card memories-card');
    box.setAttribute('role', 'listitem');
    if (editing === m.id) {
      const label = el('label', 'memories-edit-label', 'Edit this memory');
      const area = el('textarea', 'connected-paste memories-edit');
      area.id = `memory-edit-${m.id}`;
      label.htmlFor = area.id;
      area.value = draft;
      area.rows = Math.min(8, Math.max(2, Math.ceil(draft.length / 60)));
      area.maxLength = last?.max_text ?? 1200;
      area.addEventListener('input', () => {
        draft = area.value;
      });
      area.addEventListener('keydown', (ev) => {
        if (ev.key === 'Escape') {
          // Leaves the edit, not Settings.
          ev.preventDefault();
          ev.stopPropagation();
          stopEdit(m);
        } else if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) {
          ev.preventDefault();
          void save(m);
        }
      });
      const hint = el('p', 'settings-hint', 'Saved as something you said. The old wording is kept. ⌘ or Ctrl + Enter saves; Escape cancels.');
      const actions = el('div', 'memories-actions');
      const ok = button('Save', 'approve', () => void save(m));
      ok.disabled = busy;
      const cancel = button('Cancel', 'quiet', () => stopEdit(m));
      actions.append(ok, cancel);
      box.append(label, area, hint);
      if (editProblem) {
        const p = el('p', 'settings-hint connected-warn', editProblem);
        p.setAttribute('role', 'alert');
        box.append(p);
      }
      box.append(actions);
      return box;
    }
    const head = el('div', 'connected-card-head');
    const words = el('span', 'connected-card-words');
    const text = el('span', 'memories-text', m.text);
    const meta = el('small', 'connected-state', `${sourceText(m)} · ${whenText(m.created_ms, now)}`);
    words.append(text, meta);
    if (m.moves) words.append(el('small', 'connected-state', 'Changes on its own: she says how old it is'));
    const pid = waiting.get(m.id);
    if (pid) words.append(el('small', 'connected-state connected-warn', 'Waiting for your yes on the card in the conversation'));
    head.append(words);
    const actions = el('span', 'memories-actions');
    const edit = button('Edit', 'quiet', () => startEdit(m));
    edit.setAttribute('aria-label', `Edit: ${short(m.text)}`);
    edit.dataset.focus = `edit:${m.id}`;
    edit.disabled = Boolean(pid);
    const drop = button('Forget', 'quiet connected-danger', () => forget(m));
    drop.setAttribute('aria-label', `Forget: ${short(m.text)}`);
    drop.dataset.focus = `forget:${m.id}`;
    drop.disabled = Boolean(pid);
    actions.append(edit, drop);
    head.append(actions);
    box.append(head);
    return box;
  }

  function draw(): void {
    const parts: HTMLElement[] = [];
    if (!last) {
      parts.push(el('p', 'settings-hint', loadError || 'Loading…'));
      body.replaceChildren(...parts);
      return;
    }
    if (last.diverged) {
      parts.push(
        el(
          'p',
          'settings-warn',
          last.unreadable
            ? `${last.unreadable} memory file${last.unreadable === 1 ? '' : 's'} could not be read when Trillion started. They were left as they are.`
            : 'Some memory files were changed outside Trillion. What is on disk is what is shown.',
        ),
      );
    }
    const now = Date.now();
    const groups = groupMemories(last.memories, query);
    if (!last.memories.length) parts.push(el('p', 'settings-hint', 'Nothing yet. Tell her something about yourself, or say “remember that…”.'));
    else if (!groups.length) parts.push(el('p', 'settings-hint', 'Nothing matches.'));
    for (const g of groups) {
      const heading = el('h3', 'connected-heading', `${g.label} (${g.items.length})`);
      heading.id = `memories-${g.type}`;
      const list = el('div', 'connected-list');
      list.setAttribute('role', 'list');
      list.setAttribute('aria-labelledby', heading.id);
      list.append(...g.items.map((m) => row(m, now)));
      parts.push(heading, list);
    }
    // Focus stays on the same control across a redraw.
    const active = document.activeElement instanceof HTMLElement && body.contains(document.activeElement) ? document.activeElement.dataset.focus : undefined;
    body.replaceChildren(...parts);
    if (active) focusOn(active);
  }

  return {
    async open() {
      host.showModal();
      draw();
      await Promise.all([load(), notes?.open()]);
      draw();
    },
    summary: () => memoriesSummary(last),
    handle(msg) {
      if (msg.type === 'memory.asked') {
        const id = String(msg['id'] ?? '');
        const pid = typeof msg['pending_id'] === 'string' ? msg['pending_id'] : null;
        if (pid) waiting.set(id, pid);
        say(askedText(String(msg['status'] ?? ''), typeof msg['reason'] === 'string' ? msg['reason'] : null));
        draw();
        if (!pid) void load().then(draw);
        return;
      }
      if (msg.type === 'confirm.resolved') {
        const pid = String(msg['pending_id'] ?? '');
        const id = [...waiting].find(([, p]) => p === pid)?.[0];
        if (id === undefined) return;
        waiting.delete(id);
        say(resolvedText(String(msg['status'] ?? '')));
        void load().then(() => {
          draw();
          // Its row went while focus was on it: focus goes to the search, which is always there.
          // Focus elsewhere (the card that was just tapped) is left where it is.
          const lost = document.activeElement === null || document.activeElement === document.body;
          if (host.open && lost) search.focus();
        });
      }
    },
  };
}
