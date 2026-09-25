import { describe, expect, it } from 'vitest';
import { ORB_STATE_NAMES, ORB_STATES, STATE_LABELS, type OrbParams } from '../src/states';

const KEYS: (keyof OrbParams)[] = [
  'color', 'colorSecondary', 'opacity', 'fresnelPower', 'noiseSpeed',
  'displacementAmp', 'colorMix', 'glowOpacity', 'rotationSpeed', 'ringOpacity', 'colorOscillation',
];

describe('ORB_STATES', () => {
  it('defines exactly the six PRD states', () => {
    expect([...ORB_STATE_NAMES].sort()).toEqual(
      ['arming', 'error', 'idle', 'listening', 'processing', 'speaking'],
    );
    expect(Object.keys(ORB_STATES).sort()).toEqual([...ORB_STATE_NAMES].sort());
  });

  it('gives every state every parameter, with colours in 0..1', () => {
    for (const name of ORB_STATE_NAMES) {
      const s = ORB_STATES[name];
      for (const k of KEYS) expect(s[k], `${name}.${k}`).toBeDefined();
      for (const c of [...s.color, ...s.colorSecondary]) {
        expect(c).toBeGreaterThanOrEqual(0);
        expect(c).toBeLessThanOrEqual(1);
      }
    }
  });

  it('matches Appendix B where it is specific', () => {
    expect(ORB_STATES.idle.color).toEqual([0.07, 0.33, 0.26]);
    expect(ORB_STATES.idle.opacity).toBe(0.55);
    expect(ORB_STATES.idle.displacementAmp).toBe(0.08);
    expect(ORB_STATES.arming.color).toEqual([0.55, 0.4, 0.12]);
    expect(ORB_STATES.listening.color).toEqual([1.0, 0.78, 0.15]);
    expect(ORB_STATES.listening.colorSecondary).toEqual([1.0, 0.9, 0.45]);
    expect(ORB_STATES.listening.displacementAmp).toBe(0.45);
    expect(ORB_STATES.listening.glowOpacity).toBe(0.34);
    expect(ORB_STATES.processing.colorSecondary).toEqual([0.65, 0.55, 0.98]);
    expect(ORB_STATES.speaking.color).toEqual([0.18, 0.85, 0.65]);
    expect(ORB_STATES.speaking.displacementAmp).toBe(0.8);
    expect(ORB_STATES.error.color).toEqual([0.94, 0.27, 0.27]);
  });

  it('shows rings and oscillates colour only while processing', () => {
    for (const name of ORB_STATE_NAMES) {
      const expected = name === 'processing';
      expect(ORB_STATES[name].ringOpacity > 0, name).toBe(expected);
      expect(ORB_STATES[name].colorOscillation > 0, name).toBe(expected);
    }
    expect(ORB_STATES.processing.ringOpacity).toBe(0.6);
  });

  it('has a human label for every state (SUR-6 live region)', () => {
    for (const name of ORB_STATE_NAMES) expect(STATE_LABELS[name].length).toBeGreaterThan(0);
  });
});
