import { describe, expect, it } from 'vitest';
import { BargeDetector, FRAME_S, Preroll, type TurnEnd, frameLevel, holdForReply, watchMode, watchTitle } from '../src/barge';

/** Feed frames; the frame index at which it triggered, or -1. */
function run(d: BargeDetector, frames: readonly (readonly [mic: number, her: number])[]): number {
  for (let i = 0; i < frames.length; i++) {
    const f = frames[i];
    if (f && d.push(f[0], f[1])) return i;
  }
  return -1;
}

const seconds = (s: number): number => Math.round(s / FRAME_S);
// Her voice: a level that rises and falls like speech.
const her = (i: number): number => 0.12 + 0.1 * Math.sin(i / 2);

describe('barge-in: her own voice is not the user', () => {
  it('ignores her voice leaking back, however long she talks', () => {
    const leak = Array.from({ length: seconds(10) }, (_, i) => [0.3 * her(i) + 0.003, her(i)] as const);
    expect(run(new BargeDetector(), leak)).toBe(-1);
  });

  it('ignores a strong leak heard from the very start', () => {
    // Loud speakers: half of her comes back, and the first frame already clears the minimum.
    const leak = Array.from({ length: seconds(6) }, (_, i) => [0.5 * her(i), her(i)] as const);
    expect(run(new BargeDetector(), leak)).toBe(-1);
  });

  it('ignores her loudest syllables arriving a moment late', () => {
    const d = new BargeDetector();
    const frames = Array.from({ length: seconds(8) }, (_, i) => {
      const now = i % 7 === 0 ? 0.45 : 0.08; // a sharp peak every so often
      const before = (i - 1) % 7 === 0 ? 0.45 : 0.08;
      return [0.3 * before, now] as const; // the echo lags her by one frame
    });
    expect(run(d, frames)).toBe(-1);
  });

  it('ignores a cough or a click', () => {
    const d = new BargeDetector();
    run(d, Array.from({ length: seconds(1) }, (_, i) => [0.3 * her(i), her(i)] as const));
    expect(run(d, [[0.4, 0.1], [0.4, 0.1], [0.02, 0.1], [0.02, 0.1]])).toBe(-1);
  });
});

describe('barge-in: the user talking over her', () => {
  it('is noticed within about a quarter of a second', () => {
    const d = new BargeDetector();
    run(d, Array.from({ length: seconds(2) }, (_, i) => [0.3 * her(i), her(i)] as const));
    const over = Array.from({ length: seconds(1) }, (_, i) => [0.3 * her(i) + 0.15, her(i)] as const);
    const at = run(d, over);
    expect(at).toBeGreaterThanOrEqual(3);
    expect(at * FRAME_S).toBeLessThanOrEqual(0.3);
  });

  it('is noticed in a pause between her sentences too', () => {
    const d = new BargeDetector();
    run(d, Array.from({ length: seconds(2) }, (_, i) => [0.3 * her(i), her(i)] as const));
    const at = run(d, Array.from({ length: 8 }, () => [0.1, 0] as const));
    expect(at).toBeGreaterThanOrEqual(3);
    expect(at * FRAME_S).toBeLessThanOrEqual(0.3);
  });

  it('waits out the first half second, while it learns', () => {
    const d = new BargeDetector();
    const loud = Array.from({ length: seconds(1.5) }, () => [0.2, 0.05] as const);
    expect(run(d, loud)).toBeGreaterThan(seconds(0.45));
  });

  it('starts over for each reply', () => {
    const d = new BargeDetector();
    run(d, Array.from({ length: seconds(2) }, () => [0.4, 0.8] as const)); // a loud leak learned
    d.reset();
    run(d, Array.from({ length: seconds(1) }, () => [0.003, 0.1] as const)); // headphones now
    expect(run(d, Array.from({ length: 6 }, () => [0.1, 0.1] as const))).toBe(3);
  });

  it('is noticed even when they started talking as she did', () => {
    // Warming up on the user's own voice must not make them impossible to hear.
    const d = new BargeDetector();
    expect(run(d, Array.from({ length: seconds(2) }, () => [0.2, 0.05] as const))).toBeGreaterThan(0);
  });
});

describe('the frames before it noticed', () => {
  it('keeps the last few, so the first word is not lost', () => {
    const p = new Preroll(3);
    const frames = [1, 2, 3, 4, 5].map((n) => new Int16Array([n]).buffer as ArrayBuffer);
    for (const f of frames) p.push(f);
    expect(p.drain().map((f) => new Int16Array(f)[0])).toEqual([3, 4, 5]);
    expect(p.drain()).toEqual([]);
  });

  it('measures a frame', () => {
    expect(frameLevel(new Int16Array(960).buffer)).toBe(0);
    expect(frameLevel(new Int16Array(960).fill(16384).buffer)).toBeCloseTo(0.5, 3);
    expect(frameLevel(new ArrayBuffer(0))).toBe(0);
  });
});

describe('after a spoken turn: the microphone stays open for her reply, or closes', () => {
  const spoken: TurnEnd = {
    reason: 'end',
    micOn: true,
    bargeOn: true,
    linkReady: true,
    listening: true,
    coreVoice: true,
    onboarding: false,
    cutIn: false,
  };

  it('stays open when words were heard and barge-in may watch her answer', () => {
    expect(holdForReply(spoken)).toBe(true);
  });

  it('closes when no reply is coming', () => {
    for (const reason of ['nothing', 'inaudible', 'cancelled', 'failed', undefined]) {
      expect(holdForReply({ ...spoken, reason })).toBe(false);
    }
  });

  it('closes after the longest turn, when the user is often still talking', () => {
    for (const reason of ['too_long']) {
      expect(holdForReply({ ...spoken, reason })).toBe(false);
    }
  });

  it('closes when barge-in could not watch her', () => {
    expect(holdForReply({ ...spoken, bargeOn: false })).toBe(false);
    expect(holdForReply({ ...spoken, linkReady: false })).toBe(false);
    expect(holdForReply({ ...spoken, listening: false })).toBe(false);
    // Her voice off, or the computer's own: nothing to watch, or no level to compare.
    expect(holdForReply({ ...spoken, coreVoice: false })).toBe(false);
    expect(holdForReply({ ...spoken, onboarding: true })).toBe(false);
  });

  it('closes when nothing is open, or a cut-in is still unanswered', () => {
    expect(holdForReply({ ...spoken, micOn: false })).toBe(false);
    expect(holdForReply({ ...spoken, cutIn: true })).toBe(false);
  });
});

describe('barge-in: before her reply sounds', () => {
  it('notices the user going on talking once the warm-up has learned the quiet room', () => {
    const d = new BargeDetector();
    const quiet = Array.from({ length: 8 }, () => [0.003, 0] as const);
    const talking = Array.from({ length: seconds(1) }, () => [0.06, 0] as const);
    // Warm-up (8 frames) plus a quarter of a second held.
    expect(run(d, [...quiet, ...talking])).toBe(8 + 3);
  });
});

describe('barge-in: the user talking under the bar does not deafen it', () => {
  it('keeps the bar where her echo put it while the user talks quietly over her', () => {
    const d = new BargeDetector();
    const her = 0.12;
    // Her echo, learned: 30% of her comes back.
    expect(run(d, Array.from({ length: seconds(2) }, () => [0.3 * her, her] as const))).toBe(-1);
    const bar = d.threshold(her);
    // The user talking just under the bar for five seconds.
    const under = Array.from({ length: seconds(5) }, () => [0.9 * bar, her] as const);
    expect(run(d, under)).toBe(-1);
    expect(d.threshold(her)).toBeLessThan(bar * 1.1);
    // So a little louder is them, at once.
    expect(run(d, Array.from({ length: 6 }, () => [1.2 * bar, her] as const))).toBe(3);
  });

  it('keeps the room level where it was while the user talks under the bar between her sentences', () => {
    const d = new BargeDetector();
    run(d, Array.from({ length: seconds(1) }, () => [0.003, 0] as const));
    const bar = d.threshold(0);
    expect(run(d, Array.from({ length: seconds(5) }, () => [0.9 * bar, 0] as const))).toBe(-1);
    expect(d.threshold(0)).toBeLessThan(bar * 1.1);
  });

  it('still follows her echo when it really grows, slowly', () => {
    const d = new BargeDetector();
    const her = 0.12;
    run(d, Array.from({ length: seconds(2) }, () => [0.1 * her, her] as const));
    const before = d.threshold(her);
    // The speakers turned up: much more of her comes back, for a long while.
    run(d, Array.from({ length: seconds(30) }, () => [Math.min(0.9 * d.threshold(her), 0.3 * her), her] as const));
    expect(d.threshold(her)).toBeGreaterThan(before);
  });
});

describe('the pre-roll', () => {
  it('keeps two seconds by default, so the first words are not lost', () => {
    const p = new Preroll();
    for (let i = 0; i < 100; i++) p.push(new Int16Array([i]).buffer as ArrayBuffer);
    const kept = p.drain();
    expect(kept.length * FRAME_S).toBeGreaterThanOrEqual(1.95);
    expect(kept.length * FRAME_S).toBeLessThanOrEqual(2.05);
  });
});

describe('how the microphone watches while she speaks', () => {
  it('streams to the core when the hearing judges turns, and measures here otherwise', () => {
    expect(watchMode(true)).toBe('stream');
    expect(watchMode(false)).toBe('local');
  });

  it('says what is sent', () => {
    expect(watchTitle('stream')).toBe('Listening for you to cut in.');
    expect(watchTitle('local')).toBe('Listening for you to cut in. Nothing is sent.');
  });
});
