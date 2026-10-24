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
 * After the warm-up, a sound under the bar may be the user talking quietly over her,
 * not her echo. Learning it as echo would raise the bar against them, so from then on
 * the learned levels only creep up.
 *
 * With hearing that judges turns none of this runs: the microphone goes to the core
 * while she speaks, and her echo is told from the user by the words.
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
// After the warm-up, a level well above the room rises only this fast, and by no more
// than this share of itself at a time: about 7% in five seconds of the user talking
// under the bar, where it used to double in a few frames.
const CREEP = 0.004;
const CREEP_CAP = 1.2;

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
      this.learn(mic, playback, false);
      this.lastPlayback = playback;
      return false;
    }
    const over = mic > this.threshold(playback);
    if (over) {
      this.run++;
    } else {
      this.run = 0;
      this.learn(mic, playback, true);
    }
    this.lastPlayback = playback;
    if (this.run >= ONSET_FRAMES) {
      this.run = 0;
      return true;
    }
    return false;
  }

  private learn(mic: number, playback: number, settled: boolean): void {
    // Well above the room once the warm-up is over: perhaps the user, under the bar.
    const creep = settled && mic > this.room * OVER_ROOM;
    if (playback > 0.01) {
      // She is playing: what the microphone hears is mostly her, coming back.
      let share = Math.min(MAX_ECHO, mic / playback);
      // Rises quickly, falls slowly: a leak must never be underestimated. But never
      // quickly on what may be the user's own voice.
      let rise = 0.5;
      if (creep) {
        share = Math.min(share, this.echo * CREEP_CAP);
        rise = CREEP;
      }
      this.echo += (share - this.echo) * (share > this.echo ? rise : LEARN / 3);
    } else if (settled && mic > this.room) {
      // Rooms get louder slowly (a fan); a voice under the bar must not count as one.
      this.room += (Math.min(mic, this.room * CREEP_CAP) - this.room) * (creep ? CREEP : LEARN);
    } else {
      this.room += (mic - this.room) * LEARN;
    }
  }
}

/** What the page knows when the core closes listening (`mic.stop`). */
export interface TurnEnd {
  /** Why listening ended, as `mic.stop` says. */
  readonly reason: unknown;
  readonly micOn: boolean;
  readonly bargeOn: boolean;
  readonly linkReady: boolean;
  readonly listening: boolean;
  /** Her voice is played on this page (not off, not the computer's own voice). */
  readonly coreVoice: boolean;
  readonly onboarding: boolean;
  /** The user has cut in and the core has not answered it yet. */
  readonly cutIn: boolean;
}

/** After a spoken turn, the microphone stays open and watches for the user while she
 * answers, rather than closing now and opening again at her first sound: on a Mac,
 * opening or closing an echo-cancelled microphone reconfigures the audio device and
 * glitches her first words, and the gap in between would hear nobody. Only when a
 * reply is coming (the user finished) and barge-in may watch it. */
export function holdForReply(t: TurnEnd): boolean {
  return (
    // Not after the longest turn: the user is often still talking, and the warm-up
    // would learn their voice as the room.
    t.reason === 'end' &&
    t.micOn &&
    t.bargeOn &&
    t.linkReady &&
    t.listening &&
    t.coreVoice &&
    !t.onboarding &&
    !t.cutIn
  );
}

/** How the open microphone watches while she speaks. With hearing that judges turns it
 * streams to the core, which tells her echo from the user by the words; otherwise the
 * levels are measured here and nothing is sent until the user cuts in. */
export type WatchMode = 'stream' | 'local';

export function watchMode(turns: boolean): WatchMode {
  return turns ? 'stream' : 'local';
}

/** The Speak button's title while it only watches (VOI-15d): open, and what is sent. */
export function watchTitle(mode: WatchMode): string {
  return mode === 'stream' ? 'Listening for you to cut in.' : 'Listening for you to cut in. Nothing is sent.';
}

/** What a streaming watch does when the core says `overlap.over`: `close` when the
 * watch is over (`end`); `stop` when the page asked to stop while the core decided, or
 * can no longer watch (the switch off, its own microphone turn); else `keep` streaming. */
export function afterOver(o: { end: boolean; pending: boolean; canWatch: boolean }): 'close' | 'stop' | 'keep' {
  if (o.end) return 'close';
  return o.pending || !o.canWatch ? 'stop' : 'keep';
}

/** Held for her reply this long without a sound from her, the microphone closes: a
 * reply that turns out silent, a long tool, an error. */
export const HOLD_MS = 15_000;

// Two seconds: the detector notices a voice late, and more so a quiet one.
const PREROLL_FRAMES = Math.round(2 / FRAME_S);

/** The last few frames before the user was noticed, so their first word is kept. */
export class Preroll {
  private frames: ArrayBuffer[] = [];

  constructor(private readonly keep = PREROLL_FRAMES) {}

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
