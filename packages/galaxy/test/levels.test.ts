import { describe, expect, it } from 'vitest';
import {
  ATTACK, DECAY, MIC_FLOOR, SILENT, applyRole, bandLevels, smooth, syntheticListening,
  syntheticSpeech,
} from '../src/audio/levels';

const bins = (fill: (i: number) => number, n = 128): Uint8Array =>
  Uint8Array.from({ length: n }, (_, i) => fill(i));

describe('bandLevels', () => {
  it('reads silence as zero', () => {
    expect(bandLevels(bins(() => 0))).toEqual(SILENT);
  });

  it('reads a full spectrum as one in every band', () => {
    const l = bandLevels(bins(() => 255));
    expect(l.level).toBeCloseTo(1);
    expect(l.bass).toBeCloseTo(1);
    expect(l.treble).toBeCloseTo(1);
  });

  it('uses bins 10–60% for voice, the first 6 for bass, 65%+ for treble', () => {
    // Energy only in bins 0..5 (bass) — voice band starts at 12 for 128 bins.
    const bassOnly = bandLevels(bins((i) => (i < 6 ? 255 : 0)));
    expect(bassOnly.bass).toBeCloseTo(1);
    expect(bassOnly.level).toBe(0);
    expect(bassOnly.treble).toBe(0);
    const trebleOnly = bandLevels(bins((i) => (i >= 84 ? 255 : 0)));
    expect(trebleOnly.treble).toBeCloseTo(1);
    expect(trebleOnly.level).toBe(0);
  });

  it('treats an empty array as silence', () => {
    expect(bandLevels(new Uint8Array(0))).toEqual(SILENT);
  });
});

describe('applyRole', () => {
  it('boosts the mic ×2.4 with a 0.10 floor, capped at 1', () => {
    expect(applyRole({ level: 0.1, bass: 0, treble: 0 }, 'mic').level).toBeCloseTo(0.24);
    expect(applyRole(SILENT, 'mic').level).toBe(MIC_FLOOR);
    expect(applyRole({ level: 0.9, bass: 0, treble: 0 }, 'mic').level).toBe(1);
  });

  it('leaves playback alone', () => {
    const l = { level: 0.3, bass: 0.2, treble: 0.1 };
    expect(applyRole(l, 'playback')).toEqual(l);
  });
});

describe('smooth', () => {
  it('attacks fast and decays slowly', () => {
    const up = smooth(SILENT, { level: 1, bass: 1, treble: 1 });
    expect(up.level).toBeCloseTo(ATTACK);
    const down = smooth({ level: 1, bass: 1, treble: 1 }, SILENT);
    expect(down.level).toBeCloseTo(1 - DECAY);
  });
});

describe('synthetic envelopes', () => {
  it('are deterministic and bounded', () => {
    for (const t of [0, 0.37, 1.5, 12.25]) {
      const a = syntheticSpeech(t);
      expect(syntheticSpeech(t)).toEqual(a);
      for (const v of [a.level, a.bass, a.treble]) {
        expect(v).toBeGreaterThanOrEqual(0);
        expect(v).toBeLessThanOrEqual(1);
      }
      const l = syntheticListening(t);
      expect(l.level).toBeGreaterThanOrEqual(0);
      expect(l.level).toBeLessThanOrEqual(1);
    }
  });
});
