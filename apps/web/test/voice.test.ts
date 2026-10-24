import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DUCKED, type Voice, createVoice, firstAudible, parseFrame, toFloat } from '../src/voice';

function frame(turn: number, seq: number, samples: number[], kind = 1): ArrayBuffer {
  const buf = new ArrayBuffer(9 + samples.length * 2);
  const view = new DataView(buf);
  view.setUint8(0, kind);
  view.setUint32(1, turn, true);
  view.setUint32(5, seq, true);
  samples.forEach((s, i) => view.setInt16(9 + i * 2, s, true));
  return buf;
}

describe('voice frames', () => {
  it('reads the turn, the sentence and the samples', () => {
    const parsed = parseFrame(frame(7, 2, [0, 16384, -32768]));
    expect(parsed).toMatchObject({ turn: 7, seq: 2 });
    expect(Array.from(parsed?.pcm ?? [])).toEqual([0, 16384, -32768]);
  });

  it('refuses frames that are not whole audio', () => {
    expect(parseFrame(new ArrayBuffer(3))).toBeNull();
    expect(parseFrame(frame(1, 1, [1], 9))).toBeNull(); // an unknown kind
    expect(parseFrame(frame(1, 1, [1, 2]).slice(0, 12))).toBeNull(); // half a sample
  });

  it('accepts an empty frame', () => {
    expect(parseFrame(frame(1, 1, []))?.pcm.length).toBe(0);
  });

  it('scales samples into the range a speaker takes', () => {
    expect(Array.from(toFloat(new Int16Array([0, 16384, -32768])))).toEqual([0, 0.5, -1]);
  });
});

describe('the first sound', () => {
  it('skips the silence a voice service puts before the first word', () => {
    expect(firstAudible(new Int16Array([0, 3, -40, 200, 9000, 0]))).toBe(4);
  });

  it('counts a loud negative sample as sound', () => {
    expect(firstAudible(new Int16Array([0, -5000]))).toBe(1);
  });

  it('says when a frame is silence throughout', () => {
    expect(firstAudible(new Int16Array([0, 1, -2, 100]))).toBe(-1);
    expect(firstAudible(new Int16Array([]))).toBe(-1);
  });
});

// Just enough of the Web Audio API to schedule buffers on a clock that stands still.
class FakeParam {
  value = 1;
  setTargetAtTime(target: number): void {
    this.value = target;
  }
  cancelScheduledValues(): void {}
}

class FakeContext {
  currentTime = 0;
  outputLatency = 0;
  baseLatency = 0;
  destination = {};
  createGain() {
    return { gain: new FakeParam(), connect() {} };
  }
  createAnalyser() {
    return { fftSize: 0, connect() {}, getFloatTimeDomainData() {} };
  }
  createBuffer(_channels: number, length: number, rate: number) {
    return { duration: length / rate, sampleRate: rate, copyToChannel() {} };
  }
  createBufferSource() {
    return { buffer: null, onended: null, connect() {}, start() {}, stop() {} };
  }
  resume(): Promise<void> {
    return Promise.resolve();
  }
}

describe('her voice from the core', () => {
  let heard: [number, number][];
  let gain: { gain: FakeParam } | null;
  let voice: Voice;
  const loud = Array.from({ length: 10 }, () => 9000); // 10 ms at 1 kHz

  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('AudioContext', FakeContext);
    vi.stubGlobal('window', {});
    heard = [];
    gain = null;
    voice = createVoice({
      onActive: () => {},
      onEnded: () => {},
      onAudible: () => {},
      onSentence: (turn, seq) => heard.push([turn, seq]),
      attach: (node) => {
        gain = node as unknown as { gain: FakeParam };
        return () => {};
      },
    });
    voice.configure({ kind: 'k', where: 'core', voice: null, sample_rate: 1000 });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('says when each sentence starts sounding, once per sentence', () => {
    voice.audio(frame(3, 0, loud));
    voice.audio(frame(3, 0, loud));
    voice.audio(frame(3, 1, loud));
    expect(heard).toEqual([]); // scheduled, not yet heard
    vi.advanceTimersByTime(70); // past the lead: the first sentence is sounding
    expect(heard).toEqual([[3, 0]]);
    vi.advanceTimersByTime(20);
    expect(heard).toEqual([
      [3, 0],
      [3, 1],
    ]);
  });

  it('never reports a sentence that was stopped before it sounded', () => {
    voice.audio(frame(3, 0, loud));
    voice.stop();
    vi.advanceTimersByTime(1000);
    expect(heard).toEqual([]);
  });

  it('goes quieter while the user may be talking over her, and back', () => {
    voice.audio(frame(3, 0, loud));
    voice.duck(true);
    expect(gain?.gain.value).toBe(DUCKED);
    voice.duck(false);
    expect(gain?.gain.value).toBe(1);
    voice.duck(true);
    voice.stop(); // the next reply starts at full volume
    expect(gain?.gain.value).toBe(1);
  });
});
