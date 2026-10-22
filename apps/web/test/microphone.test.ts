import { describe, expect, it } from 'vitest';
import { constraints, loudest } from '../src/mic';
import { deviceName, meterLevel } from '../src/microphone';

const tone = (n: number, loud: number): Float32Array =>
  Float32Array.from({ length: n }, (_, i) => loud * Math.sin(i / 3));

describe('an audio interface with several inputs', () => {
  it('uses the input the voice is on, whichever it is', () => {
    const voice = tone(128, 0.3);
    const empty = new Float32Array(128); // an unused input: pure digital silence
    expect(loudest([empty, voice])).toBe(voice); // the mic on input 2
    expect(loudest([voice, empty])).toBe(voice); // or on input 1
  });

  it('takes a single channel as it is, and survives none', () => {
    const only = tone(128, 0.1);
    expect(loudest([only])).toBe(only);
    expect(loudest([]).length).toBe(0);
  });
});

describe('which microphone is asked for', () => {
  it('asks for the chosen one exactly, or the default', () => {
    const chosen = constraints('abc123').audio as MediaTrackConstraints;
    expect(chosen.deviceId).toEqual({ exact: 'abc123' });
    const fallback = constraints(null).audio as MediaTrackConstraints;
    expect(fallback.deviceId).toBeUndefined();
  });

  it('never narrows a device to one channel, which is how an interface went silent', () => {
    const audio = constraints(null).audio as MediaTrackConstraints;
    expect(audio.channelCount).toBeUndefined();
  });
});

describe('the microphone settings', () => {
  it('names a device, or numbers it until names are allowed', () => {
    expect(deviceName({ label: 'MacBook Pro Microphone' }, 0)).toBe('MacBook Pro Microphone');
    expect(deviceName({ label: '' }, 1)).toBe('Microphone 2');
  });

  it('shows silence as nothing and a normal voice as most of the meter', () => {
    expect(meterLevel(new Float32Array(512))).toBe(0);
    expect(meterLevel(tone(512, 0.15))).toBeGreaterThan(0.6);
    expect(meterLevel(tone(512, 1))).toBe(1);
  });
});
