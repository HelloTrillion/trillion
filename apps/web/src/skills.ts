/**
 * Settings → Skills (CON-6): the curated set and the user's folders, each skill a card
 * with a switch. Every skill starts off; one whose files changed is off again until
 * the user looks and turns it back on. Scripts are named, and never run.
 *
 * Turning a skill on sends the fingerprint its card showed, so files that changed
 * since are refused rather than approved unseen.
 */

import type { Host } from './panel';
import type { Api } from './settings';

// ── What the server sends ────────────────────────────────────────────────

export interface SkillTool {
  readonly name: string;
  readonly available: boolean;
  /** What would make it available: "google" until a Google account is connected. */
  readonly needs?: string;
}

export interface SkillRow {
  readonly id: string;
  readonly source: 'curated' | 'folder';
  readonly folder: string;
  readonly name: string;
  readonly description: string;
  readonly license: string;
  readonly tools: readonly SkillTool[];
  readonly has_scripts: boolean;
  readonly state: 'off' | 'on' | 'changed' | 'invalid';
  readonly reason: string;
  readonly fingerprint: string;
}

export interface SkillsState {
  readonly folders: readonly { readonly path: string; readonly found: boolean }[];
  readonly skills: readonly SkillRow[];
  /** What leaves skills the user turned on unused: a damaged file, a lost folder. */
  readonly problems: readonly string[];
  /** Turned on, but past what her instructions list. */
  readonly unlisted: number;
}

// ── Words, tested on their own ───────────────────────────────────────────

export function skillState(s: Pick<SkillRow, 'state' | 'reason'>): string {
  if (s.state === 'on') return 'On';
  if (s.state === 'changed') return 'Changed since you turned it on';
  if (s.state === 'invalid') return s.reason ? `Can't be used: ${s.reason}` : "Can't be used";
  return 'Off';
}

/** The line under the section's name in the sidebar. */
export function skillsSummary(s: Pick<SkillsState, 'skills'> | null): string {
  const on = s?.skills.filter((k) => k.state === 'on').length ?? 0;
  const changed = s?.skills.filter((k) => k.state === 'changed').length ?? 0;
  if (!on && !changed) return 'None on';
  return changed ? `${on} on, ${changed} changed` : `${on} on`;
}

/** A tool a skill mentions, and what it needs when she doesn't have it. */
export function toolLabel(t: SkillTool): string {
  if (t.available) return t.name;
  if (t.needs === 'google') return `${t.name} (connect Google)`;
  return `${t.name} (not available in Trillion)`;
}

/** Skills that are on but past what her instructions list (50, or about 6,000 characters). */
export function unlistedText(n: number): string {
  if (n <= 0) return '';
  return n === 1 ? '1 more skill is on than her instructions list' : `${n} more skills are on than her instructions list`;
}

const sentence = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);

/** A refusal from the server, as a sentence: capitalised, with one full stop. */
export function refusal(detail: unknown): string {
  const text = typeof detail === 'string' ? detail.trim().replace(/[.\s]+$/, '') : '';
  return `${sentence(text || 'That did not work')}.`;
}

/** The file to read again when a skill changed: its SKILL.md, relative to the folder
 * the user added when it is inside it, else in full. Curated skills have none. */
export function changedFile(s: Pick<SkillRow, 'id' | 'source' | 'folder'>): string {
  if (s.source !== 'folder') return '';
  const path = s.id.slice('folder:'.length);
  const root = s.folder.replace(/\/+$/, '');
  const inside = root && path.startsWith(`${root}/`) ? path.slice(root.length + 1) : '';
  return `${inside || path}/SKILL.md`;
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

export function createSkills(host: Host, api: Api, changed: () => void): { open(): Promise<void>; reveal(id: string): void; summary(): string } {
  let last: SkillsState | null = null;
  let loadError = '';
  // A refusal, shown where it happened: on a card (its id), or under the folders ('folders').
  let error: { at: string; text: string } | null = null;
  let busy = false;
  let focus = '';
  let confirming = '';
  let draft = '';

  const form = el('form', 'settings connected skills');
  form.addEventListener('submit', (ev) => ev.preventDefault());
  host.setAttribute('aria-labelledby', 'skills-title');
  host.replaceChildren(form);

  async function load(): Promise<void> {
    try {
      const r = await api('GET', '/api/skills');
      if (!r.ok) throw new Error(String(r.status));
      last = (await r.json()) as SkillsState;
      loadError = '';
    } catch {
      loadError = last ? '' : 'The skills could not be loaded.';
    }
    changed();
  }

  /** Change something, then show the list as it is now, refused or not. */
  async function send(at: string, method: 'POST' | 'PATCH' | 'DELETE', path: string, body: unknown): Promise<boolean> {
    busy = true;
    error = null;
    let ok = false;
    try {
      const r = await api(method, path, body);
      const data = (await r.json().catch(() => ({}))) as Partial<SkillsState> & { detail?: unknown };
      if (r.ok && Array.isArray(data.skills)) {
        last = data as SkillsState;
        ok = true;
      } else {
        error = { at, text: refusal(data.detail) };
      }
    } catch {
      error = { at, text: 'Trillion could not be reached.' };
    }
    // A refusal can mean the files moved on: show the cards as they are now.
    if (!ok) await load();
    busy = false;
    changed();
    draw();
    return ok;
  }

  function errorLine(at: string): HTMLElement[] {
    if (error?.at !== at) return [];
    const p = el('p', 'settings-hint connected-warn', error.text);
    p.setAttribute('role', 'alert');
    return [p];
  }

  let ids = 0;

  function card(s: SkillRow, grouped = false): HTMLElement {
    const box = el('div', `connected-card skills-card is-${s.state}${focus === s.id ? ' is-shown' : ''}`);
    box.dataset.skill = s.id;
    const head = el('div', 'connected-card-head');
    const words = el('span', 'connected-card-words');
    const state = el('small', 'connected-state');
    state.id = `skill-state-${++ids}`;
    const said = el('span', 'connected-state-main');
    const kind = s.state === 'on' ? 'on' : s.state === 'off' ? 'off' : 'bad';
    said.append(el('span', `connected-dot connected-dot-${kind}`), document.createTextNode(skillState(s)));
    state.append(said);
    words.append(el('strong', '', s.name));
    if (s.description) words.append(el('small', 'skills-description', s.description));
    words.append(state);

    const sw = el('label', 'connected-switch');
    const input = el('input');
    input.type = 'checkbox';
    input.setAttribute('role', 'switch');
    input.checked = s.state === 'on';
    input.disabled = s.state === 'invalid';
    input.dataset.focus = `switch:${s.id}`;
    input.setAttribute('aria-label', `Use ${s.name}`);
    input.setAttribute('aria-describedby', state.id);
    input.addEventListener('change', () => {
      // One change at a time: a second click while the first is saving is undone.
      if (busy) {
        input.checked = !input.checked;
        return;
      }
      void send(s.id, 'PATCH', '/api/skills', { id: s.id, on: input.checked, fingerprint: s.fingerprint || null });
    });
    sw.append(input, el('span', 'connected-switch-track'));
    head.append(words, sw);
    box.append(head);

    const facts = el('ul', 'skills-facts');
    // Cards under a folder's row don't repeat it.
    if (!grouped) facts.append(el('li', '', s.source === 'curated' ? 'Included with Trillion' : `From ${s.folder}`));
    if (s.tools.length) facts.append(el('li', '', `Tools it mentions: ${s.tools.map(toolLabel).join(', ')}`));
    if (s.has_scripts) facts.append(el('li', 'connected-warn', "Includes scripts Trillion won't run"));
    if (s.license) facts.append(el('li', '', `License: ${s.license}`));
    box.append(facts);
    if (s.state === 'changed') {
      const where = changedFile(s) || 'it';
      box.append(el('p', 'settings-hint connected-warn', `Its files changed, so she stopped using it. Read ${where} again, then turn it back on if you still trust it.`));
    }
    box.append(...errorLine(s.id));
    return box;
  }

  function folderBlock(f: SkillsState['folders'][number], skills: readonly SkillRow[]): HTMLElement {
    const block = el('div', 'skills-folder-block');
    const row = el('div', 'skills-folder');
    const path = el('span', 'skills-folder-path', f.path);
    row.append(path);
    if (!f.found) row.append(el('small', 'connected-warn', 'Not found'));
    if (confirming === f.path) {
      const confirm = el('div', 'connected-confirm');
      confirm.append(
        el('span', '', 'Remove this folder? Its skills go with it, and any you turned on are turned off.'),
        button('Keep it', 'quiet', () => {
          confirming = '';
          draw();
          (form.querySelector(`[data-focus="${CSS.escape(`remove:${f.path}`)}"]`) as HTMLElement | null)?.focus();
        }),
        button('Remove', 'connected-danger-solid', () => {
          if (busy) return;
          confirming = '';
          // Its row is gone afterwards: focus goes to adding a folder.
          void send('folders', 'DELETE', '/api/skills/folders', { path: f.path }).then(() => {
            (form.querySelector('[data-focus="add-input"]') as HTMLElement | null)?.focus();
          });
        }),
      );
      block.append(row, confirm);
    } else {
      const remove = button('Remove', 'quiet connected-danger', () => {
        confirming = f.path;
        draw();
        (form.querySelector('.connected-confirm button') as HTMLButtonElement | null)?.focus();
      });
      remove.setAttribute('aria-label', `Remove the folder ${f.path}`);
      remove.dataset.focus = `remove:${f.path}`;
      row.append(remove);
      block.append(row);
    }
    if (f.found && !skills.length) block.append(el('p', 'settings-hint', 'No skills found in it. A skill is a folder with a SKILL.md file.'));
    if (skills.length) {
      const list = el('div', 'connected-list');
      list.append(...skills.map((k) => card(k, true)));
      block.append(list);
    }
    return block;
  }

  function addFolder(): HTMLElement {
    const row = el('div', 'settings-row skills-add');
    const label = el('label', '', 'Add a folder');
    const input = el('input');
    input.id = 'skills-folder-path';
    input.type = 'text';
    input.placeholder = '~/.claude/skills';
    input.autocomplete = 'off';
    input.spellcheck = false;
    input.value = draft;
    input.dataset.focus = 'add-input';
    label.htmlFor = input.id;
    const go = (): void => {
      const path = input.value.trim();
      if (!path || busy) return;
      void send('folders', 'POST', '/api/skills/folders', { path }).then((ok) => {
        if (ok) draft = '';
      });
    };
    input.addEventListener('input', () => {
      draft = input.value;
    });
    input.addEventListener('keydown', (ev) => {
      if (ev.key !== 'Enter') return;
      ev.preventDefault();
      go();
    });
    const add = button('Add folder', 'approve', go);
    add.dataset.focus = 'add-button';
    row.append(label, input, add, el('small', 'settings-hint', 'A folder of skills, or one skill’s own folder. Each skill in it starts off.'));
    return row;
  }

  function draw(): void {
    const active = document.activeElement instanceof HTMLElement && form.contains(document.activeElement) ? document.activeElement.dataset.focus : undefined;
    const title = el('h2', '', 'Skills');
    title.id = 'skills-title';
    const parts: HTMLElement[] = [
      title,
      el('p', 'settings-note', 'Instructions she reads when a task calls for one. Each starts off: turn on the ones you trust. A skill can tell her how to do something, never let her skip asking you first.'),
    ];
    if (!last) {
      parts.push(el('p', 'settings-hint', loadError || 'Loading…'));
      form.replaceChildren(...parts);
      return;
    }
    const warnings = [...last.problems.map((p) => `${sentence(p)}.`)];
    const over = unlistedText(last.unlisted);
    if (over) warnings.push(`${over}, so she won't know about them. Turn off the ones you use least.`);
    if (warnings.length) {
      const box = el('div', 'settings-warn skills-warn');
      box.setAttribute('role', 'status');
      const ul = el('ul');
      ul.append(...warnings.map((w) => el('li', '', w)));
      box.append(ul);
      parts.push(box);
    }

    // A refusal for a card that is no longer there is shown here instead.
    if (error && error.at !== 'folders' && !last.skills.some((k) => k.id === error?.at)) parts.push(...errorLine(error.at));

    const curated = last.skills.filter((s) => s.source === 'curated');
    parts.push(el('h3', 'connected-heading', 'Included with Trillion'));
    const list = el('div', 'connected-list');
    list.append(...curated.map((k) => card(k, true)));
    parts.push(list);

    parts.push(el('h3', 'connected-heading', 'Your folders'));
    const mine = last.skills.filter((s) => s.source === 'folder');
    if (!last.folders.length) parts.push(el('p', 'settings-hint', 'No folders yet. Add one that holds skills, such as ~/.claude/skills.'));
    const shown = new Set<string>();
    for (const f of last.folders) {
      const its = mine.filter((s) => s.folder === f.path);
      its.forEach((s) => shown.add(s.id));
      parts.push(folderBlock(f, its));
    }
    const rest = mine.filter((s) => !shown.has(s.id));
    if (rest.length) {
      const more = el('div', 'connected-list');
      more.append(...rest.map((k) => card(k)));
      parts.push(more);
    }
    parts.push(addFolder(), ...errorLine('folders'));
    form.replaceChildren(...parts);

    if (active) (form.querySelector(`[data-focus="${CSS.escape(active)}"]`) as HTMLElement | null)?.focus();
  }

  // Once at the start, so the sidebar says how many are on before the section is opened.
  // After this turn, so the page has taken its session token from the address first.
  window.setTimeout(() => void load(), 0);

  return {
    async open() {
      host.showModal();
      draw();
      await load();
      draw();
      if (focus) {
        const target = form.querySelector(`[data-skill="${CSS.escape(focus)}"]`);
        window.setTimeout(() => target?.scrollIntoView({ block: 'center' }), 300);
        focus = '';
      }
    },
    /** Open at one skill's card: a changed skill links straight to it (#show=skill:<id>). */
    reveal(id: string) {
      focus = id;
    },
    summary: () => skillsSummary(last),
  };
}
