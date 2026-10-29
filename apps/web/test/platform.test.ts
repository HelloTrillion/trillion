import { describe, expect, it } from 'vitest';
import { detect, pageHost, primary, shortcut } from '../src/platform';
import { shownTheme } from '../src/prefs';

describe('which desktop', () => {
  it('reads the platform the browser reports', () => {
    expect(detect({ userAgentData: { platform: 'macOS' } })).toBe('mac');
    expect(detect({ platform: 'MacIntel' })).toBe('mac');
    expect(detect({ platform: 'Win32' })).toBe('windows');
    expect(detect({ userAgentData: { platform: 'Windows' } })).toBe('windows');
    expect(detect({ platform: 'Linux x86_64' })).toBe('linux');
  });

  it('does not call a phone or an iPad a desktop', () => {
    expect(detect({ platform: 'MacIntel', userAgent: 'Mozilla/5.0 (iPad; CPU OS 18_0)' })).toBe('other');
    expect(detect({ platform: 'Linux armv8l', userAgent: 'Mozilla/5.0 (Linux; Android 15)' })).toBe('other');
    expect(detect({})).toBe('other');
  });
});

describe('shortcuts the way each platform makes them', () => {
  const key = (k: Partial<KeyboardEvent>) => ({ metaKey: false, ctrlKey: false, altKey: false, ...k });

  it('uses Command on a Mac, and only Command', () => {
    expect(primary(key({ metaKey: true }), true)).toBe(true);
    expect(primary(key({ ctrlKey: true }), true)).toBe(false); // Ctrl+, is not a Mac shortcut
  });

  it('uses Control elsewhere, and only Control', () => {
    expect(primary(key({ ctrlKey: true }), false)).toBe(true);
    expect(primary(key({ metaKey: true }), false)).toBe(false); // the Windows key is the system's
  });

  it('leaves combinations with Option or Alt alone', () => {
    expect(primary(key({ metaKey: true, altKey: true }), true)).toBe(false);
    expect(primary(key({ ctrlKey: true, altKey: true }), false)).toBe(false);
  });

  it('writes them as each platform does', () => {
    expect(shortcut(',', true)).toBe('⌘,');
    expect(shortcut(',', false)).toBe('Ctrl+,');
  });
});

describe('the theme', () => {
  it('follows the computer unless told otherwise', () => {
    expect(shownTheme('system', true)).toBe('light');
    expect(shownTheme('system', false)).toBe('dark');
    expect(shownTheme('dark', true)).toBe('dark');
    expect(shownTheme('light', false)).toBe('light');
  });
});

describe('where the page runs, for its hello', () => {
  const CHROME = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36';
  const EDGE = `${CHROME} Edg/141.0.0.0`;
  const FIREFOX = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:144.0) Gecko/20100101 Firefox/144.0';
  const SAFARI = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15';

  it('is the desktop app whatever the browser engine says', () => {
    expect(pageHost({ userAgent: SAFARI }, true)).toBe('desktop');
  });

  it('reads the brands first, Edge before Chrome', () => {
    const brands = (...names: string[]) => ({ userAgentData: { brands: names.map((brand) => ({ brand })) }, userAgent: CHROME });
    expect(pageHost(brands('Not=A?Brand', 'Chromium', 'Microsoft Edge'), false)).toBe('edge');
    expect(pageHost(brands('Google Chrome', 'Chromium', 'Not=A?Brand'), false)).toBe('chrome');
  });

  it('falls back to the user agent', () => {
    expect(pageHost({ userAgent: EDGE }, false)).toBe('edge');
    expect(pageHost({ userAgent: CHROME }, false)).toBe('chrome');
    expect(pageHost({ userAgent: FIREFOX }, false)).toBe('firefox');
    expect(pageHost({ userAgent: SAFARI }, false)).toBe('safari');
    expect(pageHost({ userAgent: 'curl/8.0' }, false)).toBe('other');
    expect(pageHost({}, false)).toBe('other');
  });
});
