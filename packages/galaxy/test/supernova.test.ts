import { describe, expect, it } from 'vitest';
import { Mesh } from 'three';
import { SILENT } from '../src/audio/levels';
import { Orb } from '../src/orb/orb';
import { ORB_STATES } from '../src/states';
import {
  GENTLE_PEAK, NO_NOVA, RISE, SUPERNOVA, SUPERNOVA_TIERS, Supernova, duration, envelope, novaFrame,
} from '../src/supernova';

const FULL = { reduced: false, perf: false };

describe('the supernova envelope (GAL-9)', () => {
  it('rises to one at the end of the rise and falls back to nothing', () => {
    expect(envelope(0)).toBe(0);
    expect(envelope(RISE)).toBeCloseTo(1, 9);
    expect(envelope(1)).toBe(0);
    expect(envelope(-0.1)).toBe(0);
    expect(envelope(1.1)).toBe(0);
    // Eased: fast at first, then slowing into the peak.
    expect(envelope(RISE / 2)).toBeGreaterThan(0.5);
  });

  it('only climbs while rising and only falls after', () => {
    let last = 0;
    for (let p = 0.001; p < RISE; p += 0.001) {
      expect(envelope(p)).toBeGreaterThanOrEqual(last);
      last = envelope(p);
    }
    last = 1;
    for (let p = RISE; p < 1; p += 0.001) {
      expect(envelope(p)).toBeLessThanOrEqual(last + 1e-12);
      last = envelope(p);
    }
  });
});

describe('the tiers', () => {
  it('are each brighter, bigger and longer than the one before', () => {
    const [s, m, l] = SUPERNOVA_TIERS.map((t) => SUPERNOVA[t]);
    for (const [a, b] of [[s!, m!], [m!, l!]] as const) {
      expect(b.peak).toBeGreaterThan(a.peak);
      expect(b.swell).toBeGreaterThan(a.swell);
      expect(b.wave).toBeGreaterThan(a.wave);
      expect(b.seconds).toBeGreaterThan(a.seconds);
    }
    // A small one is a swell of light, with no wave.
    expect(s!.wave).toBe(0);
  });

  it('peak where the rise ends', () => {
    for (const tier of SUPERNOVA_TIERS) {
      const at = novaFrame(tier, duration(tier, false) * RISE, FULL);
      expect(at.glow).toBeCloseTo(SUPERNOVA[tier].peak, 9);
      expect(at.swell).toBeCloseTo(SUPERNOVA[tier].swell, 9);
    }
  });

  it('send the wave out from the surface, widening and fading', () => {
    const len = duration('large', false);
    const early = novaFrame('large', len * 0.05, FULL);
    const late = novaFrame('large', len * 0.7, FULL);
    expect(early.wave).toBeGreaterThan(1);
    expect(late.wave).toBeGreaterThan(early.wave);
    expect(late.wave).toBeLessThanOrEqual(SUPERNOVA.large.wave);
    expect(late.waveOpacity).toBeLessThan(early.waveOpacity);
  });

  it('are nothing before they start and after they end', () => {
    for (const tier of SUPERNOVA_TIERS) {
      expect(novaFrame(tier, 0, FULL)).toEqual(NO_NOVA);
      expect(novaFrame(tier, duration(tier, false), FULL)).toEqual(NO_NOVA);
    }
  });
});

describe('reduced motion and performance mode (GAL-6)', () => {
  it('reduced motion is a gentle glow: no swell, no wave, softer and slower', () => {
    for (const tier of SUPERNOVA_TIERS) {
      const len = duration(tier, true);
      expect(len).toBeGreaterThan(duration(tier, false));
      let brightest = 0;
      for (let t = 0; t < len; t += 1 / 60) {
        const f = novaFrame(tier, t, { reduced: true, perf: false });
        expect(f.swell).toBe(0);
        expect(f.wave).toBe(0);
        expect(f.waveOpacity).toBe(0);
        brightest = Math.max(brightest, f.glow);
      }
      expect(brightest).toBeGreaterThan(0);
      expect(brightest).toBeLessThanOrEqual(SUPERNOVA[tier].peak * GENTLE_PEAK + 1e-9);
    }
  });

  it('performance mode keeps the glow and swell and draws no wave', () => {
    const t = duration('large', false) * 0.3;
    const full = novaFrame('large', t, FULL);
    const light = novaFrame('large', t, { reduced: false, perf: true });
    expect(light.glow).toBe(full.glow);
    expect(light.swell).toBe(full.swell);
    expect(full.waveOpacity).toBeGreaterThan(0);
    expect([light.wave, light.waveOpacity]).toEqual([0, 0]);
  });
});

describe('playing a burst', () => {
  const play = (n: Supernova, seconds: number, fps: number) => {
    let f = NO_NOVA;
    for (let i = 0; i < Math.round(seconds * fps); i++) f = n.step(1 / fps, false);
    return f;
  };

  it('looks the same at any frame rate', () => {
    const a = new Supernova();
    const b = new Supernova();
    a.fire('medium', false);
    b.fire('medium', false);
    const at60 = play(a, 0.5, 60);
    const at30 = play(b, 0.5, 30);
    expect(at30.glow).toBeCloseTo(at60.glow, 9);
    expect(at30.wave).toBeCloseTo(at60.wave, 9);
  });

  it('ends by itself', () => {
    const n = new Supernova();
    n.fire('large', false);
    expect(n.playing).toBe(true);
    expect(play(n, duration('large', false) + 0.1, 60)).toEqual(NO_NOVA);
    expect(n.playing).toBe(false);
  });

  it('a bigger burst replaces a smaller; a smaller or equal one meanwhile is ignored', () => {
    const n = new Supernova();
    expect(n.fire('medium', false)).toBe(true);
    play(n, 0.3, 60);
    expect(n.fire('small', false)).toBe(false);
    expect(n.fire('medium', false)).toBe(false);
    expect(n.fire('large', false)).toBe(true);
    // Started again from the beginning, as a large one.
    const f = play(n, duration('large', false) * RISE, 60);
    expect(f.glow).toBeCloseTo(SUPERNOVA.large.peak, 2);
  });

  it('refuses a tier it does not know', () => {
    const n = new Supernova();
    expect(n.fire('huge' as never, false)).toBe(false);
    expect(n.playing).toBe(false);
  });

  it('keeps its shape if reduced motion changes part way', () => {
    const n = new Supernova();
    n.fire('large', true);
    const f = play(n, 1, 60);
    expect(f.wave).toBe(0);
    expect(f.swell).toBe(0);
  });
});

describe('the orb during a burst', () => {
  const frame = (nova = NO_NOVA) => ({
    params: ORB_STATES.idle, levels: SILENT, time: 0, dt: 1 / 60, flare: 0, motion: 1, nova,
  });
  const wave = (orb: Orb) =>
    orb.group.children.find((c): c is Mesh => c instanceof Mesh && c.geometry.type === 'IcosahedronGeometry' && c.scale.x > 1.2);

  it('shows the wave only while one is travelling', () => {
    const orb = new Orb(1);
    orb.update(frame());
    const meshes = orb.group.children.filter((c): c is Mesh => c instanceof Mesh);
    const hiddenBefore = meshes.filter((m) => !m.visible).length;
    orb.update(frame(novaFrame('large', 1, FULL)));
    expect(wave(orb)?.visible).toBe(true);
    orb.update(frame());
    expect(meshes.filter((m) => !m.visible).length).toBe(hiddenBefore);
  });
});
