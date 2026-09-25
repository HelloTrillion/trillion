/** GAL-6: automatic and manual performance mode, and reduced motion. */

export type PerfMode = 'auto' | 'on' | 'off';

export const PERF_FPS_THRESHOLD = 45;
export const PERF_HOLD_MS = 3000;

export class PerfGovernor {
  private mode: PerfMode;
  private lowSince: number | null = null;
  private autoLatched = false;

  constructor(mode: PerfMode = 'auto') {
    this.mode = mode;
  }

  setMode(mode: PerfMode): void {
    this.mode = mode;
    this.lowSince = null;
  }

  getMode(): PerfMode {
    return this.mode;
  }

  /** Feed the current fps estimate; returns whether perf mode is active. */
  sample(fps: number, nowMs: number): boolean {
    // Track the low-fps window regardless of mode, so a mode switched back
    // to 'auto' reflects fps history rather than resetting the latch.
    if (fps < PERF_FPS_THRESHOLD) {
      this.lowSince ??= nowMs;
      if (nowMs - this.lowSince >= PERF_HOLD_MS) this.autoLatched = true;
    } else {
      this.lowSince = null;
    }
    if (this.mode === 'on') return true;
    if (this.mode === 'off') return false;
    return this.autoLatched;
  }
}

export interface Quality {
  readonly pixelRatio: number;
  readonly orbDetail: number;
  readonly dustCount: number;
  readonly nodeCount: number;
}

export function qualityFor(perfActive: boolean, devicePixelRatio: number): Quality {
  return perfActive
    ? { pixelRatio: 1, orbDetail: 12, dustCount: 200, nodeCount: 60 }
    : { pixelRatio: Math.min(2, devicePixelRatio), orbDetail: 24, dustCount: 500, nodeCount: 100 };
}

/** Multiplier on every time-driven motion; honours prefers-reduced-motion. */
export function motionScale(reducedMotion: boolean): number {
  return reducedMotion ? 0.25 : 1;
}
