/**
 * The one place orb states are defined (GAL-2). Every visual parameter
 * eases toward the current state's target each frame; nothing else in the
 * package may hardcode a per-state value.
 *
 * Colours are 0..1 RGB floats passed straight to the shaders.
 */

export type OrbStateName = 'idle' | 'arming' | 'listening' | 'processing' | 'speaking' | 'error';

export const ORB_STATE_NAMES: readonly OrbStateName[] = [
  'idle', 'arming', 'listening', 'processing', 'speaking', 'error',
];

export type RGB = readonly [number, number, number];

export interface OrbParams {
  readonly color: RGB;
  readonly colorSecondary: RGB;
  readonly opacity: number;
  readonly fresnelPower: number;
  readonly noiseSpeed: number;
  readonly displacementAmp: number;
  /** Base mix between color and colorSecondary. */
  readonly colorMix: number;
  readonly glowOpacity: number;
  readonly rotationSpeed: number;
  /** Opacity of the three orbital rings. */
  readonly ringOpacity: number;
  /** 0..1: how strongly the colour mix swings on a 2s cycle, plus a pulse. */
  readonly colorOscillation: number;
}

export const ORB_STATES: Readonly<Record<OrbStateName, OrbParams>> = {
  idle: {
    color: [0.07, 0.33, 0.26], colorSecondary: [0.07, 0.33, 0.26],
    opacity: 0.55, fresnelPower: 3.0, noiseSpeed: 0.2, displacementAmp: 0.08,
    colorMix: 0, glowOpacity: 0.04, rotationSpeed: 0.08, ringOpacity: 0, colorOscillation: 0,
  },
  // The mic was requested but no audio has arrived yet: honest feedback.
  arming: {
    color: [0.55, 0.4, 0.12], colorSecondary: [0.55, 0.4, 0.12],
    opacity: 0.6, fresnelPower: 3.0, noiseSpeed: 0.3, displacementAmp: 0.12,
    colorMix: 0, glowOpacity: 0.08, rotationSpeed: 0.1, ringOpacity: 0, colorOscillation: 0,
  },
  // Gold: the recording light. Nothing else in the cosmos is this warm.
  listening: {
    color: [1.0, 0.78, 0.15], colorSecondary: [1.0, 0.9, 0.45],
    opacity: 0.95, fresnelPower: 2.2, noiseSpeed: 0.55, displacementAmp: 0.45,
    colorMix: 0, glowOpacity: 0.34, rotationSpeed: 0.15, ringOpacity: 0, colorOscillation: 0,
  },
  processing: {
    color: [0.18, 0.83, 0.66], colorSecondary: [0.65, 0.55, 0.98],
    opacity: 0.5, fresnelPower: 2.8, noiseSpeed: 0.8, displacementAmp: 0.2,
    colorMix: 0, glowOpacity: 0.06, rotationSpeed: 0.35, ringOpacity: 0.6, colorOscillation: 1,
  },
  speaking: {
    color: [0.18, 0.85, 0.65], colorSecondary: [0.25, 0.92, 0.75],
    opacity: 0.65, fresnelPower: 2.2, noiseSpeed: 0.7, displacementAmp: 0.8,
    colorMix: 0, glowOpacity: 0.18, rotationSpeed: 0.2, ringOpacity: 0, colorOscillation: 0,
  },
  error: {
    color: [0.94, 0.27, 0.27], colorSecondary: [0.94, 0.27, 0.27],
    opacity: 0.65, fresnelPower: 4.0, noiseSpeed: 1.0, displacementAmp: 0.12,
    colorMix: 0, glowOpacity: 0.06, rotationSpeed: 0.03, ringOpacity: 0, colorOscillation: 0,
  },
};

/** Text for the accessibility live region (SUR-6). Not spoken by the agent. */
export const STATE_LABELS: Readonly<Record<OrbStateName, string>> = {
  idle: 'Idle',
  arming: 'Waiting for the microphone',
  listening: 'Listening',
  processing: 'Thinking',
  speaking: 'Speaking',
  error: 'Error',
};
