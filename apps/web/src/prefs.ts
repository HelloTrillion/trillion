/**
 * Preferences that belong to this browser, not to Trillion: how she is drawn and which
 * keys talk to her. Kept in localStorage; if that is blocked the defaults hold.
 */

import type { PerfMode } from '@hellotrillion/galaxy';
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

/** How much the galaxy draws: lighter when frames drop, always lighter, or always full (GAL-6). */
export type Detail = 'auto' | 'smooth' | 'full';

const DETAIL_KEY = 'trillion-detail';

/** The saved choice. Nothing saved, or a damaged value, means automatic. */
export function savedDetail(): Detail {
  const saved = get(DETAIL_KEY);
  return saved === 'smooth' || saved === 'full' ? saved : 'auto';
}

export const saveDetail = (detail: Detail): void => set(DETAIL_KEY, detail);

/** The galaxy's own name for each choice: its performance mode on, off or automatic. */
export function perfModeOf(detail: Detail): PerfMode {
  return detail === 'smooth' ? 'on' : detail === 'full' ? 'off' : 'auto';
}

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

const MIC_KEY = 'trillion-mic';

/** The microphone she listens with, by the browser's id for it; null for the default. */
export function savedMic(): string | null {
  const saved = get(MIC_KEY);
  return saved && saved.length <= 200 ? saved : null;
}

const MIC_NAME_KEY = 'trillion-mic-name';

/** The chosen microphone's name. The browser's id for a device can change between
 * launches (WebKit renews it), so the name finds it again. */
export function savedMicName(): string | null {
  const saved = get(MIC_NAME_KEY);
  return saved && saved.length <= 200 ? saved : null;
}

export function saveMic(device: string | null, name?: string | null): void {
  if (device) {
    set(MIC_KEY, device);
    // Kept when not given: the same microphone, found again under a new id.
    if (name?.trim()) set(MIC_NAME_KEY, name.trim());
    return;
  }
  try {
    localStorage.removeItem(MIC_KEY);
    localStorage.removeItem(MIC_NAME_KEY);
  } catch {
    // nothing was remembered
  }
}

const BARGE_KEY = 'trillion-barge';

/** Whether talking over her stops her (VOI-9). On unless turned off. */
export const savedBarge = (): boolean => get(BARGE_KEY) !== 'off';

export const saveBarge = (on: boolean): void => set(BARGE_KEY, on ? 'on' : 'off');

const HOME_KEY = 'trillion-home-daily';
const HOME_DATE_KEY = 'trillion-home-date';

/** Whether the first load of each day opens the workspace to Home (D2). On unless turned off. */
export const savedHomeDaily = (): boolean => get(HOME_KEY) !== 'off';

export const saveHomeDaily = (on: boolean): void => set(HOME_KEY, on ? 'on' : 'off');

/** The local date Home last opened by itself on, or null. */
export const lastHomeDate = (): string | null => get(HOME_DATE_KEY);

export const saveHomeDate = (date: string): void => set(HOME_DATE_KEY, date);
