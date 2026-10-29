/**
 * The bridge to the desktop app. The app loads this same page from the server and
 * gives it Tauri's global (`withGlobalTauri`), so the web app needs no Tauri package.
 * In a browser tab there is no global: every call is a no-op and `DESKTOP` is false.
 *
 * The contract with the shell (apps/desktop/src-tauri). Commands, by `core.invoke`:
 *
 *   open_external      {url: string}                                   -> void
 *                      Opens an http(s) URL in the default browser (sign-ins, D7).
 *   set_talk_shortcut  {accelerator: string | null, mode: 'toggle' | 'hold'}
 *                                                                      -> {global: boolean}
 *                      Registers the talk shortcut to work from any app, replacing the
 *                      last one; null unregisters. The accelerator is in the
 *                      global-shortcut plugin's syntax, modifiers first in the order
 *                      Super, Control, Alt, Shift, then the key's `KeyboardEvent.code`:
 *                      "Alt+Space", "Super+Shift+KeyK", "Control+Alt+F5". It always
 *                      holds Super, Control or Alt (D6; see `globalAccelerator`).
 *                      `global` is false when the shell could not register it.
 *   report_state       {state: 'idle' | 'listening' | 'thinking' | 'speaking', caption: string}
 *                                                                      -> void
 *                      Sent by the main page whenever either changes (never twice the
 *                      same). The shell sets the tray icon and forwards it to the orb
 *                      window as an `orb` event, showing or hiding that window (D5).
 *   show_main          (no arguments)                                  -> void
 *                      Sent by the orb window when clicked: show and focus the main one.
 *
 * Events, by `event.listen`:
 *
 *   talk  {state: 'down' | 'up'}   To the main page: the global shortcut was pressed or
 *                                  let go. Used only while the page lacks focus; with
 *                                  focus the page heard the key itself.
 *   orb   {state, caption}         To the orb window: the payload of report_state.
 */

import { type Hotkey, type Mode, globalAccelerator } from './hotkey';

export type ShellState = 'idle' | 'listening' | 'thinking' | 'speaking';
export type TalkState = 'down' | 'up';
export interface OrbReport {
  readonly state: ShellState;
  readonly caption: string;
}

interface Tauri {
  core: { invoke(cmd: string, args?: Record<string, unknown>): Promise<unknown> };
  event: { listen(event: string, handler: (e: { payload: unknown }) => void): Promise<() => void> };
}

function tauri(): Tauri | null {
  const t = (globalThis as { __TAURI__?: Partial<Tauri> }).__TAURI__;
  return t?.core && t.event ? (t as Tauri) : null;
}

/** Read on every call, so a test can put the global in place and take it away. */
export const inApp = (): boolean => tauri() !== null;
export const DESKTOP: boolean = inApp();

/** `<html data-shell="desktop">`, for styles that differ in the app. */
export function markShell(root: HTMLElement = document.documentElement): void {
  if (inApp()) root.dataset['shell'] = 'desktop';
}

function call(cmd: string, args?: Record<string, unknown>): Promise<unknown> {
  const t = tauri();
  return t ? t.core.invoke(cmd, args) : Promise.resolve(undefined);
}

/** Subscribe to a shell event; the returned function unsubscribes, even before it is set up. */
function subscribe(event: string, handler: (payload: unknown) => void): () => void {
  const t = tauri();
  if (!t) return () => {};
  let stopped = false;
  let stop: (() => void) | null = null;
  void t.event
    .listen(event, (e) => {
      if (!stopped) handler(e.payload);
    })
    .then((un) => {
      if (stopped) un();
      else stop = un;
    })
    .catch(() => {});
  return () => {
    stopped = true;
    stop?.();
  };
}

export async function openExternal(url: string): Promise<void> {
  try {
    await call('open_external', { url });
  } catch {
    // The shell refused it (not http or https); nothing to open.
  }
}

/** Ask the shell to make the talk shortcut work from any app. */
export async function setTalkShortcut(hotkey: Hotkey | null, mode: Mode): Promise<{ global: boolean }> {
  if (!inApp()) return { global: false };
  const accelerator = hotkey ? globalAccelerator(hotkey) : null;
  try {
    const r = (await call('set_talk_shortcut', { accelerator, mode })) as { global?: unknown } | undefined;
    return { global: r?.global === true };
  } catch {
    return { global: false };
  }
}

/** The global shortcut's presses, only while this page does not have focus. */
export function onTalk(handler: (state: TalkState) => void, hasFocus: () => boolean = () => document.hasFocus()): () => void {
  return subscribe('talk', (p) => {
    const state = (p as { state?: unknown } | null)?.state;
    if ((state === 'down' || state === 'up') && !hasFocus()) handler(state);
  });
}

let lastReport = '';
/** Tell the shell what the orb is doing and what she is saying; a repeat is not sent. */
export function reportState(state: ShellState, caption: string): void {
  if (!inApp()) return;
  const key = JSON.stringify([state, caption]);
  if (key === lastReport) return;
  lastReport = key;
  call('report_state', { state, caption }).catch(() => {});
}

/** The galaxy's orb states, as the shell names them. */
export function orbState(s: 'idle' | 'arming' | 'listening' | 'processing' | 'speaking' | 'error'): ShellState {
  if (s === 'arming' || s === 'listening') return 'listening';
  if (s === 'processing') return 'thinking';
  if (s === 'speaking') return 'speaking';
  return 'idle';
}

const SHELL_STATES: readonly string[] = ['idle', 'listening', 'thinking', 'speaking'];

/** For the orb window: each report the main page sends. */
export function onOrb(handler: (r: OrbReport) => void): () => void {
  return subscribe('orb', (p) => {
    const r = p as { state?: unknown; caption?: unknown } | null;
    if (typeof r?.state === 'string' && SHELL_STATES.includes(r.state) && typeof r.caption === 'string') {
      handler({ state: r.state as ShellState, caption: r.caption });
    }
  });
}

/** For the orb window: bring the main window forward. */
export function showMain(): void {
  call('show_main').catch(() => {});
}

export interface SignInTab {
  /** Open the sign-in page. False if a pop-up blocker stopped the tab, so the caller can fall back. */
  go(url: string): boolean;
  /** Nothing to sign in to after all. */
  close(): void;
}

/**
 * Where a sign-in page opens. In a tab: a blank tab opened now, from the click, so a
 * pop-up blocker lets it through, pointed at the page once the server says where it is.
 * In the app: nothing until then, and then the user's own browser, which holds their
 * sessions and passwords (D7).
 */
export function signInTab(title?: string): SignInTab {
  if (inApp()) {
    return {
      go: (url) => {
        void openExternal(url);
        return true;
      },
      close: () => {},
    };
  }
  const tab = window.open('', '_blank');
  if (tab) {
    tab.opener = null; // the sign-in page cannot reach this one
    if (title) tab.document.title = title;
  }
  return {
    go: (url) => {
      if (!tab) return false;
      tab.location.href = url;
      return true;
    },
    close: () => tab?.close(),
  };
}
