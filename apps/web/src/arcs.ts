/**
 * Energy arcs (GAL-8): where the orb's arc to a tool's chip should land, as a pure
 * function of boxes measured on the page, so it is tested without a browser.
 * The galaxy draws the arc; perf mode and reduced motion are its to honour.
 */
import type { Point } from '@hellotrillion/galaxy';

/** A box in CSS pixels from the window's top-left, as getBoundingClientRect gives it. */
export interface Box {
  readonly left: number;
  readonly top: number;
  readonly right: number;
  readonly bottom: number;
}

export interface ArcScene {
  /** The tool's chip, or null when it is not on the page. */
  readonly chip: Box | null;
  /** What the chip scrolls inside (the transcript): a chip scrolled out of it is off screen. */
  readonly clip: Box;
  /** The window's size. */
  readonly viewport: { readonly w: number; readonly h: number };
  /** The galaxy's canvas: the arc's point is measured from its top-left. */
  readonly canvas: Box;
  /** Where the orb's centre is on the page, or null when it cannot be seen. */
  readonly orb: Point | null;
}

const within = (v: number, lo: number, hi: number): boolean => v >= lo && v <= hi;
const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v));

/**
 * The arc's end in canvas pixels: the point of the chip nearest the orb. Null when
 * the orb cannot be seen, or the chip is missing, empty, or its middle is scrolled or
 * slid off screen (a hidden sidebar's chips measure as empty or outside the window).
 */
export function arcTarget(s: ArcScene): Point | null {
  const { chip, clip, viewport, canvas, orb } = s;
  if (!orb || !chip) return null;
  if (chip.right - chip.left <= 0 || chip.bottom - chip.top <= 0) return null;
  const cx = (chip.left + chip.right) / 2;
  const cy = (chip.top + chip.bottom) / 2;
  const seen = (b: Box): boolean => within(cx, b.left, b.right) && within(cy, b.top, b.bottom);
  if (!seen(clip) || !seen({ left: 0, top: 0, right: viewport.w, bottom: viewport.h })) return null;
  return {
    x: clamp(orb.x, chip.left, chip.right) - canvas.left,
    y: clamp(orb.y, chip.top, chip.bottom) - canvas.top,
  };
}

/** The orb is seen when the page is and its centre is in the window. */
export function orbSeen(centre: Point | null, viewport: { readonly w: number; readonly h: number }, pageHidden: boolean): Point | null {
  if (pageHidden || !centre) return null;
  return within(centre.x, 0, viewport.w) && within(centre.y, 0, viewport.h) ? centre : null;
}
