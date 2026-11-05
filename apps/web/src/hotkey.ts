/**
 * The talk shortcut: any key or combination the user records, used two ways.
 *
 *   toggle  press it to start listening, press it again to send
 *   hold    hold it while talking, let go to send
 *
 * A modifier on its own (Control, Option, Command, Shift, and the right-hand ones
 * apart from the left) counts only when nothing else is pressed with it, so Control+C
 * still copies. A plain key that types something (Space, a letter) counts only when
 * typing it would do nothing: the message box is empty, or nothing that takes typing
 * has focus.
 *
 * A web page only hears keys while its tab is in front, and never hears the Fn key:
 * macOS keeps that one to itself. In the desktop app a combination also works from any
 * app (`globalAccelerator`); Fn and a modifier on its own still do not.
 *
 * Pure: no DOM. The page turns each key event into `Key` and acts on the answer.
 */

export type Mode = 'toggle' | 'hold';

export interface Hotkey {
  /** The physical key, as `KeyboardEvent.code`: "Space", "KeyK", "AltRight", "F5". */
  readonly code: string;
  readonly ctrl: boolean;
  readonly alt: boolean;
  readonly shift: boolean;
  readonly meta: boolean;
}

export interface TalkPrefs {
  /** null: no shortcut; only the Speak button starts listening. */
  readonly hotkey: Hotkey | null;
  readonly mode: Mode;
}

export const SPACE: Hotkey = { code: 'Space', ctrl: false, alt: false, shift: false, meta: false };
export const DEFAULT_TALK: TalkPrefs = { hotkey: SPACE, mode: 'toggle' };

/** One key event, reduced to what the shortcut needs. */
export interface Key {
  readonly code: string;
  readonly ctrl: boolean;
  readonly alt: boolean;
  readonly shift: boolean;
  readonly meta: boolean;
  readonly repeat: boolean;
  /** Focus is in the message box, and what is typed there. */
  readonly inMessageBox: boolean;
  readonly messageEmpty: boolean;
  /** Focus is in some other field, a button, or Settings. */
  readonly inOtherControl: boolean;
}

export type Action = 'toggle' | 'start' | 'stop' | 'cancel';

const MODIFIERS: Readonly<Record<string, 'ctrl' | 'alt' | 'shift' | 'meta'>> = {
  ControlLeft: 'ctrl',
  ControlRight: 'ctrl',
  AltLeft: 'alt',
  AltRight: 'alt',
  ShiftLeft: 'shift',
  ShiftRight: 'shift',
  MetaLeft: 'meta',
  MetaRight: 'meta',
  OSLeft: 'meta',
  OSRight: 'meta',
};

export const isModifier = (code: string): boolean => code in MODIFIERS;

/** A modifier on its own: the recorded key is the modifier and nothing else is held. */
export function alone(h: Hotkey): boolean {
  const which = MODIFIERS[h.code];
  if (!which) return false;
  const others = (['ctrl', 'alt', 'shift', 'meta'] as const).filter((m) => m !== which);
  return others.every((m) => !h[m]);
}

/** A key that types a character when pressed with no modifier, or with Shift. */
function types(h: Hotkey): boolean {
  if (h.ctrl || h.alt || h.meta) return false;
  return h.code === 'Space' || /^(Key[A-Z]|Digit\d|Numpad\d|Minus|Equal|Bracket|Semicolon|Quote|Backquote|Backslash|Comma|Period|Slash|IntlBackslash)/.test(h.code);
}

/** Why a shortcut cannot be used, or null if it can. */
export function refused(h: Hotkey): string | null {
  const plain = !h.ctrl && !h.alt && !h.meta && !h.shift;
  if (h.code === 'Fn' || h.code === 'FnLock') {
    return 'A web page never hears the Fn key. Try Right Option or Right Command instead.';
  }
  if (plain && ['Escape', 'Enter', 'NumpadEnter', 'Tab', 'Backspace', 'Delete'].includes(h.code)) {
    return 'That key already does something here. Pick another.';
  }
  if (h.shift && !h.ctrl && !h.alt && !h.meta && types(h)) {
    return 'Shift with a letter types a capital. Add Control, Option or Command.';
  }
  const browser = h.ctrl || h.meta;
  if (browser && !h.alt && ['KeyW', 'KeyQ', 'KeyT', 'KeyN', 'KeyR', 'KeyL', 'KeyH', 'KeyM', 'Tab'].includes(h.code)) {
    return 'Your browser keeps that one for itself. Pick another.';
  }
  if (browser && h.code === 'Comma' && !h.alt && !h.shift) return 'That opens Settings. Pick another.';
  return null;
}

/** The recorded key from a keydown, or null while only modifiers are held. */
export function fromKeydown(k: Pick<Key, 'code' | 'ctrl' | 'alt' | 'shift' | 'meta'>): Hotkey | null {
  if (isModifier(k.code) || !k.code) return null;
  return { code: k.code, ctrl: k.ctrl, alt: k.alt, shift: k.shift, meta: k.meta };
}

/** A modifier let go with nothing else pressed: that modifier alone is the shortcut. */
export function fromModifierRelease(code: string): Hotkey | null {
  const which = MODIFIERS[code];
  if (!which) return null;
  return { ...SPACE, code, ctrl: which === 'ctrl', alt: which === 'alt', shift: which === 'shift', meta: which === 'meta' };
}

const MAC_SYMBOLS = { ctrl: '⌃', alt: '⌥', shift: '⇧', meta: '⌘' } as const;
const WORDS = { ctrl: 'Ctrl', alt: 'Alt', shift: 'Shift', meta: 'Win' } as const;
const NAMES: Readonly<Record<string, string>> = {
  Space: 'Space',
  Backquote: '`',
  Minus: '-',
  Equal: '=',
  BracketLeft: '[',
  BracketRight: ']',
  Backslash: '\\',
  Semicolon: ';',
  Quote: "'",
  Comma: ',',
  Period: '.',
  Slash: '/',
  ArrowUp: '↑',
  ArrowDown: '↓',
  ArrowLeft: '←',
  ArrowRight: '→',
  CapsLock: 'Caps Lock',
  ContextMenu: 'Menu',
};

/** The keys of a shortcut as the user would name them, one per keycap. */
export function keycaps(h: Hotkey, mac: boolean): string[] {
  const sym = mac ? MAC_SYMBOLS : WORDS;
  if (alone(h)) {
    const which = MODIFIERS[h.code] as keyof typeof sym;
    const side = h.code.endsWith('Right') ? 'Right ' : h.code.endsWith('Left') ? 'Left ' : '';
    const name = mac ? { ctrl: 'Control', alt: 'Option', shift: 'Shift', meta: 'Command' }[which] : WORDS[which];
    return [`${side}${name} ${mac ? sym[which] : ''}`.trim()];
  }
  const caps: string[] = (['ctrl', 'alt', 'shift', 'meta'] as const).filter((m) => h[m]).map((m) => sym[m]);
  const key =
    NAMES[h.code] ??
    (/^Key[A-Z]$/.test(h.code)
      ? h.code.slice(3)
      : /^Digit\d$/.test(h.code)
        ? h.code.slice(5)
        : /^Numpad/.test(h.code)
          ? `Num ${h.code.slice(6)}`
          : h.code);
  return [...caps, key];
}

// The keys the global-shortcut plugin (Tauri's `global-hotkey` crate) knows by their
// `KeyboardEvent.code` name. It matches names case-insensitively, so the code goes as is.
const GLOBAL_KEY =
  /^(Key[A-Z]|Digit\d|Numpad\d|F([1-9]|1\d|20)|Arrow(Up|Down|Left|Right)|Space|Enter|Tab|Escape|Backspace|Delete|Insert|Home|End|PageUp|PageDown|Backquote|Minus|Equal|BracketLeft|BracketRight|Backslash|Semicolon|Quote|Comma|Period|Slash)$/;

/**
 * The shortcut as the desktop app registers it to work from any app, in the plugin's
 * accelerator syntax, or null if it cannot be global (D6). The rule, which the shell
 * mirrors:
 *
 *   1. It holds Command (Super), Control or Option (Alt); Shift may be added.
 *   2. The key is one of GLOBAL_KEY: a letter, digit, keypad digit, F1–F20, an arrow,
 *      Space, Enter, Tab, Escape, Backspace, Delete, Insert, Home, End, PageUp,
 *      PageDown, or ` - = [ ] \ ; ' , . /. Never a modifier, never Fn.
 *   3. Not Command or Control alone (one of them, no Option, no Shift) with a letter,
 *      Space, Tab or Backquote: every app uses those (⌘C, ⌘V, ⌘Space, ⌘Tab, ⌘`).
 *   4. Not the page's own: Command or Control alone, with or without Shift, with K,
 *      \ or , (the message box, the conversations, the sidebar, Settings).
 *
 * A modifier on its own needs Accessibility permission, and a plain key or a Shift
 * combination would stop it being typed in every other app.
 *
 * Written as the modifiers in the order Super, Control, Alt, Shift, then the code, all
 * joined with "+": "Alt+Space", "Control+Alt+KeyT", "Super+F5". Super is Command on a Mac.
 */
export function globalAccelerator(h: Hotkey): string | null {
  if (!(h.meta || h.ctrl || h.alt) || isModifier(h.code) || !GLOBAL_KEY.test(h.code)) return null;
  const primaryOnly = h.meta !== h.ctrl && !h.alt;
  if (primaryOnly && !h.shift && /^(Key[A-Z]|Space|Tab|Backquote)$/.test(h.code)) return null;
  if (primaryOnly && ['KeyK', 'Backslash', 'Comma'].includes(h.code)) return null;
  const mods = (
    [
      ['meta', 'Super'],
      ['ctrl', 'Control'],
      ['alt', 'Alt'],
      ['shift', 'Shift'],
    ] as const
  )
    .filter(([m]) => h[m])
    .map(([, name]) => name);
  return [...mods, h.code].join('+');
}

/** The key event is the shortcut: the same key with the same modifiers. */
export const matches = (h: Hotkey, k: Pick<Key, 'code' | 'ctrl' | 'alt' | 'shift' | 'meta'>): boolean =>
  h.code === k.code && h.ctrl === k.ctrl && h.alt === k.alt && h.shift === k.shift && h.meta === k.meta;
const same = matches;

/** Where focus is, as the talk shortcut sees it. */
export type Context = Pick<Key, 'inMessageBox' | 'messageEmpty' | 'inOtherControl'>;

/** The least of the DOM the context needs, so this module stays free of it. */
interface Focusable {
  closest(selector: string): unknown;
}

/**
 * What has focus: the message box, or another field, button, the thread list or
 * Settings, which take keys themselves. With Settings open the message box in the
 * sidebar still talks.
 */
/** Enter pressed in a field other than the message box: that field's own, not the page's. */
export function enterInField(target: EventTarget | null, input: unknown): boolean {
  if (typeof HTMLElement === 'undefined' || !(target instanceof HTMLElement) || target === input) return false;
  return target.closest('input, textarea, select, [contenteditable]') !== null;
}

export function contextOf(target: Focusable | null, input: Focusable & { readonly value: string }, settingsOpen: boolean): Context {
  return {
    inMessageBox: target === input,
    messageEmpty: input.value.trim() === '',
    inOtherControl:
      (settingsOpen && (target === null || target.closest('#sidebar') === null)) ||
      (target !== null &&
        target !== input &&
        target.closest('input, textarea, select, button, [contenteditable], [role="listbox"]') !== null),
  };
}

/**
 * Turns key events into talk actions. One per page: it remembers whether a lone
 * modifier is down, and whether this shortcut started the listening.
 */
export class TalkKeys {
  private armed = false; // a lone-modifier shortcut is down, with nothing else so far
  private holding = false; // hold mode: listening was started by this key

  constructor(private prefs: TalkPrefs) {}

  use(prefs: TalkPrefs): void {
    this.prefs = prefs;
    this.armed = this.holding = false;
  }

  down(k: Key): Action | null {
    const h = this.prefs.hotkey;
    if (!h || k.inOtherControl) return this.interrupted();
    if (alone(h)) {
      if (k.code === h.code && k.repeat) return null; // a held modifier repeats on some systems
      if (k.code === h.code) {
        this.armed = true;
        if (this.prefs.mode === 'hold') {
          this.holding = true;
          return 'start';
        }
        return null;
      }
      // Anything else pressed with it: it was Control+C, not the shortcut.
      return this.interrupted();
    }
    if (!same(h, k) || k.repeat) return null;
    if (types(h) && k.inMessageBox && !k.messageEmpty) return null;
    if (this.prefs.mode === 'hold') {
      this.holding = true;
      return 'start';
    }
    return 'toggle';
  }

  up(k: Pick<Key, 'code'>): Action | null {
    const h = this.prefs.hotkey;
    if (!h) return null;
    if (alone(h)) {
      if (k.code !== h.code) return null;
      const was = this.armed;
      this.armed = false;
      if (this.prefs.mode === 'hold') {
        if (!this.holding) return null;
        this.holding = false;
        return 'stop';
      }
      return was ? 'toggle' : null;
    }
    if (this.holding && k.code === h.code) {
      this.holding = false;
      return 'stop';
    }
    return null;
  }

  /** Something else was pressed while a lone modifier was down. */
  private interrupted(): Action | null {
    this.armed = false;
    if (this.holding) {
      this.holding = false;
      return 'cancel';
    }
    return null;
  }
}

/**
 * The desktop app's global shortcut, pressed or let go (its `talk` event), turned into the
 * same actions as a key here. A second press before a release is a repeat.
 */
export class ShellKeys {
  private down = false;

  constructor(private readonly keys: TalkKeys) {}

  event(h: Hotkey | null, state: 'down' | 'up', ctx: Context): Action | null {
    if (!h) return null;
    if (state === 'up') {
      this.down = false;
      return this.keys.up({ code: h.code });
    }
    const repeat = this.down;
    this.down = true;
    return this.keys.down({ ...h, repeat, ...ctx });
  }
}
