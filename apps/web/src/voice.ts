/**
 * Her voice in the page: either audio streamed from the core, or the computer's
 * own voice speaking each sentence here. Either way the rest of the page sees one
 * thing: a voice that is or is not speaking.
 */

export interface SpeechConfig {
  readonly kind: string;
  readonly where: 'core' | 'client';
  readonly voice: string | null;
  readonly sample_rate: number | null;
}

export interface Frame {
  readonly turn: number;
  readonly seq: number;
  readonly pcm: Int16Array;
}

const HEADER = 9; // kind (1 byte), turn (u32), sentence (u32)
const FRAME_AUDIO = 0x01;
// A little lead before the first sound, so the first buffers never underrun.
const LEAD_S = 0.06;

/** A binary frame from the core, or null if it is not audio we understand. */
export function parseFrame(data: ArrayBuffer): Frame | null {
  if (data.byteLength < HEADER || (data.byteLength - HEADER) % 2 !== 0) return null;
  const view = new DataView(data);
  if (view.getUint8(0) !== FRAME_AUDIO) return null;
  // Copied, because samples must start on an even byte and the header is nine long.
  return { turn: view.getUint32(1, true), seq: view.getUint32(5, true), pcm: new Int16Array(data.slice(HEADER)) };
}

export function toFloat(pcm: Int16Array): Float32Array<ArrayBuffer> {
  const out = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) out[i] = (pcm[i] ?? 0) / 32768;
  return out;
}

export interface VoiceHooks {
  /** She started or stopped making sound. */
  onActive(active: boolean): void;
  /** Everything for a turn has been heard, after `played_ms` of sound. */
  onEnded(turn: number, playedMs: number): void;
  /** Lets the Galaxy listen to her voice. Returns a function that detaches. */
  attach(node: AudioNode): () => void;
}

export interface Voice {
  configure(config: SpeechConfig | null): void;
  /** Call from a click or key press: browsers only allow sound after one. */
  unlock(): void;
  sentence(turn: number, text: string): void;
  audio(data: ArrayBuffer): void;
  /** The core has sent everything for this turn. */
  end(turn: number): void;
  /** Silence now, and drop whatever is queued. */
  stop(): void;
  readonly active: boolean;
}

export function createVoice(hooks: VoiceHooks): Voice {
  let config: SpeechConfig | null = null;
  let ctx: AudioContext | null = null;
  let gain: GainNode | null = null;
  let detach: (() => void) | null = null;
  let nextAt = 0;
  let sounding = 0; // buffers or utterances not yet finished
  let active = false;
  let turn = -1;
  let ended = false; // the core said this turn is complete
  let playedMs = 0;
  let stopped = -1; // a turn whose late audio must be ignored
  const sources = new Set<AudioBufferSourceNode>();

  const settle = (): void => {
    const now = sounding > 0;
    if (now !== active) {
      active = now;
      hooks.onActive(now);
    }
    if (!now && ended && turn >= 0) {
      const finished = turn;
      ended = false;
      turn = -1;
      hooks.onEnded(finished, Math.round(playedMs));
    }
  };

  const begin = (t: number): boolean => {
    if (t === stopped) return false;
    if (t !== turn) {
      turn = t;
      ended = false;
      playedMs = 0;
    }
    return true;
  };

  const context = (): AudioContext => {
    if (!ctx) {
      ctx = new AudioContext();
      gain = ctx.createGain();
      gain.connect(ctx.destination);
      detach = hooks.attach(gain);
    }
    return ctx;
  };

  return {
    get active() {
      return active;
    },
    configure(next) {
      if (next?.kind !== config?.kind || next?.voice !== config?.voice) this.stop();
      config = next;
    },
    unlock() {
      if (config?.where === 'core') void context().resume();
    },
    sentence(t, text) {
      if (config?.where !== 'client' || !('speechSynthesis' in window) || !begin(t)) return;
      const utterance = new SpeechSynthesisUtterance(text);
      const chosen = speechSynthesis.getVoices().find((v) => v.voiceURI === config?.voice);
      if (chosen) utterance.voice = chosen;
      const started = performance.now();
      let done = false;
      const finish = (): void => {
        if (done) return;
        done = true;
        playedMs += performance.now() - started;
        sounding--;
        settle();
      };
      utterance.onend = finish;
      utterance.onerror = finish;
      sounding++;
      settle();
      speechSynthesis.speak(utterance);
    },
    audio(data) {
      const frame = parseFrame(data);
      if (!frame || config?.where !== 'core' || !config.sample_rate || !begin(frame.turn)) return;
      const audio = context();
      if (!gain || frame.pcm.length === 0) return;
      const buffer = audio.createBuffer(1, frame.pcm.length, config.sample_rate);
      buffer.copyToChannel(toFloat(frame.pcm), 0);
      const source = audio.createBufferSource();
      source.buffer = buffer;
      source.connect(gain);
      // Each buffer starts exactly where the last one ends: no gap, no overlap.
      nextAt = Math.max(nextAt, audio.currentTime + LEAD_S);
      source.start(nextAt);
      nextAt += buffer.duration;
      sources.add(source);
      sounding++;
      source.onended = () => {
        if (!sources.delete(source)) return; // already stopped
        playedMs += buffer.duration * 1000;
        sounding--;
        settle();
      };
      settle();
    },
    end(t) {
      if (t !== turn) {
        // Nothing was spoken this turn (all code, or muted): it is already over.
        if (t !== stopped) hooks.onEnded(t, 0);
        return;
      }
      ended = true;
      settle();
    },
    stop() {
      stopped = turn;
      for (const source of sources) {
        source.onended = null;
        try {
          source.stop();
        } catch {
          // not started yet
        }
      }
      sources.clear();
      if ('speechSynthesis' in window) speechSynthesis.cancel();
      nextAt = 0;
      sounding = 0;
      ended = false;
      turn = -1;
      settle();
      void detach; // the analyser stays attached for the next reply
    },
  };
}
