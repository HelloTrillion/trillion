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
