/**
 * The Living Mind's frame-rate governor (spec decision 9): if the frame rate stays
 * under 30 for three seconds, bloom goes off; if it is still low, the particles and
 * travelling pulses go too. One slow frame (a garbage collection) changes nothing.
 * It only ever steps down: a mind that flickered between looks would be worse.
 */

export const LOW_FPS = 30;
export const HOLD_S = 3;
const WINDOW = 60;

/** 0: everything. 1: no bloom. 2: no bloom, no particles, no travelling pulses. */
export type DegradeLevel = 0 | 1 | 2;

export class MindGovernor {
  private readonly times: number[] = [];
  private sum = 0;
  private lowSince: number | null = null;
  private current: DegradeLevel;

  constructor(start: DegradeLevel = 0) {
    this.current = start;
  }

  get level(): DegradeLevel {
    return this.current;
  }

  /** Feed one frame's length and the clock, both in seconds. Returns the level now. */
  sample(dt: number, now: number): DegradeLevel {
    if (!(dt > 0)) return this.current;
    this.times.push(dt);
    this.sum += dt;
    if (this.times.length > WINDOW) this.sum -= this.times.shift() ?? 0;
    if (this.times.length < WINDOW || this.current >= 2) return this.current;
    if (this.times.length / this.sum >= LOW_FPS) {
      this.lowSince = null;
      return this.current;
    }
    this.lowSince ??= now;
    if (now - this.lowSince < HOLD_S) return this.current;
    this.lowSince = null;
    this.current = (this.current + 1) as DegradeLevel;
    return this.current;
  }
}

/** The idle camera turns after this long without a touch, never under reduced motion. */
export const IDLE_ROTATE_MS = 30_000;

export function shouldAutoRotate(idleMs: number, reducedMotion: boolean): boolean {
  return !reducedMotion && idleMs >= IDLE_ROTATE_MS;
}
