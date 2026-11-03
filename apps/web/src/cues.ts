/**
 * Her listening cues: a short "mm-hm" in her voice while the user is talking, and a
 * nod of the orb. Not her reply: a cue has its own quiet gain, sends no `playback.*`
 * messages, never makes the orb speak and has nothing to do with turns. It plays in
 * the same AudioContext as her voice, so the browser's echo cancellation hears it.
 */

import { firstAudible, toFloat } from './voice';

const HEADER = 9; // kind (1 byte), cue id (u32), seq (u32, always 0)
const FRAME_CUE = 0x02;
// The same lead as her voice, so a cue never underruns.
const LEAD_S = 0.06;
/** A cue is softer than her voice: there, not in the way. */
export const CUE_GAIN = 0.5;
/** The orb's nod. */
const NOD = 0.3;

export interface CueFrame {
  readonly id: number;
  readonly pcm: Int16Array;
}

/** A cue's audio from the core, or null if the frame is anything else. */
export function parseCueFrame(data: ArrayBuffer): CueFrame | null {
  if (data.byteLength < HEADER || (data.byteLength - HEADER) % 2 !== 0) return null;
  const view = new DataView(data);
  if (view.getUint8(0) !== FRAME_CUE) return null;
  return { id: view.getUint32(1, true), pcm: new Int16Array(data.slice(HEADER)) };
}

/** A binary frame from the core goes to the cue player if it is a cue, else to her voice. */
export function routeFrame(data: ArrayBuffer, voice: { audio(data: ArrayBuffer): void }, cues: { audio(data: ArrayBuffer): void }): void {
  // Anything else goes to her voice, which refuses kinds it does not know.
  if (data.byteLength > 0 && new DataView(data).getUint8(0) === FRAME_CUE) cues.audio(data);
  else voice.audio(data);
}

export interface CueHooks {
  /** Her voice's AudioContext, or null when her voice is not made in the core. */
  context(): AudioContext | null;
  /** Her reply is sounding: a cue would talk over her. */
  replying(): boolean;
  flare(strength: number): void;
  /** The cue is scheduled, to be heard `inMs` from now. */
  started(id: number, inMs: number): void;
}

export interface Cues {
  /** `cue.play`: the next frame for this id is a cue at this rate. */
  announce(id: number, sampleRate: number): void;
  /** A kind-2 frame. */
  audio(data: ArrayBuffer): void;
  /** Silence any cue still sounding: her reply starts, or she is stopped. */
  stop(): void;
}

export function createCues(hooks: CueHooks): Cues {
  // Only the latest announcement: its frame follows it at once.
  let pending: { id: number; rate: number } | null = null;
  let gain: GainNode | null = null;
  let gainCtx: AudioContext | null = null;
  const sounding = new Set<AudioBufferSourceNode>();

  return {
    announce(id, sampleRate) {
      pending = { id, rate: sampleRate };
    },
    audio(data) {
      const frame = parseCueFrame(data);
      if (!frame || !pending || pending.id !== frame.id) return;
      const rate = pending.rate;
      pending = null;
      if (hooks.replying() || frame.pcm.length === 0 || !(rate > 0)) return;
      const ctx = hooks.context();
      // A sleeping context would play it later, maybe over her reply, after
      // cue.started had already said it was heard.
      if (!ctx || ctx.state !== 'running') return;
      if (!gain || gainCtx !== ctx) {
        gain = ctx.createGain();
        gain.gain.value = CUE_GAIN;
        gain.connect(ctx.destination);
        gainCtx = ctx;
      }
      const buffer = ctx.createBuffer(1, frame.pcm.length, rate);
      buffer.copyToChannel(toFloat(frame.pcm), 0);
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(gain);
      source.onended = () => sounding.delete(source);
      sounding.add(source);
      source.start(ctx.currentTime + LEAD_S);
      // Measured like her reply's first sound: the lead, any silence before the
      // sound, and the time the device itself takes to play.
      const at = Math.max(0, firstAudible(frame.pcm));
      const latency = ctx.outputLatency || ctx.baseLatency || 0;
      const inMs = Math.max(0, Math.round((LEAD_S + at / rate + latency) * 1000));
      hooks.flare(NOD);
      hooks.started(frame.id, inMs);
    },
    stop() {
      for (const source of sounding) {
        source.onended = null;
        try {
          source.stop();
        } catch {
          // not started yet
        }
      }
      sounding.clear();
    },
  };
}
