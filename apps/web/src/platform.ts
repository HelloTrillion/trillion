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
  readonly userAgentData?: { readonly platform?: string };
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
