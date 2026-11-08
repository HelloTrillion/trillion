/**
 * Framing the camera round the HUD: how far back to stand so a sphere fits the part of
 * the screen the page leaves free, and how far to shift the picture so what the camera
 * looks at sits in the middle of that part (beside an open panel, not under it).
 * Plain maths, no three.js.
 */

/** CSS pixels the page covers on each side: its top bar, legend and stats, an open panel. */
export interface Insets {
  readonly top: number;
  readonly right: number;
  readonly bottom: number;
  readonly left: number;
}

export const NO_INSETS: Insets = { top: 0, right: 0, bottom: 0, left: 0 };

/** The free part, never less than a fifth of the screen each way. */
function free(w: number, h: number, i: Insets): { w: number; h: number } {
  return { w: Math.max(w * 0.2, w - i.left - i.right), h: Math.max(h * 0.2, h - i.top - i.bottom) };
}

/**
 * The distance at which a sphere of `radius` fits the free part of a `w`×`h` view with a
 * vertical field of view of `fovDeg`, with `margin` to spare.
 */
export function fitDistance(radius: number, fovDeg: number, w: number, h: number, insets: Insets = NO_INSETS, margin = 1.1): number {
  if (!(radius > 0) || !(w > 0) || !(h > 0)) return 0;
  const f = free(w, h, insets);
  const tanV = Math.tan((fovDeg * Math.PI) / 360);
  // Half-angles of the free part, vertically and across.
  const halfV = Math.atan((tanV * f.h) / h);
  const halfH = Math.atan((tanV * f.w) / h);
  return (radius * margin) / Math.sin(Math.min(halfV, halfH));
}

/** How far (CSS pixels, right and down) the centre of the free part is from the view's centre. */
export function freeCentreShift(w: number, h: number, insets: Insets = NO_INSETS): { x: number; y: number } {
  const f = free(w, h, insets);
  return { x: insets.left + f.w / 2 - w / 2, y: insets.top + f.h / 2 - h / 2 };
}
