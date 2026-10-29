import { describe, expect, it } from 'vitest';
import {
  type Hotkey,
  type Key,
  SPACE,
  TalkKeys,
  alone,
  fromKeydown,
  fromModifierRelease,
  keycaps,
  globalAccelerator,
  refused,
} from '../src/hotkey';

const key = (code: string, extra: Partial<Key> = {}): Key => ({
  code,
  ctrl: false,
  alt: false,
  shift: false,
  meta: false,
  repeat: false,
  inMessageBox: false,
  messageEmpty: true,
  inOtherControl: false,
  ...extra,
});
const combo = (code: string, mods: Partial<Hotkey>): Hotkey => ({ ...SPACE, code, ...mods });
const RIGHT_OPTION = fromModifierRelease('AltRight') as Hotkey;

describe('recording a shortcut', () => {
  it('takes any key with whatever modifiers are held', () => {
    expect(fromKeydown({ code: 'KeyK', ctrl: true, alt: false, shift: false, meta: true })).toEqual(
      combo('KeyK', { ctrl: true, meta: true }),
    );
    expect(fromKeydown({ code: 'ControlLeft', ctrl: true, alt: false, shift: false, meta: false })).toBeNull();
  });

  it('takes a modifier on its own, telling right from left', () => {
    expect(RIGHT_OPTION).toEqual(combo('AltRight', { alt: true }));
    expect(alone(RIGHT_OPTION)).toBe(true);
    expect(alone(combo('KeyK', { alt: true }))).toBe(false);
  });

  it('names the keys the way people say them', () => {
    expect(keycaps(SPACE, true)).toEqual(['Space']);
    expect(keycaps(combo('KeyK', { ctrl: true, alt: true }), true)).toEqual(['⌃', '⌥', 'K']);
    expect(keycaps(combo('KeyK', { ctrl: true }), false)).toEqual(['Ctrl', 'K']);
    expect(keycaps(RIGHT_OPTION, true)).toEqual(['Right Option ⌥']);
    expect(keycaps(fromModifierRelease('ControlLeft') as Hotkey, false)).toEqual(['Left Ctrl']);
    expect(keycaps(combo('F5', {}), true)).toEqual(['F5']);
  });

  it('refuses keys that already mean something, and says why', () => {
    expect(refused(combo('Escape', {}))).toMatch(/already does something/);
    expect(refused(combo('Enter', {}))).toMatch(/already does something/);
    expect(refused(combo('KeyW', { meta: true }))).toMatch(/browser keeps/);
    expect(refused(combo('Comma', { meta: true }))).toMatch(/opens Settings/);
    expect(refused(combo('KeyA', { shift: true }))).toMatch(/capital/);
    expect(refused(combo('Fn', {}))).toMatch(/never hears the Fn key/);
    expect(refused(SPACE)).toBeNull();
    expect(refused(RIGHT_OPTION)).toBeNull();
    expect(refused(combo('KeyK', { ctrl: true }))).toBeNull();
  });
});

describe('press to talk', () => {
  it('toggles on the shortcut, once per press', () => {
    const t = new TalkKeys({ hotkey: SPACE, mode: 'toggle' });
    expect(t.down(key('Space'))).toBe('toggle');
    expect(t.down(key('Space', { repeat: true }))).toBeNull();
    expect(t.up(key('Space'))).toBeNull();
  });

  it('never steals a typed space, and leaves other fields and Settings alone', () => {
    const t = new TalkKeys({ hotkey: SPACE, mode: 'toggle' });
    expect(t.down(key('Space', { inMessageBox: true, messageEmpty: false }))).toBeNull();
    expect(t.down(key('Space', { inMessageBox: true, messageEmpty: true }))).toBe('toggle');
    expect(t.down(key('Space', { inOtherControl: true }))).toBeNull();
  });

  it('lets a combination work even while typing, since it types nothing', () => {
    const t = new TalkKeys({ hotkey: combo('KeyK', { ctrl: true }), mode: 'toggle' });
    expect(t.down(key('KeyK', { ctrl: true, inMessageBox: true, messageEmpty: false }))).toBe('toggle');
    expect(t.down(key('KeyK'))).toBeNull(); // the key without its modifier is just a k
  });

  it('counts a lone modifier only when nothing else was pressed with it', () => {
    const t = new TalkKeys({ hotkey: RIGHT_OPTION, mode: 'toggle' });
    expect(t.down(key('AltRight', { alt: true }))).toBeNull(); // not yet: it may become a combination
    expect(t.up(key('AltRight'))).toBe('toggle');
    t.down(key('AltRight', { alt: true }));
    t.down(key('KeyE', { alt: true })); // Option+E types an accent
    expect(t.up(key('AltRight'))).toBeNull();
    expect(t.up(key('AltLeft'))).toBeNull(); // the other Option key is not this shortcut
  });

  it('does nothing at all when switched off', () => {
    const t = new TalkKeys({ hotkey: null, mode: 'toggle' });
    expect(t.down(key('Space'))).toBeNull();
    expect(t.up(key('Space'))).toBeNull();
  });
});

describe('hold to talk', () => {
  it('starts on the press and sends on the release', () => {
    const t = new TalkKeys({ hotkey: SPACE, mode: 'hold' });
    expect(t.down(key('Space'))).toBe('start');
    expect(t.down(key('Space', { repeat: true }))).toBeNull();
    expect(t.up(key('Space'))).toBe('stop');
    expect(t.up(key('Space'))).toBeNull(); // only once
  });

  it('with a lone modifier, starts at once and cancels if it turns out to be a combination', () => {
    const t = new TalkKeys({ hotkey: RIGHT_OPTION, mode: 'hold' });
    expect(t.down(key('AltRight', { alt: true }))).toBe('start');
    expect(t.down(key('AltRight', { alt: true, repeat: true }))).toBeNull(); // held modifiers repeat on Windows
    expect(t.down(key('KeyE', { alt: true }))).toBe('cancel');
    expect(t.up(key('AltRight'))).toBeNull();
    expect(t.down(key('AltRight', { alt: true }))).toBe('start');
    expect(t.up(key('AltRight'))).toBe('stop');
  });

  it('starts fresh when the preferences change', () => {
    const t = new TalkKeys({ hotkey: SPACE, mode: 'hold' });
    t.down(key('Space'));
    t.use({ hotkey: SPACE, mode: 'toggle' });
    expect(t.up(key('Space'))).toBeNull();
  });
});

describe('globalAccelerator (the desktop app, D6)', () => {
  const hk = (code: string, mods: Partial<Hotkey> = {}): Hotkey => ({ ...SPACE, code, ...mods });

  it('writes a combination the way the global-shortcut plugin reads it', () => {
    expect(globalAccelerator(hk('Space', { alt: true }))).toBe('Alt+Space');
    expect(globalAccelerator(hk('KeyK', { meta: true, shift: true }))).toBe('Super+Shift+KeyK');
    expect(globalAccelerator(hk('Digit5', { ctrl: true, alt: true }))).toBe('Control+Alt+Digit5');
    expect(globalAccelerator(hk('F5', { meta: true }))).toBe('Super+F5');
    expect(globalAccelerator(hk('ArrowUp', { ctrl: true }))).toBe('Control+ArrowUp');
    expect(globalAccelerator(hk('Period', { alt: true }))).toBe('Alt+Period');
  });

  it('refuses what is not a combination', () => {
    expect(globalAccelerator(SPACE)).toBeNull();
    expect(globalAccelerator(hk('KeyA'))).toBeNull();
    expect(globalAccelerator(hk('F5'))).toBeNull();
    expect(globalAccelerator(hk('AltRight', { alt: true }))).toBeNull();
    expect(globalAccelerator(hk('MetaRight', { meta: true }))).toBeNull();
    expect(globalAccelerator(hk('KeyK', { shift: true }))).toBeNull();
  });

  it('refuses a modifier as the key, Fn, and keys the plugin does not know', () => {
    expect(globalAccelerator(hk('ShiftLeft', { alt: true, shift: true }))).toBeNull();
    expect(globalAccelerator(hk('Fn', { alt: true }))).toBeNull();
    expect(globalAccelerator(hk('Unidentified', { alt: true }))).toBeNull();
    expect(globalAccelerator(hk('', { alt: true }))).toBeNull();
  });
});
