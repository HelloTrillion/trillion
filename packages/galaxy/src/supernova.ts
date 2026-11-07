/**
 * GAL-9: the Supernova, a celebration in three tiers. The host decides when (in
 * Trillion, only the attention engine does); this is how it looks, frame by frame.
 *
 * Every tier has the same shape: a quick eased rise to its peak, then a slow eased
 * fall to nothing. A bigger tier is brighter, swells more, sends a wider wave and
 * lasts longer. Reduced motion gets a gentle glow and nothing that moves; performance
 * mode skips the wave, which is extra geometry to draw.
 */

export type SupernovaTier = 'small' | 'medium' | 'large';

export const SUPERNOVA_TIERS: readonly SupernovaTier[] = ['small', 'medium', 'large'];

export interface TierSpec {
  /** Extra glow at the peak, on the same scale as `flare`. */
  readonly peak: number;
  /** How much the orb swells at the peak, as a share of its size. */
  readonly swell: number;
  /** The shock wave's widest radius, in orb radii; 0 for none. */
  readonly wave: number;
  /** From start to nothing. */
  readonly seconds: number;
}

export const SUPERNOVA: Readonly<Record<SupernovaTier, TierSpec>> = {
  small: { peak: 0.6, swell: 0.06, wave: 0, seconds: 1.6 },
  medium: { peak: 1.0, swell: 0.1, wave: 2.6, seconds: 2.4 },
  large: { peak: 1.5, swell: 0.16, wave: 4.0, seconds: 3.6 },
};

/** The share of a burst spent rising; the rest is the fall. */
export const RISE = 0.15;
/** Reduced motion: a glow this much of the tier's, rising slowly over this much longer. */
export const GENTLE_PEAK = 0.4;
export const GENTLE_STRETCH = 1.5;
export const GENTLE_RISE = 0.4;

/** What a burst adds to the orb this frame. All zero when nothing is playing. */
export interface NovaFrame {
  readonly glow: number;
  readonly swell: number;
  /** The wave's radius in orb radii, and how visible it is. */
  readonly wave: number;
  readonly waveOpacity: number;
}

export const NO_NOVA: NovaFrame = { glow: 0, swell: 0, wave: 0, waveOpacity: 0 };

const easeOutCubic = (x: number): number => 1 - Math.pow(1 - x, 3);
const easeInOutSine = (x: number): number => -(Math.cos(Math.PI * x) - 1) / 2;
const clamp01 = (x: number): number => Math.min(1, Math.max(0, x));

/** 0 → 1 → 0 over `p` from 0 to 1: an eased rise over `rise`, an eased fall after. */
export function envelope(p: number, rise: number = RISE): number {
  if (p <= 0 || p >= 1) return 0;
  if (p < rise) return easeOutCubic(p / rise);
  return 1 - easeInOutSine((p - rise) / (1 - rise));
}

/** How long a tier lasts, as it will be drawn. */
export function duration(tier: SupernovaTier, reduced: boolean): number {
  return SUPERNOVA[tier].seconds * (reduced ? GENTLE_STRETCH : 1);
}

/** The burst `t` seconds in. */
export function novaFrame(tier: SupernovaTier, t: number, opts: { reduced: boolean; perf: boolean }): NovaFrame {
  const spec = SUPERNOVA[tier];
  const length = duration(tier, opts.reduced);
  const p = t / length;
  if (!(p > 0 && p < 1)) return NO_NOVA;
  if (opts.reduced) {
    // A soft brightening and nothing that moves: no swell, no wave.
    return { glow: spec.peak * GENTLE_PEAK * envelope(p, GENTLE_RISE), swell: 0, wave: 0, waveOpacity: 0 };
  }
  const e = envelope(p);
  // The wave leaves the surface at the start and slows as it goes, fading as it widens.
  const travelled = easeOutCubic(clamp01(p / 0.85));
  const drawWave = spec.wave > 0 && !opts.perf;
  return {
    glow: spec.peak * e,
    swell: spec.swell * e,
    wave: drawWave ? 1 + (spec.wave - 1) * travelled : 0,
    waveOpacity: drawWave ? Math.pow(1 - travelled, 1.5) * Math.min(1, spec.peak) : 0,
  };
}

const RANK: Readonly<Record<SupernovaTier, number>> = { small: 0, medium: 1, large: 2 };

/** Plays one burst at a time. A bigger one replaces a smaller; a smaller or equal one
 * while a burst plays is ignored, so two arriving together are one celebration. */
export class Supernova {
  private tier: SupernovaTier | null = null;
  private t = 0;
  private length = 0;
  // A burst keeps the shape it started with, even if reduced motion changes meanwhile.
  private reduced = false;

  /** Whether it was taken. */
  fire(tier: SupernovaTier, reduced: boolean): boolean {
    if (!(tier in SUPERNOVA)) return false;
    if (this.playing && this.tier !== null && RANK[tier] <= RANK[this.tier]) return false;
    this.tier = tier;
    this.t = 0;
    this.reduced = reduced;
    this.length = duration(tier, reduced);
    return true;
  }

  get playing(): boolean {
    return this.tier !== null && this.t < this.length;
  }

  /** Advance by `dt` seconds of real time and say what to draw. */
  step(dt: number, perf: boolean): NovaFrame {
    if (this.tier === null) return NO_NOVA;
    this.t += Math.max(0, dt);
    if (this.t >= this.length) {
      this.tier = null;
      return NO_NOVA;
    }
    return novaFrame(this.tier, this.t, { reduced: this.reduced, perf });
  }
}
