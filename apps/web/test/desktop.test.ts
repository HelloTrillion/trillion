// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SPACE, contextOf } from '../src/hotkey';
import {
  inApp,
  markShell,
  onOrb,
  onTalk,
  openExternal,
  orbState,
  reportState,
  resetReports,
  routeLinks,
  setTalkShortcut,
  showMain,
  signInTab,
} from '../src/desktop';
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
  resetReports();
  vi.useRealTimers();
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

  it('passes on every talk event, focused or not: the shell is the only source of that shortcut', async () => {
    const t = stubTauri();
    const seen: string[] = [];
    const off = onTalk((s) => seen.push(s));
    await flush();
    expect(t.listen).toHaveBeenCalledWith('talk', expect.any(Function));
    t.emit('talk', { state: 'down' });
    t.emit('talk', { state: 'up' });
    t.emit('talk', { state: 'sideways' });
    expect(seen).toEqual(['down', 'up']);
    off();
    expect(t.unlisten).toHaveBeenCalled();
  });

  it('sends a state change at once, and gathers caption changes for a moment', () => {
    vi.useFakeTimers();
    const t = stubTauri();
    const sent = () => t.invoke.mock.calls.filter(([c]) => c === 'report_state').map(([, a]) => a);
    reportState('listening', '');
    reportState('listening', '');
    expect(sent()).toEqual([{ state: 'listening', caption: '' }]);
    reportState('listening', 'Hel');
    reportState('listening', 'Hello');
    expect(sent()).toHaveLength(1);
    vi.advanceTimersByTime(120);
    expect(sent()).toEqual([
      { state: 'listening', caption: '' },
      { state: 'listening', caption: 'Hello' },
    ]);
    reportState('speaking', 'Hello');
    expect(sent()).toHaveLength(3);
    // A caption that goes back to the one sent before the timer fires sends nothing.
    reportState('speaking', 'Hello there');
    reportState('speaking', 'Hello');
    vi.advanceTimersByTime(500);
    expect(sent()).toHaveLength(3);
    // A state change takes the latest caption with it and drops the waiting one.
    reportState('speaking', 'Bye');
    reportState('idle', 'Bye now');
    vi.advanceTimersByTime(500);
    expect(sent().slice(3)).toEqual([{ state: 'idle', caption: 'Bye now' }]);
  });

  it('sends only the end of a long caption', () => {
    const t = stubTauri();
    reportState('speaking', 'word '.repeat(200));
    const caption = (t.invoke.mock.calls[0]?.[1] as { caption: string }).caption;
    expect(caption.length).toBeLessThanOrEqual(120);
    expect(caption.startsWith('…')).toBe(true);
  });

  it('opens links meant for a new tab in the browser', () => {
    const t = stubTauri();
    const stop = routeLinks();
    const a = document.createElement('a');
    a.href = 'https://example.com/docs';
    a.target = '_blank';
    const inner = document.createElement('span');
    a.append(inner);
    document.body.append(a);
    const ev = new MouseEvent('click', { bubbles: true, cancelable: true });
    inner.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(true);
    expect(t.invoke).toHaveBeenCalledWith('open_external', { url: 'https://example.com/docs' });

    const same = document.createElement('a');
    same.href = 'https://example.com/here';
    document.body.append(same);
    const ev2 = new MouseEvent('click', { bubbles: true, cancelable: true });
    same.dispatchEvent(ev2);
    expect(ev2.defaultPrevented).toBe(false);

    const mail = document.createElement('a');
    mail.href = 'mailto:someone@example.com';
    mail.target = '_blank';
    document.body.append(mail);
    const ev3 = new MouseEvent('click', { bubbles: true, cancelable: true });
    mail.dispatchEvent(ev3);
    expect(ev3.defaultPrevented).toBe(false);
    stop();
    a.remove();
    same.remove();
    mail.remove();
  });

  it('leaves links alone outside the app', () => {
    const stop = routeLinks();
    const a = document.createElement('a');
    a.href = 'https://example.com/docs';
    a.target = '_blank';
    document.body.append(a);
    const ev = new MouseEvent('click', { bubbles: true, cancelable: true });
    a.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(false);
    stop();
    a.remove();
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
    expect(reachLine(true, opt, false)).toBe("That combination couldn't be used from other apps; try another, such as ⌥Space.");
  });
  it('says nothing until the shell has answered', () => {
    expect(reachLine(true, opt, null)).toBe('');
  });
});

describe("contextOf: what has focus, for a talk key or the shell's talk event", () => {
  const page = () => {
    document.body.innerHTML =
      '<aside id="sidebar"><textarea id="input"></textarea><button id="b">x</button></aside><dialog id="settings"><input id="field"></dialog>';
    return document.getElementById('input') as HTMLTextAreaElement;
  };
  it('the message box, and whether it is empty', () => {
    const input = page();
    expect(contextOf(input, input, false)).toEqual({ inMessageBox: true, messageEmpty: true, inOtherControl: false });
    input.value = 'hi';
    expect(contextOf(input, input, false)).toEqual({ inMessageBox: true, messageEmpty: false, inOtherControl: false });
  });
  it('another field or a button takes keys itself', () => {
    const input = page();
    expect(contextOf(document.getElementById('b'), input, false).inOtherControl).toBe(true);
    expect(contextOf(document.body, input, false).inOtherControl).toBe(false);
  });
  it('with Settings open only the sidebar talks', () => {
    const input = page();
    expect(contextOf(document.getElementById('field'), input, true).inOtherControl).toBe(true);
    expect(contextOf(null, input, true).inOtherControl).toBe(true);
    expect(contextOf(input, input, true).inOtherControl).toBe(false);
  });
});
