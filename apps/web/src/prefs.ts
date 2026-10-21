/**
 * Preferences that belong to this browser, not to Trillion: how she is drawn and which
 * keys talk to her. Kept in localStorage; if that is blocked the defaults hold.
 */

import { DEFAULT_TALK, type Hotkey, type Mode, type TalkPrefs } from './hotkey';

/** What the user chose: a theme, or whatever the computer is set to. */
export type Theme = 'system' | 'dark' | 'light';

const THEME_KEY = 'trillion-theme';
const TALK_KEY = 'trillion-talk';
// The first version stored only on or off.
const OLD_TALK_KEY = 'trillion-talk-key';

function get(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function set(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // not remembered, still applied for this page
  }
}

/** The saved choice. Nothing saved means following the computer, as a native app does. */
export function savedTheme(): Theme {
  const saved = get(THEME_KEY);
  return saved === 'light' || saved === 'dark' ? saved : 'system';
}

/** The theme to draw: the choice, or the computer's when the choice is to follow it. */
export function shownTheme(choice: Theme, prefersLight: boolean): 'dark' | 'light' {
  if (choice !== 'system') return choice;
  return prefersLight ? 'light' : 'dark';
}

export const saveTheme = (theme: Theme): void => set(THEME_KEY, theme);

function hotkeyOf(raw: unknown): Hotkey | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const r = raw as Record<string, unknown>;
  if (typeof r['code'] !== 'string' || !r['code'] || r['code'].length > 40) return null;
  return {
    code: r['code'],
    ctrl: r['ctrl'] === true,
    alt: r['alt'] === true,
    shift: r['shift'] === true,
    meta: r['meta'] === true,
  };
}

/** Space, press to talk, unless the user chose otherwise. A damaged value is ignored. */
export function savedTalk(): TalkPrefs {
  const raw = get(TALK_KEY);
  if (raw === null) return get(OLD_TALK_KEY) === 'off' ? { ...DEFAULT_TALK, hotkey: null } : DEFAULT_TALK;
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const mode: Mode = parsed['mode'] === 'hold' ? 'hold' : 'toggle';
    return { hotkey: parsed['hotkey'] === null ? null : (hotkeyOf(parsed['hotkey']) ?? DEFAULT_TALK.hotkey), mode };
  } catch {
    return DEFAULT_TALK;
  }
}

export const saveTalk = (prefs: TalkPrefs): void => set(TALK_KEY, JSON.stringify(prefs));
