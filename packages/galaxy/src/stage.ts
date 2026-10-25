/**
 * Where the orb sits on screen (GAL-5). The host gives a centre and a diameter in
 * CSS pixels; the orb's camera is shifted and zoomed to match, so the landscape
 * behind it, which has its own camera, never moves.
 */
import type { PerspectiveCamera } from 'three';
import { approach } from './easing';

/** CSS pixels: the orb's centre, and the outer ring's diameter. */
export interface StageRect {
  readonly x: number;
  readonly y: number;
  readonly size: number;
}

/** The outer ring's diameter in world units (rings reach radius 1.75). */
export const ORB_EXTENT = 3.5;
/** How far the orb's camera sits from it. */
export const ORB_DISTANCE = 4.2;
/** Per-60fps-frame share: about 98% of the way there after 300 ms (18 frames). */
const RATE = 0.2;

export function placeOrb(camera: PerspectiveCamera, rect: StageRect, w: number, h: number): void {
  const view = 2 * ORB_DISTANCE * Math.tan(((camera.fov / 2) * Math.PI) / 180);
  camera.aspect = w / h;
  camera.zoom = Math.max(0.01, (rect.size * view) / (ORB_EXTENT * h));
  camera.updateProjectionMatrix();
  // A lens shift: elements 8 and 9 offset every point in NDC without changing
  // perspective. The frustum is symmetric, so they start at zero.
  const e = camera.projectionMatrix.elements;
  e[8] = -((2 * rect.x) / w - 1);
  e[9] = -(1 - (2 * rect.y) / h);
  camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
}

export function easeRect(from: StageRect, to: StageRect, dt: number, jump: boolean): StageRect {
  if (jump) return to;
  return {
    x: approach(from.x, to.x, RATE, dt),
    y: approach(from.y, to.y, RATE, dt),
    size: approach(from.size, to.size, RATE, dt),
  };
}
