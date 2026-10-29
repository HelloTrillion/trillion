// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SPACE } from '../src/hotkey';
import { inApp, markShell, onOrb, onTalk, openExternal, orbState, reportState, setTalkShortcut, showMain, signInTab } from '../src/desktop';
import { reachLine } from '../src/browser';

type Handler = (e: { payload: unknown }) => void;

/** A stand-in for Tauri's global: records every invoke, keeps every listener. */
function stubTauri(answers: Record<string, unknown> = {}) {
  const invoke = vi.fn(async (cmd: string, _args?: Record<string, unknown>) => answers[cmd]);
  const listeners = new Map<string, Handler>();
  const unlisten = vi.fn();
  const listen = vi.fn(async (event: string, handler: Handler) => {
    listeners.set(event, handler);
    return unlisten;
  });
  (window as unknown as { __TAURI__: unknown }).__TAURI__ = { core: { invoke }, event: { listen } };
  const emit = (event: string, payload: unknown): void => listeners.get(event)?.({ payload });
  return { invoke, listen, emit, unlisten };
}

const flush = (): Promise<void> => new Promise((r) => setTimeout(r, 0));

afterEach(() => {
  delete (window as unknown as { __TAURI__?: unknown }).__TAURI__;
  delete document.documentElement.dataset['shell'];
  vi.restoreAllMocks();
});

describe('outside the app', () => {
  it('is not the app, and every call is a no-op', async () => {
    expect(inApp()).toBe(false);
    const { DESKTOP } = await import('../src/desktop');
    expect(DESKTOP).toBe(false);
    markShell();
    expect(document.documentElement.dataset['shell']).toBeUndefined();
    await expect(openExternal('https://example.com')).resolves.toBeUndefined();
    await expect(setTalkShortcut({ ...SPACE, alt: true }, 'toggle')).resolves.toEqual({ global: false });
    const off = onTalk(() => {
      throw new Error('never');
    });
    off();
    reportState('listening', 'hi');
    onOrb(() => {
      throw new Error('never');
    })();
    showMain();
  });
});

describe('in the app', () => {
  it('marks the page', () => {
    stubTauri();
    markShell();
    expect(document.documentElement.dataset['shell']).toBe('desktop');
  });

  it('opens a link in the browser', async () => {
    const t = stubTauri();
    await openExternal('https://accounts.example.com/o');
    expect(t.invoke).toHaveBeenCalledWith('open_external', { url: 'https://accounts.example.com/o' });
  });

  it('registers the talk shortcut and returns whether it works from any app', async () => {
    const t = stubTauri({ set_talk_shortcut: { global: true } });
    await expect(setTalkShortcut({ ...SPACE, alt: true }, 'hold')).resolves.toEqual({ global: true });
    expect(t.invoke).toHaveBeenCalledWith('set_talk_shortcut', { accelerator: 'Alt+Space', mode: 'hold' });
  });

  it('clears the global shortcut for one that is not a combination, or none', async () => {
    const t = stubTauri({ set_talk_shortcut: { global: false } });
    await expect(setTalkShortcut(SPACE, 'toggle')).resolves.toEqual({ global: false });
    expect(t.invoke).toHaveBeenLastCalledWith('set_talk_shortcut', { accelerator: null, mode: 'toggle' });
    await setTalkShortcut(null, 'toggle');
    expect(t.invoke).toHaveBeenLastCalledWith('set_talk_shortcut', { accelerator: null, mode: 'toggle' });
  });

  it('reads a failed registration as not global', async () => {
    stubTauri();
    (window as unknown as { __TAURI__: { core: { invoke: unknown } } }).__TAURI__.core.invoke = vi.fn(async () => {
      throw new Error('taken');
    });
    await expect(setTalkShortcut({ ...SPACE, alt: true }, 'toggle')).resolves.toEqual({ global: false });
  });

  it('feeds the talk event on only while the page does not have focus', async () => {
    const t = stubTauri();
    const seen: string[] = [];
    let focused = true;
    const off = onTalk((s) => seen.push(s), () => focused);
    await flush();
    expect(t.listen).toHaveBeenCalledWith('talk', expect.any(Function));
    t.emit('talk', { state: 'down' });
    t.emit('talk', { state: 'up' });
    expect(seen).toEqual([]);
    focused = false;
    t.emit('talk', { state: 'down' });
    t.emit('talk', { state: 'up' });
    t.emit('talk', { state: 'sideways' });
    expect(seen).toEqual(['down', 'up']);
    off();
    expect(t.unlisten).toHaveBeenCalled();
  });

  it('sends the orb state and caption, once per change', async () => {
    const t = stubTauri();
    reportState('listening', '');
    reportState('listening', '');
    reportState('listening', 'Hello');
    reportState('speaking', 'Hello');
    expect(t.invoke.mock.calls.filter(([c]) => c === 'report_state').map(([, a]) => a)).toEqual([
      { state: 'listening', caption: '' },
      { state: 'listening', caption: 'Hello' },
      { state: 'speaking', caption: 'Hello' },
    ]);
  });

  it('names the galaxy states the way the shell does', () => {
    expect(orbState('idle')).toBe('idle');
    expect(orbState('arming')).toBe('listening');
    expect(orbState('listening')).toBe('listening');
    expect(orbState('processing')).toBe('thinking');
    expect(orbState('speaking')).toBe('speaking');
    expect(orbState('error')).toBe('idle');
  });

  it('asks for the main window', () => {
    const t = stubTauri();
    showMain();
    expect(t.invoke).toHaveBeenCalledWith('show_main', undefined);
  });
});

describe('signInTab', () => {
  it('in a tab: opens a blank tab from the click, then points it at the sign-in page', () => {
    const tab = { opener: {} as unknown, document: { title: '' }, location: { href: '' }, close: vi.fn() };
    const open = vi.spyOn(window, 'open').mockReturnValue(tab as unknown as Window);
    const s = signInTab('Signing in…');
    expect(open).toHaveBeenCalledWith('', '_blank');
    expect(tab.opener).toBeNull();
    expect(tab.document.title).toBe('Signing in…');
    expect(s.go('https://accounts.example.com/o')).toBe(true);
    expect(tab.location.href).toBe('https://accounts.example.com/o');
    s.close();
    expect(tab.close).toHaveBeenCalled();
  });

  it('in a tab: says so when the tab was blocked', () => {
    vi.spyOn(window, 'open').mockReturnValue(null);
    const s = signInTab();
    expect(s.go('https://accounts.example.com/o')).toBe(false);
    s.close();
  });

  it('in the app: opens nothing until the address is known, then the browser', async () => {
    const t = stubTauri();
    const open = vi.spyOn(window, 'open');
    const s = signInTab('Signing in…');
    expect(open).not.toHaveBeenCalled();
    expect(t.invoke).not.toHaveBeenCalled();
    expect(s.go('https://accounts.example.com/o')).toBe(true);
    expect(t.invoke).toHaveBeenCalledWith('open_external', { url: 'https://accounts.example.com/o' });
    s.close();
    expect(open).not.toHaveBeenCalled();
  });

  it('in the app: closing before an address opens nothing', () => {
    const t = stubTauri();
    signInTab().close();
    expect(t.invoke).not.toHaveBeenCalled();
  });
});

describe('the orb listener', () => {
  it('passes on state and caption', async () => {
    const t = stubTauri();
    const seen: unknown[] = [];
    onOrb((o) => seen.push(o));
    await flush();
    t.emit('orb', { state: 'speaking', caption: 'Hi there' });
    t.emit('orb', { state: 'nonsense', caption: 3 });
    expect(seen).toEqual([{ state: 'speaking', caption: 'Hi there' }]);
  });
});

describe('the Settings line under the talk shortcut (D6)', () => {
  const opt = { ...SPACE, code: 'Space', alt: true };
  it('says nothing outside the app', () => {
    expect(reachLine(false, opt, true)).toBe('');
  });
  it('asks for a combination when the shortcut cannot be global', () => {
    const d6 = 'To talk from any app, record a combination such as ⌥Space.';
    expect(reachLine(true, SPACE, false)).toBe(d6);
    expect(reachLine(true, { ...SPACE, code: 'AltRight', alt: true }, false)).toBe(d6);
    expect(reachLine(true, null, false)).toBe(d6);
  });
  it('says it works from any app once the shell has it', () => {
    expect(reachLine(true, opt, true)).toBe('Works from any app, even while this window is hidden.');
  });
  it('says when the shell could not take a combination', () => {
    expect(reachLine(true, opt, false)).toMatch(/another app/i);
  });
  it('says nothing until the shell has answered', () => {
    expect(reachLine(true, opt, null)).toBe('');
  });
});
