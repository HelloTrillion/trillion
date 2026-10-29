/**
 * Which desktop the page is on, so it can behave like an app made for it: ⌘ on a Mac
 * and Ctrl elsewhere, the system's own font, its conventions for shortcuts.
 *
 * Set once on <html> as `data-platform`, where the styles can see it too. The
 * desktop shell will report the same names when it hosts the page.
 */

export type Platform = 'mac' | 'windows' | 'linux' | 'other';

interface Navigatorish {
  readonly platform?: string;
  readonly userAgent?: string;
  readonly userAgentData?: { readonly platform?: string; readonly brands?: readonly { readonly brand: string }[] };
}

export function detect(nav: Navigatorish): Platform {
  const said = `${nav.userAgentData?.platform ?? ''} ${nav.platform ?? ''} ${nav.userAgent ?? ''}`;
  // iPad reports itself as a Mac; a phone or tablet is not a desktop either way.
  if (/iPhone|iPad|Android/i.test(said)) return 'other';
  if (/Mac/i.test(said)) return 'mac';
  if (/Win/i.test(said)) return 'windows';
  if (/Linux|X11|CrOS/i.test(said)) return 'linux';
  return 'other';
}

/** Where the page runs, as its hello tells the server: the mic check then leaves this
 * app's own recording out, so the user talking to her is not "another app". */
export type PageHost = 'desktop' | 'chrome' | 'edge' | 'firefox' | 'safari' | 'other';

export function pageHost(nav: Navigatorish, desktop: boolean): PageHost {
  if (desktop) return 'desktop';
  const brands = (nav.userAgentData?.brands ?? []).map((b) => b.brand);
  // Edge is Chromium too, and says so, so it is asked about first.
  if (brands.some((b) => /Edge/i.test(b))) return 'edge';
  if (brands.some((b) => /Chrome|Chromium/i.test(b))) return 'chrome';
  const ua = nav.userAgent ?? '';
  if (/\bEdg(e|A|iOS)?\//.test(ua)) return 'edge';
  if (/\b(Chrome|Chromium|CriOS)\//.test(ua)) return 'chrome';
  if (/\b(Firefox|FxiOS)\//.test(ua)) return 'firefox';
  // Every Chromium browser says "Safari" as well; only now is it Safari itself.
  if (/\bSafari\//.test(ua)) return 'safari';
  return 'other';
}

export const PLATFORM: Platform = typeof navigator === 'undefined' ? 'other' : detect(navigator);
export const MAC = PLATFORM === 'mac';

/** The key app shortcuts are made with: Command on a Mac, Control everywhere else. */
export function primary(ev: Pick<KeyboardEvent, 'metaKey' | 'ctrlKey' | 'altKey'>, mac = MAC): boolean {
  if (ev.altKey) return false;
  return mac ? ev.metaKey && !ev.ctrlKey : ev.ctrlKey && !ev.metaKey;
}

/** A shortcut as this platform writes it: "⌘," on a Mac, "Ctrl+," elsewhere. */
export function shortcut(key: string, mac = MAC): string {
  return mac ? `⌘${key}` : `Ctrl+${key}`;
}

if (typeof document !== 'undefined') document.documentElement.dataset['platform'] = PLATFORM;
