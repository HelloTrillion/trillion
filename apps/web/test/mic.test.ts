import { describe, expect, it } from 'vitest';
import { FRAME_SAMPLES, Framer, TARGET_RATE, WAKE_S, Waking, problemOf } from '../src/mic';

const sine = (rate: number, seconds: number, hz = 200, loud = 0.5): Float32Array =>
  Float32Array.from({ length: Math.round(rate * seconds) }, (_, i) => loud * Math.sin((2 * Math.PI * hz * i) / rate));

function feed(framer: Framer, samples: Float32Array, block = 128): Int16Array {
  const frames: ArrayBuffer[] = [];
  for (let i = 0; i < samples.length; i += block) frames.push(...framer.push(samples.subarray(i, i + block)));
  for (const f of frames) expect(f.byteLength).toBe(FRAME_SAMPLES * 2);
  const out = new Int16Array(frames.length * FRAME_SAMPLES);
  frames.forEach((f, i) => out.set(new Int16Array(f), i * FRAME_SAMPLES));
  return out;
}

const rms = (pcm: Int16Array): number => Math.sqrt(pcm.reduce((s, v) => s + v * v, 0) / pcm.length) / 32768;

describe('microphone framing', () => {
  it('passes 16 kHz audio through as whole PCM16 frames', () => {
    const out = feed(new Framer(TARGET_RATE), sine(TARGET_RATE, 1));
    // One second is 16 frames of 960 with 640 samples left waiting for the next block.
    expect(out.length).toBe(Math.floor(TARGET_RATE / FRAME_SAMPLES) * FRAME_SAMPLES);
    expect(rms(out)).toBeCloseTo(0.5 / Math.SQRT2, 2);
  });

  it.each([48_000, 44_100, 96_000])('brings %i Hz down to 16 kHz without changing the speed or level', (rate) => {
    const out = feed(new Framer(rate), sine(rate, 2));
    expect(Math.abs(out.length - 2 * TARGET_RATE)).toBeLessThanOrEqual(FRAME_SAMPLES);
    expect(rms(out)).toBeGreaterThan(0.33);
    // The tone still crosses zero 200 times a second in each direction.
    let rising = 0;
    for (let i = 1; i < out.length; i++) if ((out[i - 1] ?? 0) < 0 && (out[i] ?? 0) >= 0) rising++;
    expect(Math.abs(rising - (200 * out.length) / TARGET_RATE)).toBeLessThanOrEqual(2);
  });

  it('clips instead of wrapping, and keeps silence silent', () => {
    const loud = feed(new Framer(TARGET_RATE), new Float32Array(FRAME_SAMPLES).fill(4));
    expect(Math.max(...loud)).toBe(32767);
    const low = feed(new Framer(TARGET_RATE), new Float32Array(FRAME_SAMPLES).fill(-4));
    expect(Math.min(...low)).toBe(-32768);
    expect(rms(feed(new Framer(48_000), new Float32Array(48_000)))).toBe(0);
  });

  it('gives each frame its own buffer, so one can be sent while the next fills', () => {
    const framer = new Framer(TARGET_RATE);
    const [a] = framer.push(new Float32Array(FRAME_SAMPLES).fill(0.25));
    const [b] = framer.push(new Float32Array(FRAME_SAMPLES).fill(-0.25));
    expect(a).not.toBe(b);
    expect(new Int16Array(a!)[0]).toBeGreaterThan(0);
    expect(new Int16Array(b!)[0]).toBeLessThan(0);
  });

  it('names why a microphone could not be opened', () => {
    const named = (name: string): Error => Object.assign(new Error('x'), { name });
    expect(problemOf(named('NotAllowedError'))).toBe('denied');
    expect(problemOf(named('NotFoundError'))).toBe('no_device');
    expect(problemOf(named('AbortError'))).toBe('failed');
    expect(problemOf('nope')).toBe('failed');
  });
});

describe('a microphone waking up', () => {
  const rate = 48_000;
  const zeros = (seconds: number): Float32Array => new Float32Array(Math.round(rate * seconds));
  function run(w: Waking, samples: Float32Array, block = 128): string[] {
    const seen: string[] = [];
    for (let i = 0; i < samples.length; i += block) seen.push(w.push(samples.subarray(i, i + block)));
    return seen;
  }

  it('skips the exact zeros a USB microphone sends while it wakes, for a few seconds', () => {
    const w = new Waking(rate);
    // The Studio Display's microphone: well over a second of zeros, then the room.
    expect(new Set(run(w, zeros(2.5)))).toEqual(new Set(['skip']));
    expect(run(w, sine(rate, 0.1, 200, 0.001))).toContain('sound');
    // Once it has made a sound, a quiet moment is just a quiet moment.
    expect(new Set(run(w, zeros(WAKE_S + 1)))).toEqual(new Set(['sound']));
  });

  it('says it is silent only after WAKE_S of nothing but zeros', () => {
    const w = new Waking(rate);
    const seen = run(w, zeros(WAKE_S + 0.1));
    expect(seen.indexOf('silent')).toBeGreaterThan(Math.floor((rate * (WAKE_S - 0.01)) / 128));
    expect(WAKE_S).toBeGreaterThanOrEqual(3);
  });
});
