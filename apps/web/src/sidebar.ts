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

/** The width after a key on the edge (arrows nudge, Home and End go to the ends), or null for any other key. */
export function nudged(px: number, key: string): number | null {
  if (key === 'ArrowRight') return clampWidth(px + NUDGE);
  if (key === 'ArrowLeft') return clampWidth(px - NUDGE);
  if (key === 'Home') return SIDEBAR.min;
  if (key === 'End') return SIDEBAR.max;
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

/** Any of these ends a drag of the edge. */
const ENDS = ['pointerup', 'pointercancel', 'lostpointercapture'] as const;

export interface Sidebar {
  /** Its width in CSS px; 0 while hidden or on a phone. */
  width(): number;
  readonly hidden: boolean;
  setHidden(hidden: boolean): void;
  toggle(): void;
  /** Called when it is shown, hidden or resized; `animate` is false while the edge is moved. */
  onChange(listener: (animate: boolean) => void): void;
}

export function createSidebar(
  aside: HTMLElement,
  handle: HTMLElement,
  show: HTMLButtonElement,
  hide: HTMLButtonElement,
): Sidebar {
  const listeners: ((animate: boolean) => void)[] = [];
  let px = savedWidth(read(WIDTH_KEY));
  let hidden = read(HIDDEN_KEY) === '1';
  // The orb glides when the sidebar slides; it follows the edge exactly while dragged.
  const changed = (animate: boolean): void => listeners.forEach((l) => l(animate));

  function apply(): void {
    document.documentElement.style.setProperty('--sidebar-w', `${px}px`);
    aside.toggleAttribute('data-hidden', hidden);
    aside.inert = hidden;
    document.body.dataset['sidebar'] = hidden ? 'hidden' : 'shown';
    show.hidden = !hidden;
    handle.setAttribute('aria-valuenow', String(px));
    handle.setAttribute('aria-valuetext', `${px} pixels`);
  }

  function setHidden(next: boolean): void {
    if (next === hidden) return;
    hidden = next;
    write(HIDDEN_KEY, hidden ? '1' : null);
    apply();
    changed(true);
    (hidden ? show : hide).focus();
  }

  let dragging: number | null = null;
  handle.addEventListener('pointerdown', (ev) => {
    // The main button only, and one pointer at a time.
    if (ev.button !== 0 || dragging !== null) return;
    ev.preventDefault();
    dragging = ev.pointerId;
    handle.setPointerCapture(ev.pointerId);
    const move = (e: PointerEvent): void => {
      if (e.pointerId !== dragging) return;
      const next = clampWidth(e.clientX);
      if (next === px) return;
      px = next;
      apply();
      changed(false);
    };
    const end = (e: PointerEvent): void => {
      if (e.pointerId !== dragging) return;
      dragging = null;
      handle.removeEventListener('pointermove', move);
      for (const type of ENDS) handle.removeEventListener(type, end);
      write(WIDTH_KEY, String(px));
    };
    handle.addEventListener('pointermove', move);
    for (const type of ENDS) handle.addEventListener(type, end);
  });
  handle.addEventListener('keydown', (ev) => {
    const next = nudged(px, ev.key);
    if (next === null) return;
    ev.preventDefault();
    px = next;
    write(WIDTH_KEY, String(px));
    apply();
    changed(false);
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
