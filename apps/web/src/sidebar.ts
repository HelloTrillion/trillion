/**
 * The sidebar (SUR-1): the conversation beside the orb. It can be dragged wider and
 * hidden; both are remembered per device.
 */
import { SIDEBAR, clampWidth, isPhone } from './layout';

const WIDTH_KEY = 'trillion-sidebar-width';
const HIDDEN_KEY = 'trillion-sidebar-hidden';
/** How far one arrow key moves the edge, in CSS px. */
export const NUDGE = 16;

/** The width to start at, from what was stored (or nothing, or junk). */
export const savedWidth = (raw: string | null): number => clampWidth(Number(raw) || SIDEBAR.initial);

/** The width after an arrow key on the edge, or null for any other key. */
export function nudged(px: number, key: string): number | null {
  if (key === 'ArrowRight') return clampWidth(px + NUDGE);
  if (key === 'ArrowLeft') return clampWidth(px - NUDGE);
  return null;
}

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // Blocked storage: the page simply forgets.
  }
}

export interface Sidebar {
  /** Its width in CSS px; 0 while hidden or on a phone. */
  width(): number;
  readonly hidden: boolean;
  setHidden(hidden: boolean): void;
  toggle(): void;
  onChange(listener: () => void): void;
}

export function createSidebar(
  aside: HTMLElement,
  handle: HTMLElement,
  show: HTMLButtonElement,
  hide: HTMLButtonElement,
): Sidebar {
  const listeners: (() => void)[] = [];
  let px = savedWidth(read(WIDTH_KEY));
  let hidden = read(HIDDEN_KEY) === '1';
  const changed = (): void => listeners.forEach((l) => l());

  function apply(): void {
    document.documentElement.style.setProperty('--sidebar-w', `${px}px`);
    aside.toggleAttribute('data-hidden', hidden);
    aside.inert = hidden;
    document.body.dataset['sidebar'] = hidden ? 'hidden' : 'shown';
    show.hidden = !hidden;
    handle.setAttribute('aria-valuenow', String(px));
  }

  function setHidden(next: boolean): void {
    if (next === hidden) return;
    hidden = next;
    write(HIDDEN_KEY, hidden ? '1' : null);
    apply();
    changed();
    (hidden ? show : hide).focus();
  }

  handle.addEventListener('pointerdown', (ev) => {
    ev.preventDefault();
    handle.setPointerCapture(ev.pointerId);
    const move = (e: PointerEvent): void => {
      const next = clampWidth(e.clientX);
      if (next === px) return;
      px = next;
      apply();
      changed();
    };
    const up = (): void => {
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', up);
      handle.removeEventListener('pointercancel', up);
      write(WIDTH_KEY, String(px));
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', up);
    handle.addEventListener('pointercancel', up);
  });
  handle.addEventListener('keydown', (ev) => {
    const next = nudged(px, ev.key);
    if (next === null) return;
    ev.preventDefault();
    px = next;
    write(WIDTH_KEY, String(px));
    apply();
    changed();
  });
  show.addEventListener('click', () => setHidden(false));
  hide.addEventListener('click', () => setHidden(true));
  handle.setAttribute('aria-valuemin', String(SIDEBAR.min));
  handle.setAttribute('aria-valuemax', String(SIDEBAR.max));
  apply();

  return {
    width: () => (hidden || isPhone(innerWidth) ? 0 : px),
    get hidden() {
      return hidden;
    },
    setHidden,
    toggle: () => setHidden(!hidden),
    onChange: (l) => listeners.push(l),
  };
}
