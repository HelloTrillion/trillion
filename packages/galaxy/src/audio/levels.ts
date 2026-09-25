/**
 * GAL-3 audio maths. Pure: input is an analyser's byte frequency data
 * (0..255 per bin), output is 0..1 levels. Constants are from Appendix B/C.
 */

export interface Levels {
  readonly level: number;
  readonly bass: number;
  readonly treble: number;
}

export type AnalyserRole = 'mic' | 'playback';

export const SILENT: Levels = { level: 0, bass: 0, treble: 0 };
export const ATTACK = 0.45;
export const DECAY = 0.08;
export const MIC_GAIN = 2.4;
export const MIC_FLOOR = 0.1;
const MIC_BASS_GAIN = 2.0;

function meanRange(bins: ArrayLike<number>, start: number, end: number): number {
  if (end <= start) return 0;
  let sum = 0;
  for (let i = start; i < end; i++) sum += bins[i] ?? 0;
  return sum / ((end - start) * 255);
}

export function bandLevels(bins: ArrayLike<number>): Levels {
  const n = bins.length;
  if (n === 0) return SILENT;
  return {
    level: meanRange(bins, Math.max(1, Math.floor(n * 0.1)), Math.floor(n * 0.6)),
    bass: meanRange(bins, 0, Math.min(6, n)),
    treble: meanRange(bins, Math.ceil(n * 0.65), n),
  };
}

const clamp01 = (v: number): number => Math.min(1, Math.max(0, v));

/** Mic input is quieter than playback; lift it so the orb visibly listens. */
export function applyRole(l: Levels, role: AnalyserRole): Levels {
  if (role === 'playback') return l;
  return {
    level: Math.max(MIC_FLOOR, clamp01(l.level * MIC_GAIN)),
    bass: clamp01(l.bass * MIC_BASS_GAIN),
    treble: clamp01(l.treble * MIC_GAIN),
  };
}

function smoothOne(prev: number, next: number): number {
  const k = next > prev ? ATTACK : DECAY;
  return prev + (next - prev) * k;
}

/** Breathe instead of twitch: fast attack, slow decay. */
export function smooth(prev: Levels, next: Levels): Levels {
  return {
    level: smoothOne(prev.level, next.level),
    bass: smoothOne(prev.bass, next.bass),
    treble: smoothOne(prev.treble, next.treble),
  };
}

/** Speech-like envelope for when playback cannot be analysed. Deterministic. */
export function syntheticSpeech(t: number): Levels {
  const wave = Math.sin(t * 4.3) * 0.4 + Math.sin(t * 11.7) * 0.25 + 0.5;
  const flutter = Math.sin(t * 23.1) * 0.05;
  return {
    level: clamp01((wave + flutter) * 0.55),
    bass: clamp01(Math.sin(t * 1.7) * 0.35 + 0.35),
    treble: clamp01(Math.sin(t * 9.1) * 0.2 + 0.25),
  };
}

/** Gentle pulse while listening with no mic attached (demo, before permission). */
export function syntheticListening(t: number): Levels {
  return {
    level: clamp01(0.18 + 0.12 * Math.sin(t * 2.6) + 0.06 * Math.sin(t * 7.3)),
    bass: clamp01(0.08 + 0.06 * Math.sin(t * 1.4)),
    treble: 0.05,
  };
}
