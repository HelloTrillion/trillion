/**
 * Where the orb sits on screen (GAL-5). The host gives a centre and a diameter in
 * CSS pixels; the orb's camera is shifted and zoomed to match, so the landscape
 * behind it, which has its own camera, never moves.
 */
import type { PerspectiveCamera } from 'three';
import { approach } from './easing';

/**
 * CSS pixels from the canvas's top-left: the orb's centre, and the outer ring's
 * diameter. A size of zero or less gives a tiny orb rather than none.
 */
export interface StageRect {
  readonly x: number;
  readonly y: number;
  readonly size: number;
}

/** The outer ring's diameter in world units (rings reach radius 1.75). */
export const ORB_EXTENT = 3.5;
/** How far the orb's camera sits from it. */
export const ORB_DISTANCE = 4.2;
/** The orb camera's vertical field of view, in degrees. */
export const ORB_FOV = 45;
/** Per-60fps-frame share: about 98% of the way there after 300 ms (18 frames). */
const RATE = 0.2;
/** Closer than this on every axis counts as there, so easing ends. */
const SNAP = 0.1;

/** World units visible top to bottom at the orb's plane, before zoom. */
const viewHeight = (fov: number): number => 2 * ORB_DISTANCE * Math.tan(((fov / 2) * Math.PI) / 180);

/**
 * Where the orb sits without setStage: centred, and on portrait screens zoomed out
 * so it keeps the same share of the short side (zoom = min(1, w/h)).
 */
export function defaultRect(w: number, h: number): StageRect {
  return { x: w / 2, y: h / 2, size: (ORB_EXTENT / viewHeight(ORB_FOV)) * Math.min(w, h) };
}

export function placeOrb(camera: PerspectiveCamera, rect: StageRect, w: number, h: number): void {
  const view = viewHeight(camera.fov);
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

/**
 * One frame towards the target. Returns `target` itself once within SNAP, and the
 * same object again once settled, so the caller can skip the matrix work.
 */
export function stepStage(stage: StageRect, target: StageRect, dt: number, jump: boolean): StageRect {
  if (stage === target) return stage;
  const next = easeRect(stage, target, dt, jump);
  const near = Math.abs(next.x - target.x) < SNAP && Math.abs(next.y - target.y) < SNAP
    && Math.abs(next.size - target.size) < SNAP;
  return near ? target : next;
}
