/**
 * The Settings sections that need nothing from Trillion's core: Shortcuts and
 * Appearance (kept in this browser), and two that only show what the page already
 * knows: the shared folders and what is running.
 */

import { type Hotkey, type Mode, SPACE, type TalkPrefs, fromKeydown, fromModifierRelease, isModifier, keycaps, refused } from './hotkey';
import type { Host } from './panel';
import { MAC, shortcut } from './platform';
import type { Theme } from './prefs';
import { type Usage, breakdown, sentence } from './usage';

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function section(host: Host, id: string, heading: string, intro: string): { form: HTMLFormElement; finish: (...nodes: Node[]) => void } {
  const form = el('form', 'settings');
  form.addEventListener('submit', (ev) => ev.preventDefault());
  const title = el('h2', '', heading);
  title.id = `${id}-title`;
  host.setAttribute('aria-labelledby', title.id);
  form.append(title, el('p', 'settings-note', intro));
  return {
    form,
    finish: (...nodes) => {
      const actions = el('div', 'settings-actions');
      const close = el('button', 'settings-close', 'Done');
      close.type = 'button';
      close.addEventListener('click', () => host.close());
      actions.append(close);
      form.append(...nodes, actions);
      host.replaceChildren(form);
    },
  };
}

function radios<T extends string>(
  name: string,
  label: string,
  options: readonly { readonly value: T; readonly title: string; readonly note: string }[],
  choose: (value: T) => void,
): { element: HTMLElement; set: (value: T) => void } {
  const kinds = el('div', 'settings-kinds');
  kinds.setAttribute('role', 'radiogroup');
  kinds.setAttribute('aria-label', label);
  const inputs: HTMLInputElement[] = [];
  for (const o of options) {
    const row = el('label', 'settings-kind');
    const input = el('input');
    input.type = 'radio';
    input.name = name;
    input.value = o.value;
    input.addEventListener('change', () => choose(o.value));
    inputs.push(input);
    const words = el('span');
    words.append(el('strong', '', o.title), el('small', '', o.note));
    row.append(input, words);
    kinds.append(row);
  }
  return {
    element: kinds,
    set: (value) => {
      for (const input of inputs) input.checked = input.value === value;
    },
  };
}


// ── Shortcuts ────────────────────────────────────────────────────────────

export function createShortcuts(
  host: Host,
  current: () => TalkPrefs,
  choose: (prefs: TalkPrefs) => void,
): { open(): void; summary(): string } {
  const { finish } = section(
    host,
    'shortcuts',
    'Shortcuts',
    "Talk to her without reaching for the mouse. Shortcuts work while Trillion's tab is in front; one that works from any app comes with the desktop app.",
  );

  const card = el('div', 'settings-card');
  const cardHead = el('div', 'settings-card-head');
  cardHead.append(el('strong', '', 'Talk'), el('small', '', 'Starts listening, and sends what she heard.'));
  const caps = el('div', 'keycaps');
  caps.setAttribute('aria-live', 'polite');
  const buttons = el('div', 'settings-buttons');
  const record = el('button', 'approve', 'Record a shortcut');
  record.type = 'button';
  const space = el('button', '', 'Use Space');
  space.type = 'button';
  const off = el('button', 'quiet', 'Turn off');
  off.type = 'button';
  buttons.append(record, space, off);
  const status = el('p', 'settings-status');
  status.setAttribute('role', 'status');
  const fn = el(
    'p',
    'settings-hint',
    'Any key or combination works: a modifier on its own (such as Right Option), Control or Command with a key, a function key, or Space. The Fn key cannot be used here: macOS never shows it to a web page.',
  );
  card.append(cardHead, caps, buttons, status, fn);

  const how = radios<Mode>(
    'talk-mode',
    'How the shortcut works',
    [
      { value: 'toggle', title: 'Press to talk', note: 'Press once to start listening, again to send.' },
      { value: 'hold', title: 'Hold to talk', note: 'Hold it while you speak; let go to send.' },
    ],
    (mode) => {
      choose({ ...current(), mode });
      draw();
    },
  );
  const howCard = el('div', 'settings-card');
  const howHead = el('div', 'settings-card-head');
  howHead.append(el('strong', '', 'How it works'));
  howCard.append(howHead, how.element);

  // The ones every window has, written the way this computer writes them.
  const builtIn = el('div', 'settings-card');
  const builtInHead = el('div', 'settings-card-head');
  builtInHead.append(el('strong', '', 'Built in'));
  const list = el('dl', 'settings-shortcuts');
  for (const [keys, what] of [
    [shortcut(','), 'Open Settings'],
    [shortcut('K'), 'Go to the message box'],
    ['Esc', 'Stop her, or close Settings'],
  ] as const) {
    const dt = el('dt');
    dt.append(el('kbd', '', keys));
    list.append(dt, el('dd', '', what));
  }
  builtIn.append(builtInHead, list);

  finish(card, howCard, builtIn);

  function draw(): void {
    const prefs = current();
    if (recording) {
      caps.replaceChildren(el('span', 'keycaps-waiting', 'Press the keys you want… (Esc to cancel)'));
    } else if (prefs.hotkey) {
      caps.replaceChildren(...keycaps(prefs.hotkey, MAC).map((k) => el('kbd', '', k)));
    } else {
      caps.replaceChildren(el('span', 'keycaps-none', 'No shortcut. Only the Speak button starts listening.'));
    }
    record.textContent = recording ? 'Cancel' : 'Record a shortcut';
    how.set(prefs.mode);
  }

  // Recording: the next key, or combination, pressed anywhere on the page.
  let recording = false;
  let modifierDown = '';
  let other = false;

  function stop(): void {
    recording = false;
    modifierDown = '';
    other = false;
    removeEventListener('keydown', onDown, true);
    removeEventListener('keyup', onUp, true);
    draw();
  }

  function take(h: Hotkey | null): void {
    if (!h) return;
    const why = refused(h);
    if (why) {
      status.textContent = why;
      stop();
      return;
    }
    choose({ ...current(), hotkey: h });
    status.textContent = `Saved. ${current().mode === 'hold' ? 'Hold' : 'Press'} ${keycaps(h, MAC).join(' ')} to talk.`;
    stop();
  }

  function onDown(ev: KeyboardEvent): void {
    // Everything goes to the recorder, including what would close Settings.
    ev.preventDefault();
    ev.stopPropagation();
    if (ev.repeat) return;
    if (ev.code === 'Escape' && !ev.ctrlKey && !ev.altKey && !ev.metaKey && !ev.shiftKey) {
      status.textContent = 'Nothing changed.';
      stop();
      return;
    }
    if (isModifier(ev.code)) {
      modifierDown ||= ev.code;
      return;
    }
    other = true;
    take(fromKeydown({ code: ev.code, ctrl: ev.ctrlKey, alt: ev.altKey, shift: ev.shiftKey, meta: ev.metaKey }));
  }

  function onUp(ev: KeyboardEvent): void {
    ev.preventDefault();
    ev.stopPropagation();
    // A modifier pressed and let go on its own: that modifier is the shortcut.
    if (ev.code === modifierDown && !other) take(fromModifierRelease(ev.code));
  }

  record.addEventListener('click', () => {
    if (recording) {
      status.textContent = 'Nothing changed.';
      stop();
      return;
    }
    recording = true;
    status.textContent = '';
    addEventListener('keydown', onDown, true);
    addEventListener('keyup', onUp, true);
    record.focus();
    draw();
  });
  space.addEventListener('click', () => {
    choose({ ...current(), hotkey: SPACE });
    status.textContent = 'Space is the shortcut again.';
    draw();
  });
  off.addEventListener('click', () => {
    choose({ ...current(), hotkey: null });
    status.textContent = 'Turned off.';
    draw();
  });
  host.addEventListener('close', () => {
    if (recording) stop();
    status.textContent = '';
  });

  return {
    open() {
      draw();
      host.showModal();
    },
    summary() {
      const h = current().hotkey;
      return h ? keycaps(h, MAC).join(' ') : 'Off';
    },
  };
}

// ── Appearance ───────────────────────────────────────────────────────────

export function createAppearance(host: Host, current: () => Theme, choose: (t: Theme) => void): { open(): void } {
  const { finish } = section(host, 'appearance', 'Appearance', 'Kept in this browser only.');
  const theme = radios<Theme>(
    'appearance',
    'Theme',
    [
      { value: 'system', title: 'Match system', note: 'Light or dark, as this computer is set.' },
      { value: 'dark', title: 'Dark', note: 'The galaxy at night.' },
      { value: 'light', title: 'Light', note: 'The same, on a pale sky.' },
    ],
    choose,
  );
  finish(theme.element);
  return {
    open() {
      theme.set(current());
      host.showModal();
    },
  };
}

// ── Shared folders ───────────────────────────────────────────────────────

export function createFolders(host: Host, roots: () => readonly string[]): { open(): void } {
  const { finish } = section(
    host,
    'folders',
    'Shared folders',
    'The only places her file tools can reach. Every change in them asks you first, on a card, and Trillion’s own files, keys and hidden folders such as .ssh stay out of reach even inside them.',
  );
  const list = el('ul', 'settings-list');
  const how = el('p', 'settings-hint');
  finish(list, how);
  return {
    open() {
      const found = roots();
      list.replaceChildren(
        ...(found.length ? found.map((r) => el('li', 'settings-path', r)) : [el('li', 'settings-empty', 'None. Her file tools are off.')]),
      );
      how.textContent =
        'Folders are shared when Trillion starts. To share one, start it with: trillion run --grant <folder>. Repeat --grant for more.';
      host.showModal();
    },
  };
}

// ── API usage ────────────────────────────────────────────────────────────

export function createUsageSettings(host: Host, load: () => Promise<Usage | null>): { open(): Promise<void> } {
  const { finish } = section(
    host,
    'usage',
    'API usage',
    'What the model providers report for this month so far. A model without prices counts tokens only. Voice services are not counted yet.',
  );
  const list = el('ul', 'settings-list');
  const total = el('p', 'settings-hint');
  finish(list, total);
  return {
    async open() {
      const u = await load();
      const rows = u ? breakdown(u) : [];
      list.replaceChildren(
        ...(rows.length
          ? rows.map((r) => {
              const item = el('li', 'settings-path', r.name);
              item.append(el('br'), el('span', 'settings-hint', r.detail));
              return item;
            })
          : [el('li', 'settings-empty', u ? 'Nothing yet this month.' : 'Usage could not be read right now.')]),
      );
      total.textContent = u ? sentence(u) : '';
      host.showModal();
    },
  };
}

// ── About ────────────────────────────────────────────────────────────────

export function createAbout(host: Host, facts: () => { version: string; model: string }): { open(): void } {
  const { finish } = section(
    host,
    'about',
    'About',
    'Trillion runs on this computer. What you say goes only to the providers you chose: the model, and her voice and ears if you turned them on.',
  );
  const table = el('dl', 'settings-facts');
  const help = el('p', 'settings-hint', 'If something is not working, run trillion doctor in a terminal: it checks this machine and says how to fix what it finds.');
  finish(table, help);
  return {
    open() {
      const f = facts();
      table.replaceChildren(el('dt', '', 'Version'), el('dd', '', f.version || 'unknown'), el('dt', '', 'Model'), el('dd', '', f.model));
      host.showModal();
    },
  };
}
