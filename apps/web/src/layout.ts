/**
 * Where the orb goes, as a pure function of the window and what is open, so every
 * arrangement can be tested without a browser. Sizes are CSS pixels.
 */
import { defaultRect, type StageRect } from '@hellotrillion/galaxy';

export const PHONE_MAX = 768;
export const SIDEBAR = { min: 300, max: 520, initial: 360 } as const;
export const DOCK = { size: 96, inset: 24, phoneSize: 64, phoneInset: 12 } as const;

export interface Frame {
  readonly w: number;
  readonly h: number;
  /** The sidebar's width, 0 when hidden or on a phone. */
  readonly sidebar: number;
  readonly workspace: boolean;
  readonly phone: boolean;
}

export const isPhone = (width: number): boolean => width < PHONE_MAX;

export function orbRect(f: Frame): StageRect {
  if (f.workspace) {
    return f.phone
      ? { x: f.w / 2, y: DOCK.phoneInset + DOCK.phoneSize / 2, size: DOCK.phoneSize }
      : { x: f.w - DOCK.inset - DOCK.size / 2, y: f.h - DOCK.inset - DOCK.size / 2, size: DOCK.size };
  }
  // At rest the orb is the galaxy's own centred one, moved over to sit in the stage.
  const left = f.phone ? 0 : f.sidebar;
  const rest = defaultRect(f.w - left, f.h);
  return { ...rest, x: left + rest.x };
}

export const clampWidth = (px: number): number => Math.round(Math.min(SIDEBAR.max, Math.max(SIDEBAR.min, px)));

/** The phone's bottom sheet: where it rests, lowest first. */
export type Rest = 'collapsed' | 'half' | 'full';
export const RESTS: readonly Rest[] = ['collapsed', 'half', 'full'];
/** Full stops below the docked orb, so the orb and its button are never under the sheet. */
export const SHEET = { full: DOCK.phoneInset * 2 + DOCK.phoneSize } as const;

/**
 * The sheet's top edge in px from the top of a window `h` tall, for each rest.
 * Collapsed leaves the ask bar showing (the sheet's height then, `ask`).
 */
export function sheetTop(rest: Rest, h: number, ask: number): number {
  return rest === 'full' ? SHEET.full : rest === 'half' ? Math.round(h / 2) : h - ask;
}

/** Where a drag that ended at `top` settles: the nearest rest. */
export function settle(top: number, h: number, ask: number): Rest {
  return RESTS.reduce((best, r) =>
    Math.abs(sheetTop(r, h, ask) - top) < Math.abs(sheetTop(best, h, ask) - top) ? r : best,
  );
}

/** The top while dragged: the grab point stays under the finger, between full and collapsed. */
export function dragTop(pointerY: number, grab: number, h: number, ask: number): number {
  return Math.min(sheetTop('collapsed', h, ask), Math.max(SHEET.full, pointerY - grab));
}

/** A drag shorter than this, in CSS px, is a tap on the handle. */
export const TAP_SLOP = 6;

/** The rest after a tap on the handle: one up, and from full back down to collapsed. */
export const tapped = (rest: Rest): Rest => (rest === 'collapsed' ? 'half' : rest === 'half' ? 'full' : 'collapsed');

/** The rest after a key on the handle (arrows step, Home and End go to the ends), or null for any other key. */
export function stepRest(rest: Rest, key: string): Rest | null {
  const i = RESTS.indexOf(rest);
  if (key === 'ArrowUp') return RESTS[Math.min(RESTS.length - 1, i + 1)] ?? rest;
  if (key === 'ArrowDown') return RESTS[Math.max(0, i - 1)] ?? rest;
  if (key === 'Home') return 'collapsed';
  if (key === 'End') return 'full';
  return null;
}
