/**
 * Voice barge-in (VOI-9): telling the user talking over her from her own voice
 * coming back through the microphone.
 *
 * The browser's echo cancellation removes most of her voice from the microphone,
 * and not always all of it. So, while she speaks, this learns how much of her
 * reaches the microphone (the echo, as a share of what she is playing) and the
 * room's own level, and counts as the user only a sound well above both, held
 * for a quarter of a second: a word, not a cough, a click or her loudest syllable.
 *
 * Pure: levels in, a yes or no out. The page measures the levels.
 */

/** One microphone frame is 960 samples at 16 kHz: 60 ms. */
export const FRAME_S = 0.06;
// Held this long, a sound is someone speaking.
const ONSET_FRAMES = 4; // 240 ms
// Nothing is decided in the first half second: the echo is still being learned.
const WARMUP_FRAMES = 8;
// Louder than the learned echo and the room by this much: a person, not a leak.
const OVER_ECHO = 2;
const OVER_ROOM = 4;
// The most of her that can plausibly come back after echo cancellation. Learning
// more than this would mean learning the user's own voice as her echo, and then the
// user could not interrupt that reply at all.
const MAX_ECHO = 0.6;
// Never below this, however quiet the room: a level a voice at the desk clears.
const MIN_VOICE = 0.02;
// How quickly the learned levels follow the sound (per frame).
const LEARN = 0.15;

/** The loudness of one frame of PCM16, 0 to 1 (root mean square). */
export function frameLevel(pcm: ArrayBuffer): number {
  const samples = new Int16Array(pcm);
  if (!samples.length) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += (samples[i] ?? 0) ** 2;
  return Math.sqrt(sum / samples.length) / 32768;
}

export class BargeDetector {
  private frames = 0;
  private run = 0;
  private echo = 0; // the microphone's level as a share of hers
  private room = 0.002;
  private lastPlayback = 0;

  /** Start over: a new reply, or a new microphone. */
  reset(): void {
    this.frames = 0;
    this.run = 0;
    this.echo = 0;
    this.room = 0.002;
    this.lastPlayback = 0;
  }

  /** The level the microphone must clear right now to count as the user. */
  threshold(playback: number): number {
    // Her level a moment ago counts too: the echo arrives a little after she plays it.
    const her = Math.max(playback, this.lastPlayback);
    return Math.max(MIN_VOICE, this.room * OVER_ROOM, this.echo * her * OVER_ECHO);
  }

  /** One frame: the microphone's level and hers, both 0 to 1. True once, when the
   * user has been talking over her long enough to count. */
  push(mic: number, playback: number): boolean {
    this.frames++;
    if (this.frames <= WARMUP_FRAMES) {
      // Everything heard while warming up is learned, however loud: a strong leak
      // must raise the bar, not slip under it.
      this.learn(mic, playback);
      this.lastPlayback = playback;
      return false;
    }
    const over = mic > this.threshold(playback);
    if (over) {
      this.run++;
    } else {
      this.run = 0;
      this.learn(mic, playback);
    }
    this.lastPlayback = playback;
    if (this.run >= ONSET_FRAMES) {
      this.run = 0;
      return true;
    }
    return false;
  }

  private learn(mic: number, playback: number): void {
    if (playback > 0.01) {
      // She is playing: what the microphone hears is mostly her, coming back.
      const share = Math.min(MAX_ECHO, mic / playback);
      // Rises quickly, falls slowly: a leak must never be underestimated.
      this.echo += (share - this.echo) * (share > this.echo ? 0.5 : LEARN / 3);
    } else {
      this.room += (mic - this.room) * LEARN;
    }
  }
}

/** The last few frames before the user was noticed, so their first word is kept. */
export class Preroll {
  private frames: ArrayBuffer[] = [];

  constructor(private readonly keep = 10) {} // 600 ms

  push(frame: ArrayBuffer): void {
    this.frames.push(frame);
    if (this.frames.length > this.keep) this.frames.shift();
  }

  /** Hand over what is held, and hold nothing. */
  drain(): ArrayBuffer[] {
    const out = this.frames;
    this.frames = [];
    return out;
  }
}
