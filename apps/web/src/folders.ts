/**
 * Shared folders (the shared folders spec): the Folders panel in the workspace beside
 * the board, and Settings → Shared folders. Both read and change shares through the
 * same store, and both end in the same form.
 *
 * Folder names, share names and paths are the user's (or came from the disk): every
 * one is set as text, never as markup. The panel only sends a share the user filled
 * in and submitted; she proposes hers on a card instead.
 */

import { icon } from './icons';
import type { Host } from './panel';
import type { Api } from './settings';
import { PickerMissing } from './desktop';
import { refusal } from './skills';

// ── What the server sends ────────────────────────────────────────────────

export type Access = 'read' | 'change';

export interface ShareRow {
  readonly id: string;
  readonly name: string;
  readonly path: string;
  readonly access: Access;
  /** `everyone`, or `routine:<id>`. */
  readonly scope: string;
  /** The routine it is for; null for everyone, or when that routine is gone. */
  readonly routine: { readonly id: string; readonly title: string } | null;
  /** Why it reaches nothing now ("not found", ...), or null when it reaches its folder. */
  readonly state: string | null;
  /** From `--grant`: kept for this run only. */
  readonly run_only: boolean;
}

export interface SharesState {
  readonly shares: readonly ShareRow[];
  /** The damaged-file sentence, naming the file, or null. */
  readonly problem: string | null;
}

export interface Place {
  readonly label: string;
  readonly path: string;
  readonly kind: 'home' | 'folder' | 'shared' | 'drive';
}

/** The session's `folders` frame, and the browse route's answer. */
export interface FolderFrame {
  /** The folder shown, or null for the places list. */
  readonly path: string | null;
  readonly crumbs: readonly { readonly label: string; readonly path: string }[];
  readonly folders: readonly string[];
  readonly more: number;
  /** The id of the share of the folder shown, or null. */
  readonly shared: string | null;
  readonly places: readonly Place[] | null;
}

/** A routine she can be given a folder for, by its title. */
export interface Choice {
  readonly id: string;
  readonly title: string;
}

const KINDS: readonly string[] = ['home', 'folder', 'shared', 'drive'];
const str = (v: unknown): string => (typeof v === 'string' ? v : '');

/** A frame from the socket or the route, checked field by field; null when it is not one. */
export function parseFrame(msg: unknown): FolderFrame | null {
  if (typeof msg !== 'object' || msg === null) return null;
  const m = msg as Record<string, unknown>;
  if (m['type'] !== undefined && m['type'] !== 'folders') return null;
  const path = typeof m['path'] === 'string' && m['path'] ? m['path'] : null;
  const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
  const crumbs = list(m['crumbs'])
    .map((c) => (typeof c === 'object' && c !== null ? (c as Record<string, unknown>) : {}))
    .filter((c) => str(c['path']))
    .map((c) => ({ label: str(c['label']) || str(c['path']), path: str(c['path']) }));
  const places = Array.isArray(m['places'])
    ? m['places']
        .map((p) => (typeof p === 'object' && p !== null ? (p as Record<string, unknown>) : {}))
        .filter((p) => str(p['path']))
        .map((p) => ({ label: str(p['label']) || str(p['path']), path: str(p['path']), kind: (KINDS.includes(str(p['kind'])) ? p['kind'] : 'folder') as Place['kind'] }))
    : null;
  const more = typeof m['more'] === 'number' && m['more'] > 0 ? Math.floor(m['more']) : 0;
  return {
    path,
    crumbs,
    folders: list(m['folders']).filter((f): f is string => typeof f === 'string' && f !== ''),
    more,
    shared: typeof m['shared'] === 'string' && m['shared'] ? m['shared'] : null,
    places: path === null ? (places ?? []) : null,
  };
}

// ── Words, tested on their own ───────────────────────────────────────────

const sentence = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);

export function accessText(a: Access): string {
  return a === 'change' ? 'Read and change' : 'Read only';
}

/** Who a share is for: everyone, or one routine by its title. */
export function whoText(s: Pick<ShareRow, 'scope' | 'routine'>): string {
  if (s.scope === 'everyone') return 'Everyone';
  return s.routine ? `Only “${s.routine.title}”` : 'A routine that is gone';
}

/** Why it reaches nothing, capitalised ("Not found"); empty when it is fine. */
export function stateText(s: Pick<ShareRow, 'state'>): string {
  return s.state ? sentence(s.state) : '';
}

/** The line under the section's name in the sidebar. Empty until the shares are read. */
export function sharesSummary(s: SharesState | null): string {
  if (!s) return '';
  return s.shares.length ? `${s.shares.length} shared` : 'None';
}

/** The page's footer: what her file tools reach for her own turns. */
export function footText(s: SharesState | null): string {
  // Nothing until the shares are read: a guess could say folders are shared that are not.
  if (!s) return '';
  const reach = s.shares.filter((r) => r.scope === 'everyone' && !r.state).map((r) => r.name);
  return reach.length
    ? `Files: ${reach.join(', ')}. Every change asks first.`
    : 'No folders are shared, so file tools are off. Share one in Settings → Shared folders, or ask her to find one.';
}

/** A folder inside `dir`, with the path's own separator, so a Windows path never mixes `\` and `/`. */
export function joinPath(dir: string, name: string): string {
  const sep = dir.includes('\\') && !dir.includes('/') ? '\\' : '/';
  return dir.endsWith(sep) ? `${dir}${name}` : `${dir}${sep}${name}`;
}

/** A folder's own name: the last part of its path. */
export function baseName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

/** Where Backspace goes: the folder holding this one, or the places list from a place. */
export function upTarget(f: FolderFrame): { path: string } | { place: 'places' } | null {
  if (f.path === null) return null;
  const above = f.crumbs.length >= 2 ? f.crumbs[f.crumbs.length - 2] : undefined;
  return above ? { path: above.path } : { place: 'places' };
}

/** What the live region says about the folder shown (SUR-6). */
export function shownText(f: FolderFrame): string {
  if (f.path === null) return `Showing your places: ${count(f.places?.length ?? 0, 'place')}.`;
  const here = f.crumbs[f.crumbs.length - 1]?.label ?? baseName(f.path);
  return `Showing ${here}: ${count(f.folders.length + f.more, 'folder')}.`;
}

function count(n: number, what: string): string {
  return n === 0 ? `no ${what}s` : n === 1 ? `1 ${what}` : `${n} ${what}s`;
}

/** Name rules the server holds too, checked here so a mistake is said at once. */
export function nameProblem(name: string): string {
  const n = name.trim();
  if (!n) return 'Give it a name.';
  if (n.length > 60) return 'Use at most 60 characters for the name.';
  if (/[\r\n]/.test(n)) return 'Keep the name on one line.';
  return '';
}

/**
 * Whether two paths name one folder, as the core compares them: case-blind for Windows
 * paths and macOS ones (its home and drives live under /Users and /Volumes), exact
 * elsewhere, where Linux tells `Notes` from `notes`.
 */
export function samePath(a: string, b: string): boolean {
  if (a === b) return true;
  const blind = (p: string): boolean => /^[A-Za-z]:[\\/]|^\\\\/.test(p) || /^\/(Users|Volumes)(\/|$)/.test(p);
  return blind(a) && blind(b) && a.toLowerCase() === b.toLowerCase();
}

// ── The store both use ───────────────────────────────────────────────────

export interface Shares {
  readonly last: SharesState | null;
  /** Why the shares could not be read, when none have been. */
  readonly loadError: string;
  load(): Promise<void>;
  /** Add (`path`) or change (`id`); resolves to an error sentence, or '' when saved. */
  save(body: { path?: string; id?: string; name?: string; access?: Access; scope?: string }): Promise<string>;
  remove(id: string): Promise<string>;
  /** The share of `path`, by its id when the frame names one. */
  find(id: string | null, path: string | null): ShareRow | null;
  onChange(listener: () => void): void;
}

export function createShares(api: Api): Shares {
  let last: SharesState | null = null;
  let loadError = '';
  const listeners: (() => void)[] = [];
  const changed = (): void => listeners.forEach((l) => l());

  const take = (data: unknown): boolean => {
    const d = data as Partial<SharesState> | null;
    if (!d || !Array.isArray(d.shares)) return false;
    last = { shares: d.shares, problem: typeof d.problem === 'string' && d.problem ? d.problem : null };
    return true;
  };

  async function load(): Promise<void> {
    try {
      const r = await api('GET', '/api/folders');
      if (!r.ok || !take(await r.json())) throw new Error(String(r.status));
      loadError = '';
    } catch {
      loadError = last ? '' : 'Your shared folders could not be loaded.';
    }
    changed();
  }

  async function send(method: 'POST' | 'DELETE', path: string, body: unknown): Promise<string> {
    let error = '';
    try {
      const r = await api(method, path, body);
      const data = (await r.json().catch(() => ({}))) as { detail?: unknown; error?: unknown };
      if (!(r.ok && take(data))) error = r.status === 404 ? 'That folder was already not shared.' : refusal(data.detail);
    } catch {
      error = 'Trillion could not be reached.';
    }
    // Refused: show the shares as they are now.
    if (error) await load();
    else changed();
    return error;
  }

  return {
    get last() {
      return last;
    },
    get loadError() {
      return loadError;
    },
    load,
    save: (body) => send('POST', '/api/folders', body),
    // A body, so the request is JSON like every other change.
    remove: (id) => send('DELETE', `/api/folders/${encodeURIComponent(id)}`, {}),
    find(id, path) {
      const all = last?.shares ?? [];
      return (id ? all.find((s) => s.id === id) : undefined) ?? (path ? all.find((s) => samePath(s.path, path)) : undefined) ?? null;
    },
    onChange: (l) => void listeners.push(l),
  };
}

/** The routines a folder can be given to, by title. Empty when they can't be read. */
export async function loadRoutines(api: Api): Promise<Choice[]> {
  try {
    const r = await api('GET', '/api/routines');
    if (!r.ok) return [];
    const data = (await r.json()) as { routines?: { id?: unknown; name?: unknown }[] };
    return (data.routines ?? []).filter((x) => typeof x.id === 'string').map((x) => ({ id: String(x.id), title: str(x.name) || String(x.id) }));
  } catch {
    return [];
  }
}

// ── The form ─────────────────────────────────────────────────────────────

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

export interface FormValues {
  readonly name: string;
  readonly access: Access;
  readonly scope: string;
}

export interface FormOptions {
  /** The folder, shown above the fields so it is plain what is shared. */
  readonly path: string;
  readonly initial: FormValues;
  /** The routine the share is for now, when it is not among `routines`' answer. */
  readonly current?: Choice | null;
  readonly routines: () => Promise<readonly Choice[]>;
  /** The button's words: Share, or Save. */
  readonly submit: string;
  /** Resolves to an error sentence, or '' when it was saved. */
  readonly onSubmit: (v: FormValues) => Promise<string>;
  readonly onCancel: () => void;
}

let forms = 0;

/** Name, access and who, then Share. A div, not a form: Settings already draws inside one. */
export function shareForm(o: FormOptions): HTMLElement {
  const n = ++forms;
  const box = el('div', 'fo-form');
  box.setAttribute('role', 'group');
  box.setAttribute('aria-label', `Share ${o.path}`);
  box.append(el('p', 'fo-form-path', o.path));

  const field = (label: string, control: HTMLInputElement | HTMLSelectElement, id: string): HTMLElement => {
    const row = el('div', 'settings-row');
    const l = el('label', '', label);
    control.id = `fo-${id}-${n}`;
    l.htmlFor = control.id;
    row.append(l, control);
    return row;
  };

  const name = el('input');
  name.type = 'text';
  name.autocomplete = 'off';
  name.spellcheck = false;
  name.maxLength = 60;
  name.value = o.initial.name;
  name.dataset.field = 'name';

  const access = el('select');
  access.dataset.field = 'access';
  for (const a of ['read', 'change'] as const) {
    const opt = el('option', '', accessText(a));
    opt.value = a;
    access.append(opt);
  }
  access.value = o.initial.access;

  const who = el('select');
  who.dataset.field = 'who';
  const everyone = el('option', '', 'Everyone');
  everyone.value = 'everyone';
  who.append(everyone);
  const addRoutine = (c: Choice): void => {
    const value = `routine:${c.id}`;
    if ([...who.options].some((x) => x.value === value)) return;
    const opt = el('option', '', `Only “${c.title}”`);
    opt.value = value;
    who.append(opt);
  };
  if (o.current) addRoutine(o.current);
  if (o.initial.scope.startsWith('routine:') && !o.current) addRoutine({ id: o.initial.scope.slice(8), title: 'A routine that is gone' });
  who.value = o.initial.scope;
  void o.routines().then((list) => {
    const was = who.value;
    list.forEach(addRoutine);
    who.value = was;
  });

  const error = el('p', 'settings-hint connected-warn fo-error');
  error.setAttribute('role', 'alert');
  error.hidden = true;

  let busy = false;
  const go = async (): Promise<void> => {
    if (busy) return;
    const why = nameProblem(name.value);
    if (why) {
      error.textContent = why;
      error.hidden = false;
      name.focus();
      return;
    }
    busy = true;
    ok.disabled = true;
    error.hidden = true;
    const said = await o.onSubmit({ name: name.value.trim(), access: access.value === 'change' ? 'change' : 'read', scope: who.value });
    busy = false;
    ok.disabled = false;
    if (said) {
      error.textContent = said;
      error.hidden = false;
    }
  };
  name.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter') return;
    ev.preventDefault();
    void go();
  });

  const actions = el('div', 'connected-actions fo-form-actions');
  const ok = button(o.submit, 'approve', () => void go());
  ok.dataset.field = 'submit';
  actions.append(ok, button('Cancel', 'quiet', o.onCancel));
  box.append(field('Name', name, 'name'), field('Access', access, 'access'), field('Who', who, 'who'), error, actions);
  return box;
}

// ── The panel ────────────────────────────────────────────────────────────

export interface FolderPanelDeps {
  readonly api: Api;
  readonly shares: Shares;
  readonly routines: () => Promise<readonly Choice[]>;
  /** The conversation on this page, so her browser follows the click. */
  readonly conversation: () => string | null;
  /** Told when it opens or closes, so the page can make room for it. */
  readonly shown?: (open: boolean) => void;
  /** Where focus goes when the panel closes with focus in it. */
  readonly focusAfter?: () => HTMLElement | null;
}

export interface FolderPanel {
  readonly el: HTMLElement;
  /** The live region (SUR-6), for the page to put outside the panel: inside a hidden
   * panel, the first thing it says would go unheard. */
  readonly live: HTMLElement;
  readonly isOpen: boolean;
  /** Show a frame: from the session, or a click's answer. The same frame twice draws once. */
  take(frame: FolderFrame, opts?: { focus?: boolean }): void;
  /** The user's own Add a folder: open at the places list. Opens with the problem shown
   * when it was refused, and resolves to that problem ('' when it opened). */
  openPlaces(): Promise<string>;
  /** The page's Escape, in its turn (escapeAction): the form closes first, then the panel. */
  escape(): void;
  close(): void;
}

const EDITABLE = 'input, select, textarea, [contenteditable="true"]';

export function createFolderPanel(d: FolderPanelDeps): FolderPanel {
  const root = el('aside', 'fo');
  root.hidden = true;
  root.setAttribute('aria-labelledby', 'fo-title');

  const head = el('header', 'fo-head');
  const title = el('h2', 'fo-title', 'Folders');
  title.id = 'fo-title';
  const shut = button('', 'icon-btn fo-close', () => close());
  shut.setAttribute('aria-label', 'Close the folders');
  shut.title = 'Close (Esc)';
  shut.append(icon('close', 16));
  head.append(title, shut);

  const crumbs = el('nav', 'fo-crumbs');
  crumbs.setAttribute('aria-label', 'Where you are');
  const tag = el('p', 'fo-tag');
  const problem = el('p', 'settings-hint connected-warn fo-problem');
  problem.setAttribute('role', 'alert');
  const rows = el('ul', 'fo-rows');
  rows.setAttribute('aria-label', 'Folders in here');
  const more = el('p', 'settings-hint fo-more');
  const formSlot = el('div', 'fo-slot');
  const actions = el('div', 'fo-actions');
  const shareBtn = button('Share this folder…', 'approve', () => openForm());
  actions.append(shareBtn, button('Close', 'quiet', () => close()));
  // Kept across redraws, so a screen reader hears each folder once (SUR-6).
  const live = el('p', 'visually-hidden');
  live.setAttribute('role', 'status');
  live.setAttribute('aria-live', 'polite');
  const body = el('div', 'fo-body');
  body.append(crumbs, tag, problem, rows, more, formSlot, actions);
  root.append(head, body);

  let frame: FolderFrame | null = null;
  let key = '';
  let open = false;
  let asked = 0;
  // The folder Backspace left, so its row has focus in the one above.
  let leaving = '';
  let formOpen = false;

  function setOpen(on: boolean): void {
    if (open === on) return;
    open = on;
    root.hidden = !on;
    d.shown?.(on);
  }

  function close(): void {
    if (!open) return;
    // Focus moves first: a hidden element drops it to the page's body.
    if (root.contains(document.activeElement)) d.focusAfter?.()?.focus();
    formOpen = false;
    formSlot.replaceChildren();
    setOpen(false);
  }

  /** Move the browser: the user's own click, through the same fence as hers. */
  async function go(where: { path: string } | { place: string }, from = ''): Promise<string> {
    const mine = ++asked;
    const conversation = d.conversation();
    let said: string;
    try {
      let r = await d.api('POST', '/api/folders/browse', { ...where, ...(conversation ? { conversation } : {}) });
      // Not found: that conversation's session is gone (reconnecting). The same fence answers without it.
      if (r.status === 404 && conversation) r = await d.api('POST', '/api/folders/browse', where);
      const data = (await r.json().catch(() => ({}))) as Record<string, unknown>;
      // A later click has gone out: this answer is old.
      if (mine !== asked) return '';
      const next = r.ok ? parseFrame(data) : null;
      if (next) {
        leaving = from;
        take(next, { focus: true });
        return '';
      }
      said = r.status === 404 ? 'The folders could not be opened just now. Try again in a moment.' : refusal(data['detail']);
    } catch {
      if (mine !== asked) return '';
      said = 'Trillion could not be reached.';
    }
    problem.textContent = said;
    problem.hidden = false;
    return said;
  }

  function crumb(label: string, onClick: (() => void) | null): HTMLElement {
    const li = el('li', 'fo-crumb');
    if (onClick) li.append(button(label, 'fo-crumb-btn', onClick));
    else {
      const here = el('span', 'fo-crumb-here', label);
      here.setAttribute('aria-current', 'location');
      li.append(here);
    }
    return li;
  }

  function row(label: string, kind: string, target: { path: string }, sharedAs: string, from = ''): HTMLElement {
    const li = el('li');
    const b = button('', 'fo-row', () => void go(target, from));
    b.dataset.path = target.path;
    b.append(icon(kind === 'home' ? 'home' : kind === 'drive' ? 'drive' : 'folders', 16), el('span', 'fo-row-name', label));
    if (sharedAs) b.append(el('span', 'fo-row-tag', `Shared as ${sharedAs}`));
    b.tabIndex = -1;
    li.append(b);
    return li;
  }

  function draw(f: FolderFrame): void {
    const ol = el('ol');
    if (f.path === null) ol.append(crumb('Places', null));
    else {
      ol.append(crumb('Places', () => void go({ place: 'places' })));
      f.crumbs.forEach((c, i) => ol.append(crumb(c.label, i === f.crumbs.length - 1 ? null : () => void go({ path: c.path }))));
    }
    crumbs.replaceChildren(ol);

    const here = f.path !== null ? d.shares.find(f.shared, f.path) : null;
    tag.textContent = here ? `Shared as ${here.name}` : f.shared ? 'Shared' : '';
    tag.hidden = !tag.textContent;
    problem.hidden = true;

    const items: HTMLElement[] = [];
    if (f.path === null) {
      for (const p of f.places ?? []) items.push(row(p.label, p.kind, { path: p.path }, p.kind === 'shared' ? '' : (d.shares.find(null, p.path)?.name ?? '')));
    } else {
      const dir = f.path;
      for (const name of f.folders) {
        const path = joinPath(dir, name);
        items.push(row(name, 'folder', { path }, d.shares.find(null, path)?.name ?? ''));
      }
    }
    rows.replaceChildren(...items);
    const first = rows.querySelector<HTMLButtonElement>('.fo-row');
    if (first) first.tabIndex = 0;
    if (!items.length) rows.append(el('li', 'settings-hint fo-empty', f.path === null ? 'No places found on this computer.' : 'No folders in here.'));
    more.textContent = f.more ? `and ${f.more} more` : '';
    more.hidden = !f.more;
    shareBtn.hidden = f.path === null;
    if (!formOpen || f.path === null) {
      formOpen = false;
      formSlot.replaceChildren();
    }
  }

  function focusRow(): void {
    const all = [...rows.querySelectorAll<HTMLButtonElement>('.fo-row')];
    const target = (leaving && all.find((b) => baseName(b.dataset.path ?? '') === leaving)) || all[0] || shareBtn;
    leaving = '';
    roving(target);
    target.focus();
  }

  function roving(target: HTMLElement): void {
    for (const b of rows.querySelectorAll<HTMLButtonElement>('.fo-row')) b.tabIndex = b === target ? 0 : -1;
  }

  function take(f: FolderFrame, opts: { focus?: boolean } = {}): void {
    const next = JSON.stringify(f);
    const wasOpen = open;
    // The click's answer and the session's frame are the same: drawn once, no flicker.
    if (next !== key || !open) {
      const moved = next !== key;
      if (moved) formOpen = false;
      key = next;
      frame = f;
      draw(f);
      if (moved || !wasOpen) live.textContent = shownText(f);
    }
    setOpen(true);
    if (opts.focus) focusRow();
  }

  function openForm(): void {
    const f = frame;
    if (!f || f.path === null) return;
    const path = f.path;
    const existing = d.shares.find(f.shared, path);
    formOpen = true;
    const done = (): void => {
      formOpen = false;
      formSlot.replaceChildren();
      shareBtn.focus();
    };
    const form = shareForm({
      path,
      initial: {
        name: existing?.name ?? f.crumbs[f.crumbs.length - 1]?.label ?? baseName(path),
        access: existing?.access ?? 'read',
        scope: existing?.scope ?? 'everyone',
      },
      current: existing?.routine ?? null,
      routines: d.routines,
      submit: 'Share',
      onSubmit: async (v) => {
        const error = await d.shares.save({ path, name: v.name, access: v.access, scope: v.scope });
        if (error) return error;
        done();
        if (frame) draw(frame);
        live.textContent = `Shared as ${v.name}.`;
        return '';
      },
      onCancel: done,
    });
    formSlot.replaceChildren(form);
    (form.querySelector('input') as HTMLInputElement | null)?.focus();
  }

  root.addEventListener('keydown', (ev) => {
    const t = ev.target instanceof HTMLElement ? ev.target : null;
    // Escape is the page's, in its one order (escapeAction): it calls escape().
    if (ev.key === 'Escape' || t?.closest(EDITABLE)) return;
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp' || ev.key === 'Home' || ev.key === 'End') {
      const all = [...rows.querySelectorAll<HTMLButtonElement>('.fo-row')];
      if (!all.length) return;
      ev.preventDefault();
      const at = t ? all.indexOf(t as HTMLButtonElement) : -1;
      const i =
        ev.key === 'Home' ? 0 : ev.key === 'End' ? all.length - 1 : at < 0 ? 0 : Math.max(0, Math.min(all.length - 1, at + (ev.key === 'ArrowDown' ? 1 : -1)));
      const target = all[i];
      if (target) {
        roving(target);
        target.focus();
      }
      return;
    }
    if (ev.key === 'Backspace' && frame) {
      const up = upTarget(frame);
      if (!up) return;
      ev.preventDefault();
      void go(up, frame.path ? baseName(frame.path) : '');
    }
  });

  // A share made or removed in Settings changes the tags.
  d.shares.onChange(() => {
    if (open && frame && !formOpen) draw(frame);
  });

  return {
    el: root,
    live,
    get isOpen() {
      return open;
    },
    take,
    async openPlaces() {
      const said = await go({ place: 'places' });
      if (!said) return '';
      // Refused: the panel opens anyway, saying why, rather than nothing happening.
      if (!open) {
        if (!frame) {
          crumbs.replaceChildren();
          rows.replaceChildren();
          tag.hidden = true;
          more.hidden = true;
          shareBtn.hidden = true;
        }
        setOpen(true);
        live.textContent = said;
      }
      return said;
    },
    escape() {
      if (!formOpen) {
        close();
        return;
      }
      formOpen = false;
      formSlot.replaceChildren();
      shareBtn.focus();
    },
    close,
  };
}

// ── Settings → Shared folders ────────────────────────────────────────────

export interface SharedFoldersDeps {
  readonly shares: Shares;
  readonly routines: () => Promise<readonly Choice[]>;
  /** In the desktop app: the system folder picker. Null when it is not there. It rejects
   * with `PickerMissing` when the shell has no picker, or an Error saying what failed. */
  readonly pick: (() => Promise<string | null>) | null;
  /** In a browser: open the Folders panel at the places list; resolves to why it could not ('' when it did). */
  readonly browse: () => Promise<string>;
  readonly changed: () => void;
}

export const PRIVACY =
  'Only folder names are shown to Trillion while you browse, and they go to your model provider so she can follow you. Files in a shared folder are read only when she uses a file tool.';

export function createSharedFolders(host: Host, d: SharedFoldersDeps): { open(): Promise<void>; summary(): string } {
  const form = el('form', 'settings connected folders');
  form.addEventListener('submit', (ev) => ev.preventDefault());
  host.setAttribute('aria-labelledby', 'folders-title');
  host.replaceChildren(form);

  // One card or the add row at a time has the form open: a share's id, or 'add'.
  let editing = '';
  let adding = '';
  let error: { at: string; text: string } | null = null;
  let busy = false;

  d.shares.onChange(() => {
    d.changed();
    if (host.open) draw(false);
  });

  function errorLine(at: string): HTMLElement[] {
    if (error?.at !== at) return [];
    const p = el('p', 'settings-hint connected-warn', error.text);
    p.setAttribute('role', 'alert');
    return [p];
  }

  function card(s: ShareRow): HTMLElement {
    const box = el('div', `connected-card folders-card${s.state ? ' is-missing' : ''}`);
    box.dataset.share = s.id;
    const head = el('div', 'connected-card-head');
    const words = el('span', 'connected-card-words');
    words.append(el('strong', '', s.name), el('small', 'folders-path', s.path));
    const facts = el('small', 'connected-state');
    facts.append(`${accessText(s.access)} · ${whoText(s)}`);
    words.append(facts);
    if (s.state) {
      const st = el('small', 'connected-state connected-warn folders-state');
      st.append(el('span', 'connected-dot connected-dot-bad'), stateText(s));
      words.append(st);
    }
    if (s.run_only) words.append(el('small', 'connected-state folders-run', 'This run only'));
    const side = el('span', 'connected-card-side');
    const change = button('Change', 'quiet', () => {
      editing = s.id;
      adding = '';
      error = null;
      draw();
      (form.querySelector(`[data-share="${CSS.escape(s.id)}"] input`) as HTMLInputElement | null)?.focus();
    });
    change.setAttribute('aria-label', `Change ${s.name}`);
    change.dataset.focus = `change:${s.id}`;
    const remove = button('Remove', 'quiet connected-danger', () => {
      if (busy) return;
      busy = true;
      error = null;
      // Narrowing never asks (D3): it happens at once.
      void d.shares.remove(s.id).then((said) => {
        busy = false;
        if (said) error = { at: s.id, text: said };
        draw();
        (form.querySelector('[data-focus="add"]') as HTMLElement | null)?.focus();
      });
    });
    remove.setAttribute('aria-label', `Remove ${s.name}`);
    side.append(change, remove);
    head.append(words, side);
    box.append(head);
    if (editing === s.id) {
      box.classList.add('is-open');
      box.append(
        shareForm({
          path: s.path,
          initial: { name: s.name, access: s.access, scope: s.scope },
          current: s.routine,
          routines: d.routines,
          submit: 'Save',
          onSubmit: async (v) => {
            const said = await d.shares.save({ id: s.id, name: v.name, access: v.access, scope: v.scope });
            if (!said) {
              editing = '';
              draw();
              (form.querySelector(`[data-focus="${CSS.escape(`change:${s.id}`)}"]`) as HTMLElement | null)?.focus();
            }
            return said;
          },
          onCancel: () => {
            editing = '';
            draw();
            (form.querySelector(`[data-focus="${CSS.escape(`change:${s.id}`)}"]`) as HTMLElement | null)?.focus();
          },
        }),
      );
    }
    box.append(...errorLine(s.id));
    return box;
  }

  let picking = false;

  async function browse(): Promise<void> {
    const said = await d.browse();
    if (said) {
      error = { at: 'add', text: said };
      draw();
    }
  }

  async function add(): Promise<void> {
    // One picker at a time: a second click while it is up does nothing.
    if (picking) return;
    error = null;
    editing = '';
    picking = true;
    try {
      if (!d.pick) {
        await browse();
        return;
      }
      let path: string | null;
      try {
        path = await d.pick();
      } catch (e) {
        // A shell without the picker: the panel does the same job. Anything else is said.
        if (e instanceof PickerMissing) await browse();
        else {
          error = { at: 'add', text: refusal(e instanceof Error ? e.message : String(e)) };
          draw();
        }
        return;
      }
      if (!path) return;
      adding = path;
      draw();
      (form.querySelector('.folders-add input') as HTMLInputElement | null)?.focus();
    } finally {
      picking = false;
    }
  }

  function addRow(): HTMLElement {
    const box = el('div', 'folders-add');
    const b = button('Add a folder', 'approve', () => void add());
    b.dataset.focus = 'add';
    box.append(b);
    if (adding) {
      const path = adding;
      box.append(
        shareForm({
          path,
          initial: { name: baseName(path), access: 'read', scope: 'everyone' },
          routines: d.routines,
          submit: 'Share',
          onSubmit: async (v) => {
            const said = await d.shares.save({ path, name: v.name, access: v.access, scope: v.scope });
            if (!said) {
              adding = '';
              draw();
              (form.querySelector('[data-focus="add"]') as HTMLElement | null)?.focus();
            }
            return said;
          },
          onCancel: () => {
            adding = '';
            draw();
            (form.querySelector('[data-focus="add"]') as HTMLElement | null)?.focus();
          },
        }),
      );
    }
    box.append(...errorLine('add'));
    return box;
  }

  /** `force` false: a change from elsewhere, which never redraws a form being filled in. */
  function draw(force = true): void {
    const active = document.activeElement instanceof HTMLElement && form.contains(document.activeElement) ? document.activeElement : null;
    if (!force && active?.closest('.fo-form')) return;
    const title = el('h2', '', 'Shared folders');
    title.id = 'folders-title';
    const parts: HTMLElement[] = [
      title,
      el(
        'p',
        'settings-note',
        'The only places her file tools can reach. Every change in them asks you first, on a card, and Trillion’s own files, keys and hidden folders such as .ssh stay out of reach even inside them.',
      ),
    ];
    const last = d.shares.last;
    if (!last) {
      parts.push(el('p', 'settings-hint', d.shares.loadError || 'Loading…'));
      form.replaceChildren(...parts);
      return;
    }
    if (last.problem) {
      const warn = el('p', 'settings-warn folders-damaged', last.problem);
      warn.setAttribute('role', 'status');
      parts.push(warn);
    }
    const list = el('div', 'connected-list');
    list.append(...last.shares.map(card));
    if (!last.shares.length) list.append(el('p', 'settings-empty', 'None yet. Her file tools are off until you share a folder, here or by asking her.'));
    parts.push(list);
    // A refusal for a card that is gone is shown under the list.
    if (error && error.at !== 'add' && !last.shares.some((s) => s.id === error?.at)) parts.push(...errorLine(error.at));
    parts.push(addRow(), el('p', 'settings-hint folders-privacy', PRIVACY));
    form.replaceChildren(...parts);
    const key = active?.dataset.focus;
    if (key) (form.querySelector(`[data-focus="${CSS.escape(key)}"]`) as HTMLElement | null)?.focus();
  }

  // Read once at the start, after this turn so the page has its token, for the summary.
  window.setTimeout(() => void d.shares.load(), 0);

  return {
    async open() {
      editing = '';
      adding = '';
      error = null;
      host.showModal();
      draw();
      await d.shares.load();
      draw();
    },
    summary: () => sharesSummary(d.shares.last),
  };
}
