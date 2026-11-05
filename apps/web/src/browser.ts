/**
 * The Settings sections that need nothing from Trillion's core: Shortcuts and
 * Appearance (kept in this browser), and those that only show what the page already
 * knows: what is running and what it costs. And the Board's one switch, which the
 * core keeps. Shared folders are in folders.ts.
 */

import { type Hotkey, type Mode, SPACE, type TalkPrefs, fromKeydown, fromModifierRelease, globalAccelerator, isModifier, keycaps, refused } from './hotkey';
import type { Host } from './panel';
import type { Api } from './settings';
import { MAC, shortcut } from './platform';
import type { Detail, Theme } from './prefs';
import { createProviderBlocks } from './spend';
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

/**
 * In the desktop app, the line under the talk shortcut: whether it works from any app.
 * `global` is the shell's answer, null until it has given one. Nothing in a tab.
 */
export function reachLine(app: boolean, hotkey: Hotkey | null, global: boolean | null): string {
  if (!app) return '';
  if (!hotkey || globalAccelerator(hotkey) === null) return 'To talk from any app, record a combination such as ⌥Space.';
  if (global === null) return '';
  return global
    ? 'Works from any app, even while this window is hidden.'
    : "That combination couldn't be used from other apps; try another, such as ⌥Space.";
}

export function createShortcuts(
  host: Host,
  current: () => TalkPrefs,
  choose: (prefs: TalkPrefs) => void,
  /**
   * In the desktop app: whether the shell registered the shortcut globally (null: not yet
   * known), and recording starting and ending, so the old shortcut is let go meanwhile.
   */
  app?: { global(): boolean | null; recording(on: boolean): void },
): { open(): void; summary(): string; redraw(): void } {
  const { finish } = section(
    host,
    'shortcuts',
    'Shortcuts',
    app
      ? 'Talk to her without reaching for the mouse. A combination works from any app, even while this window is hidden.'
      : "Talk to her without reaching for the mouse. Shortcuts work while Trillion's tab is in front; one that works from any app comes with the desktop app.",
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
  const reach = el('p', 'settings-hint');
  const fn = el(
    'p',
    'settings-hint',
    'Any key or combination works: a modifier on its own (such as Right Option), Control or Command with a key, a function key, or Space. The Fn key cannot be used here: macOS never shows it to a web page.',
  );
  card.append(cardHead, caps, reach, buttons, status, fn);

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
    reach.textContent = recording ? '' : reachLine(app !== undefined, prefs.hotkey, app?.global() ?? null);
    reach.hidden = !reach.textContent;
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
    app?.recording(false);
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
    app?.recording(true);
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
    redraw: draw,
  };
}

// ── Appearance ───────────────────────────────────────────────────────────

const THEME_WORDS: Readonly<Record<Theme, string>> = { system: 'Match system', dark: 'Dark', light: 'Light' };
const DETAIL_WORDS: Readonly<Record<Detail, string>> = { auto: 'auto performance', smooth: 'always smooth', full: 'full detail' };

/** The Appearance line in Settings: the theme, then the performance choice. */
export function appearanceSummary(theme: Theme, detail: Detail): string {
  return `${THEME_WORDS[theme]}, ${DETAIL_WORDS[detail]}`;
}

/** The galaxy's detail, kept in this browser (prefs.ts). */
export interface DetailChoice {
  get(): Detail;
  set(detail: Detail): void;
}

export function createAppearance(host: Host, current: () => Theme, choose: (t: Theme) => void, detail?: DetailChoice): { open(): void } {
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
  const perf = detail
    ? radios<Detail>(
        'detail',
        'Performance',
        [
          { value: 'auto', title: 'Automatic', note: 'Full detail, drawn lighter if this computer starts to drop frames.' },
          { value: 'smooth', title: 'Always smooth', note: 'Lighter from the start. Easier on an older computer or a battery.' },
          { value: 'full', title: 'Full detail', note: 'Every star, even if the motion stutters.' },
        ],
        (d) => detail.set(d),
      )
    : null;
  if (perf) finish(el('h3', 'connected-heading', 'Theme'), theme.element, el('h3', 'connected-heading', 'Performance'), perf.element);
  else finish(theme.element);
  return {
    open() {
      theme.set(current());
      if (perf && detail) perf.set(detail.get());
      host.showModal();
    },
  };
}

// ── Board ────────────────────────────────────────────────────────────────

/** The core's preference: live widgets refresh while the board is open. On unless turned off. */
export const WIDGETS_FRESH_PATH = '/api/settings/workspace';

/** Home's once-a-day opening, kept in this browser (prefs.ts). */
export interface HomeDaily {
  get(): boolean;
  set(on: boolean): void;
}

export function createBoardSettings(
  host: Host,
  api: Api,
  changed: () => void,
  home?: HomeDaily,
): { open(): Promise<void>; summary(): string } {
  const { finish } = section(host, 'board', 'Board', 'Widgets she builds from a connected service can keep their numbers up to date.');
  const row = el('label', 'settings-check');
  const box = el('input');
  box.type = 'checkbox';
  box.setAttribute('role', 'switch');
  box.id = 'board-fresh';
  const words = el('span');
  words.append(
    el('strong', '', 'Keep widgets fresh'),
    el('small', '', 'While the board is open, she reads the service again every few minutes. Each refresh uses a little of your model.'),
  );
  row.append(box, words);
  const status = el('p', 'settings-status');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  // Beside it, the browser's own switch: the morning screen, once a day.
  let daily: HTMLInputElement | null = null;
  const extra: HTMLElement[] = [];
  if (home) {
    const homeRow = el('label', 'settings-check');
    daily = el('input');
    daily.type = 'checkbox';
    daily.setAttribute('role', 'switch');
    daily.id = 'board-home-daily';
    const homeWords = el('span');
    homeWords.append(
      el('strong', '', 'Show Home when I open Trillion'),
      el('small', '', 'The first time you open her each day, the workspace opens to Home. Kept in this browser only.'),
    );
    homeRow.append(daily, homeWords);
    const box2 = daily;
    box2.addEventListener('change', () => home.set(box2.checked));
    extra.push(homeRow);
  }
  finish(row, status, ...extra);

  let on = true;
  const take = async (r: Response): Promise<void> => {
    const body = (await r.json()) as { widgets_fresh?: unknown };
    on = body.widgets_fresh !== false;
  };
  box.addEventListener('change', () => {
    const want = box.checked;
    box.disabled = true;
    status.textContent = '';
    void api('PUT', WIDGETS_FRESH_PATH, { widgets_fresh: want })
      .then(async (r) => {
        if (r.ok) await take(r);
        else status.textContent = 'That could not be saved. Try again.';
      })
      .catch(() => {
        status.textContent = 'That could not be saved. Try again.';
      })
      .finally(() => {
        box.checked = on;
        box.disabled = false;
        changed();
      });
  });
  const load = async (): Promise<void> => {
    try {
      const r = await api('GET', WIDGETS_FRESH_PATH);
      if (r.ok) await take(r);
    } catch {
      // Unread: the switch shows the default.
    }
  };
  // Read now, so the section's summary is right before it is first opened.
  void load().then(changed);
  return {
    async open() {
      status.textContent = '';
      await load();
      box.checked = on;
      if (daily && home) daily.checked = home.get();
      host.showModal();
    },
    summary: () => (on ? 'Kept fresh' : 'Snapshots'),
  };
}

// ── API usage ────────────────────────────────────────────────────────────

export function createUsageSettings(host: Host, load: () => Promise<Usage | null>, api: Api): { open(): Promise<void> } {
  const { finish } = section(
    host,
    'usage',
    'API usage',
    'You pay each service directly. Trillion warns you at the first amount and stops using that service at the second, until you raise it or the month ends.',
  );
  const list = el('ul', 'settings-list');
  const total = el('p', 'settings-hint');
  const byModel = el('details', 'spend-more');
  byModel.append(el('summary', '', 'Details by model'), list, total);
  function rows(u: Usage | null): void {
    if (!u) return;
    const found = breakdown(u);
    list.replaceChildren(
      ...(found.length
        ? found.map((r) => {
            const item = el('li', 'settings-path', r.name);
            item.append(el('br'), el('span', 'settings-hint', r.detail));
            return item;
          })
        : [el('li', 'settings-empty', 'Nothing yet this month.')]),
    );
    total.textContent = sentence(u);
  }
  const blocks = createProviderBlocks(api, load, rows);
  finish(blocks.element, byModel);
  return {
    async open() {
      await blocks.open();
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
