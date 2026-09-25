import type { OrbParams, RGB } from './states';

/** Default per-60fps-frame approach rate for state parameters. */
export const STATE_EASE = 0.07;

/**
 * Fraction of the remaining distance to cover in `dt` seconds, such that
 * the curve is identical at any frame rate: 1 - (1 - k)^(dt·60).
 */
export function approachFactor(k: number, dt: number): number {
  if (dt <= 0) return 0;
  return Math.min(1, 1 - Math.pow(1 - k, dt * 60));
}

export function approach(current: number, target: number, k: number, dt: number): number {
  return current + (target - current) * approachFactor(k, dt);
}

function approachRGB(c: RGB, t: RGB, f: number): RGB {
  return [c[0] + (t[0] - c[0]) * f, c[1] + (t[1] - c[1]) * f, c[2] + (t[2] - c[2]) * f];
}

export function stepParams(
  current: OrbParams,
  target: OrbParams,
  dt: number,
  k: number = STATE_EASE,
): OrbParams {
  const f = approachFactor(k, dt);
  const n = (a: number, b: number): number => a + (b - a) * f;
  return {
    color: approachRGB(current.color, target.color, f),
    colorSecondary: approachRGB(current.colorSecondary, target.colorSecondary, f),
    opacity: n(current.opacity, target.opacity),
    fresnelPower: n(current.fresnelPower, target.fresnelPower),
    noiseSpeed: n(current.noiseSpeed, target.noiseSpeed),
    displacementAmp: n(current.displacementAmp, target.displacementAmp),
    colorMix: n(current.colorMix, target.colorMix),
    glowOpacity: n(current.glowOpacity, target.glowOpacity),
    rotationSpeed: n(current.rotationSpeed, target.rotationSpeed),
    ringOpacity: n(current.ringOpacity, target.ringOpacity),
    colorOscillation: n(current.colorOscillation, target.colorOscillation),
  };
}
