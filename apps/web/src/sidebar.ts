/**
 * The sidebar (SUR-1): the conversation beside the orb. It can be dragged wider and
 * hidden; both are remembered per device. On a phone it is a bottom sheet instead, dragged
 * between three rests by the same handle and never hidden; its rest is remembered too.
 */
import {
  RESTS,
  SIDEBAR,
  TAP_SLOP,
  clampWidth,
  dragTop,
  isPhone,
  settle,
  sheetTop,
  stepRest,
  tapped,
  type Rest,
} from './layout';

const WIDTH_KEY = 'trillion-sidebar-width';
const HIDDEN_KEY = 'trillion-sidebar-hidden';
const REST_KEY = 'trillion-sheet-rest';
const REST_TEXT: Record<Rest, string> = { collapsed: 'Collapsed', half: 'Half height', full: 'Full height' };
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

/** The rest to start at: collapsed, orb first, unless another was stored. */
export const savedRest = (raw: string | null): Rest => RESTS.find((r) => r === raw) ?? 'collapsed';

/** `rest` raised to at least `to`; a sheet already higher stays where it is. */
export const higher = (rest: Rest, to: Rest): Rest => (RESTS.indexOf(rest) >= RESTS.indexOf(to) ? rest : to);

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
  /** Hidden on a desktop. A phone's sheet is never hidden, whatever the desktop chose. */
  readonly hidden: boolean;
  /** Hide or show it on a desktop; on a phone, nothing to do. */
  setHidden(hidden: boolean): void;
  toggle(): void;
  /** On a phone, raise the sheet to at least `rest`; on a desktop, nothing to do. */
  raise(rest: Rest): void;
  /** Called when it is shown, hidden or resized, or the window crosses into or out of phone width; `animate` is false while the edge is moved. */
  onChange(listener: (animate: boolean) => void): void;
}

export function createSidebar(
  aside: HTMLElement,
  handle: HTMLElement,
  show: HTMLButtonElement,
  hide: HTMLButtonElement,
  ask: HTMLElement,
): Sidebar {
  const listeners: ((animate: boolean) => void)[] = [];
  let px = savedWidth(read(WIDTH_KEY));
  let hidden = read(HIDDEN_KEY) === '1';
  let rest = savedRest(read(REST_KEY));
  let phone = isPhone(innerWidth);
  // The orb glides when the sidebar slides; it follows the edge exactly while dragged.
  const changed = (animate: boolean): void => listeners.forEach((l) => l(animate));
  const isHidden = (): boolean => hidden && !phone;

  /** The collapsed sheet's height: the ask bar and the sheet's own padding around it. */
  function askHeight(): number {
    const css = getComputedStyle(aside);
    return ask.offsetHeight + parseFloat(css.paddingTop) + parseFloat(css.paddingBottom);
  }

  function placeSheet(top: number): void {
    aside.style.setProperty('--sheet-top', `${top}px`);
  }

  function apply(): void {
    const root = document.documentElement.style;
    root.setProperty('--sidebar-w', `${px}px`);
    aside.toggleAttribute('data-hidden', isHidden());
    aside.inert = isHidden();
    document.body.dataset['sidebar'] = isHidden() ? 'hidden' : 'shown';
    show.hidden = !isHidden();
    if (phone) {
      aside.dataset['rest'] = rest;
      const a = askHeight();
      root.setProperty('--sheet-ask', `${a}px`);
      placeSheet(sheetTop(rest, innerHeight, a));
      handle.setAttribute('aria-orientation', 'horizontal');
      handle.setAttribute('aria-label', 'Resize conversation');
      handle.setAttribute('aria-valuemin', '0');
      handle.setAttribute('aria-valuemax', String(RESTS.length - 1));
      handle.setAttribute('aria-valuenow', String(RESTS.indexOf(rest)));
      handle.setAttribute('aria-valuetext', REST_TEXT[rest]);
    } else {
      delete aside.dataset['rest'];
      aside.style.removeProperty('--sheet-top');
      handle.setAttribute('aria-orientation', 'vertical');
      handle.setAttribute('aria-label', 'Resize sidebar');
      handle.setAttribute('aria-valuemin', String(SIDEBAR.min));
      handle.setAttribute('aria-valuemax', String(SIDEBAR.max));
      handle.setAttribute('aria-valuenow', String(px));
      handle.setAttribute('aria-valuetext', `${px} pixels`);
    }
  }

  function setHidden(next: boolean): void {
    if (phone || next === hidden) return;
    hidden = next;
    write(HIDDEN_KEY, hidden ? '1' : null);
    apply();
    changed(true);
    (hidden ? show : hide).focus();
  }

  function setRest(next: Rest): void {
    rest = next;
    write(REST_KEY, rest);
    apply();
  }

  let dragging: number | null = null;
  handle.addEventListener('pointerdown', (ev) => {
    // The main button only, and one pointer at a time.
    if (ev.button !== 0 || dragging !== null) return;
    ev.preventDefault();
    dragging = ev.pointerId;
    handle.setPointerCapture(ev.pointerId);
    // A sheet: where on it the finger is, so the grab point stays under it; and its size now.
    const sheet = phone;
    const a = askHeight();
    const grab = ev.clientY - aside.getBoundingClientRect().top;
    const startY = ev.clientY;
    let top = sheetTop(rest, innerHeight, a);
    if (sheet) aside.toggleAttribute('data-dragging', true);
    const move = (e: PointerEvent): void => {
      if (e.pointerId !== dragging) return;
      if (sheet) {
        top = dragTop(e.clientY, grab, innerHeight, a);
        placeSheet(top);
        return;
      }
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
      if (sheet) {
        aside.removeAttribute('data-dragging');
        // Barely moved: a tap, which steps the rest (collapsed shows no title to tap).
        const tap = e.type === 'pointerup' && Math.abs(e.clientY - startY) < TAP_SLOP;
        setRest(tap ? tapped(rest) : settle(top, innerHeight, a));
      } else write(WIDTH_KEY, String(px));
    };
    handle.addEventListener('pointermove', move);
    for (const type of ENDS) handle.addEventListener(type, end);
  });
  handle.addEventListener('keydown', (ev) => {
    if (phone) {
      const next = stepRest(rest, ev.key);
      if (next === null) return;
      ev.preventDefault();
      setRest(next);
      return;
    }
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
  addEventListener('resize', () => {
    const now = isPhone(innerWidth);
    if (now === phone) {
      // A phone's height changes (its keyboard, turning it): the sheet keeps its rest.
      if (phone) apply();
      return;
    }
    phone = now;
    apply();
    // Hidden, inert and the orb's place all change with the mode; a resize is followed exactly.
    changed(false);
  });
  // The ask bar grows with what is typed; a collapsed sheet grows with it.
  if (typeof ResizeObserver !== 'undefined') {
    new ResizeObserver(() => {
      if (phone && dragging === null) apply();
    }).observe(ask);
  }
  apply();

  return {
    width: () => (isHidden() || phone ? 0 : px),
    get hidden() {
      return isHidden();
    },
    setHidden,
    toggle: () => setHidden(!hidden),
    raise: (to) => {
      if (phone && higher(rest, to) !== rest) setRest(to);
    },
    onChange: (l) => listeners.push(l),
  };
}
