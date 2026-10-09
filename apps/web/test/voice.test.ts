import { describe, expect, it } from 'vitest';
import { parseFrame, toFloat } from '../src/voice';

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
