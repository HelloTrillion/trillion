/**
 * Settings, as a screen of its own: a sidebar of sections in groups, with search, and
 * the chosen section in a wide column beside it. Escape or the close button returns
 * to the conversation.
 *
 * Each section is drawn by its own module. The model, voice and microphone modules
 * were written for a dialog of their own; here each gets a `Host` that looks like
 * one, so they keep their checks, their wording and their `open()`. Opening one of
 * them opens Settings at that section, which is how onboarding still works.
 */

import { icon } from './icons';

/** What a section needs from where it is drawn. A <dialog> already is one. */
export interface Host {
  readonly open: boolean;
  showModal(): void;
  close(): void;
  replaceChildren(...nodes: (Node | string)[]): void;
  setAttribute(name: string, value: string): void;
  addEventListener(type: 'close', listener: () => void): void;
}

export interface SectionSpec {
  readonly id: string;
  readonly label: string;
  readonly group: string;
  readonly icon: string;
  /** More words a search should find this section by. */
  readonly keywords?: string;
  /** Shown under the label: the current choice, in a word or two. */
  readonly summary?: () => string;
  /** Called when the section is chosen. It fills the section and asks to be shown. */
  readonly open: () => void | Promise<void>;
}

export interface Panel {
  /** A host for a section. Showing it opens Settings at that section. */
  host(id: string): Host;
  add(section: SectionSpec): void;
  /** Open at a section, or at the first one. */
  open(id?: string): void;
  /** Refresh the summaries under each label. */
  refresh(): void;
  readonly isOpen: boolean;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** The words a search compares against, folded so "Mic" finds "Microphone". */
const fold = (s: string): string => s.toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '');

export function createPanel(dialog: HTMLDialogElement): Panel {
  dialog.setAttribute('aria-label', 'Settings');

  // The sidebar: title, search, and the groups of sections.
  const rail = el('aside', 'panel-rail');
  const heading = el('h2', 'panel-title', 'Settings');
  const searchWrap = el('label', 'panel-search');
  const search = el('input');
  search.type = 'search';
  search.placeholder = 'Search settings';
  search.setAttribute('aria-label', 'Search settings');
  search.autocomplete = 'off';
  search.spellcheck = false;
  searchWrap.append(icon('search', 16), search);
  const nav = el('nav', 'panel-nav');
  nav.setAttribute('aria-label', 'Settings sections');
  const nothing = el('p', 'panel-nothing', 'Nothing matches.');
  nothing.hidden = true;
  rail.append(heading, searchWrap, nav, nothing);

  // The main column: where you are, a way back, and the section itself.
  const main = el('main', 'panel-main');
  const bar = el('header', 'panel-bar');
  const crumb = el('p', 'panel-crumb');
  const shut = el('button', 'panel-close');
  shut.type = 'button';
  shut.setAttribute('aria-label', 'Close settings');
  shut.title = 'Close (Esc)';
  shut.append(icon('close', 18), el('kbd', '', 'Esc'));
  shut.addEventListener('click', () => dialog.close());
  bar.append(crumb, shut);
  const body = el('div', 'panel-body');
  main.append(bar, body);
  dialog.replaceChildren(rail, main);

  const sections: SectionSpec[] = [];
  const groups = new Map<string, HTMLElement>();
  const panes = new Map<string, HTMLElement>();
  const links = new Map<string, HTMLButtonElement>();
  let current = '';

  function pane(id: string): HTMLElement {
    let found = panes.get(id);
    if (!found) {
      found = el('section', 'panel-pane');
      found.hidden = true;
      panes.set(id, found);
      body.append(found);
    }
    return found;
  }

  function refresh(): void {
    for (const s of sections) {
      const small = links.get(s.id)?.querySelector('small');
      if (small) small.textContent = s.summary?.() ?? '';
    }
  }

  function show(id: string): void {
    current = id;
    for (const [key, p] of panes) p.hidden = key !== id;
    for (const [key, link] of links) {
      if (key === id) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
    const s = sections.find((x) => x.id === id);
    crumb.replaceChildren(el('span', '', 'Settings'), el('span', 'panel-crumb-sep', '/'), el('strong', '', s?.label ?? ''));
    refresh();
    body.scrollTop = 0;
    if (!dialog.open) {
      dialog.showModal();
      // Start on the section shown, not on the close button.
      links.get(id)?.focus();
    }
  }

  function filter(): void {
    const q = fold(search.value.trim());
    let shownAny = false;
    for (const s of sections) {
      const hay = fold(`${s.label} ${s.group} ${s.keywords ?? ''} ${s.summary?.() ?? ''}`);
      const match = !q || q.split(/\s+/).every((w) => hay.includes(w));
      const item = links.get(s.id)?.parentElement;
      if (item) item.hidden = !match;
      shownAny ||= match;
    }
    for (const g of groups.values()) {
      g.hidden = [...g.querySelectorAll('li')].every((li) => li.hidden);
    }
    nothing.hidden = shownAny;
  }

  search.addEventListener('input', filter);
  search.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter') return;
    ev.preventDefault();
    const first = sections.find((s) => !links.get(s.id)?.parentElement?.hidden);
    if (first) void first.open();
  });
  dialog.addEventListener('close', () => {
    search.value = '';
    filter();
  });

  return {
    host(id) {
      const p = pane(id);
      return {
        get open() {
          return dialog.open && current === id;
        },
        showModal: () => show(id),
        close: () => dialog.close(),
        replaceChildren: (...nodes) => p.replaceChildren(...nodes),
        setAttribute: (name, value) => p.setAttribute(name, value),
        addEventListener: (_type, listener) => dialog.addEventListener('close', listener),
      };
    },
    add(section) {
      sections.push(section);
      pane(section.id);
      let group = groups.get(section.group);
      if (!group) {
        group = el('div', 'panel-group');
        group.append(el('p', 'panel-group-label', section.group), el('ul'));
        groups.set(section.group, group);
        nav.append(group);
      }
      const item = el('li');
      const link = el('button', 'panel-link');
      link.type = 'button';
      const words = el('span', 'panel-link-words');
      words.append(el('span', '', section.label), el('small'));
      link.append(icon(section.icon), words);
      link.addEventListener('click', () => void section.open());
      links.set(section.id, link);
      item.append(link);
      group.querySelector('ul')?.append(item);
      refresh();
    },
    open(id) {
      const target = sections.find((s) => s.id === id) ?? sections[0];
      refresh();
      if (target) void target.open();
    },
    refresh,
    get isOpen() {
      return dialog.open;
    },
  };
}
