/**
 * The microphone: capture, convert to what the core expects (16 kHz mono PCM16,
 * VOI-1), and hand it over in small binary frames.
 *
 * Audio is only captured between `start` and `stop`, and it goes to the core on
 * this machine and nowhere else from here.
 */

export const TARGET_RATE = 16_000;
/** About 60 ms per frame: small enough to feel live, large enough not to flood the socket. */
export const FRAME_SAMPLES = 960;

export type MicProblem = 'denied' | 'no_device' | 'unsupported' | 'failed';

/**
 * Turns blocks of float samples at any rate into fixed-size PCM16 frames at 16 kHz.
 * Averages across each output sample's span, which is a crude low-pass: enough to
 * keep speech clean when a browser will not capture at 16 kHz itself.
 */
export class Framer {
  private readonly step: number;
  private position = 0;
  private sum = 0;
  private count = 0;
  private frame = new Int16Array(FRAME_SAMPLES);
  private filled = 0;

  constructor(inputRate: number) {
    this.step = inputRate / TARGET_RATE;
  }

  /** Feed one block. Returns the frames it completed, oldest first. */
  push(block: Float32Array): ArrayBuffer[] {
    const out: ArrayBuffer[] = [];
    for (let i = 0; i < block.length; i++) {
      this.sum += block[i] ?? 0;
      this.count++;
      this.position++;
      if (this.position < this.step) continue;
      this.position -= this.step;
      const value = Math.max(-1, Math.min(1, this.sum / this.count));
      this.sum = 0;
      this.count = 0;
      this.frame[this.filled++] = Math.round(value < 0 ? value * 32768 : value * 32767);
      if (this.filled === FRAME_SAMPLES) {
        out.push(this.frame.buffer as ArrayBuffer);
        this.frame = new Int16Array(FRAME_SAMPLES);
        this.filled = 0;
      }
    }
    return out;
  }
}

export function problemOf(error: unknown): MicProblem {
  const name = error instanceof Error ? error.name : '';
  if (name === 'NotAllowedError' || name === 'SecurityError') return 'denied';
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'no_device';
  return 'failed';
}

export interface MicHooks {
  onFrame(pcm: ArrayBuffer): void;
  /** The microphone could not be opened, or was lost while open. */
  onProblem(problem: MicProblem): void;
  /** Lets the Galaxy listen to the user's voice. Returns a function that detaches. */
  attach(node: AudioNode): () => void;
}

export interface Mic {
  start(): Promise<void>;
  stop(): void;
  readonly on: boolean;
}

export function createMic(hooks: MicHooks): Mic {
  let stream: MediaStream | null = null;
  let context: AudioContext | null = null;
  let detach: (() => void) | null = null;
  // Each start gets a number, so a slow permission prompt answered after a stop does nothing.
  let run = 0;

  function stop(): void {
    run++;
    detach?.();
    detach = null;
    for (const track of stream?.getTracks() ?? []) track.stop();
    stream = null;
    void context?.close().catch(() => undefined);
    context = null;
  }

  async function start(): Promise<void> {
    if (stream || !navigator.mediaDevices?.getUserMedia || typeof AudioWorkletNode === 'undefined') {
      if (!stream) hooks.onProblem('unsupported');
      return;
    }
    const mine = ++run;
    try {
      const got = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      if (mine !== run) {
        for (const track of got.getTracks()) track.stop();
        return;
      }
      stream = got;
      // Asking for 16 kHz lets the browser resample well; the framer covers the rest.
      let ctx: AudioContext;
      try {
        ctx = new AudioContext({ sampleRate: TARGET_RATE });
      } catch {
        ctx = new AudioContext();
      }
      context = ctx;
      await ctx.audioWorklet.addModule(new URL('mic-worklet.js', document.baseURI).href);
      if (mine !== run) return;
      const source = ctx.createMediaStreamSource(got);
      const node = new AudioWorkletNode(ctx, 'mic-capture', { numberOfInputs: 1, numberOfOutputs: 0 });
      const framer = new Framer(ctx.sampleRate);
      node.port.onmessage = (ev: MessageEvent<Float32Array>) => {
        if (mine !== run) return;
        for (const frame of framer.push(ev.data)) hooks.onFrame(frame);
      };
      source.connect(node);
      detach = hooks.attach(source);
      // Unplugged, or permission withdrawn, while she was listening.
      for (const track of got.getTracks()) {
        track.addEventListener('ended', () => {
          if (mine === run) {
            stop();
            hooks.onProblem('no_device');
          }
        });
      }
      await ctx.resume();
    } catch (error) {
      if (mine !== run) return;
      stop();
      hooks.onProblem(problemOf(error));
    }
  }

  return {
    start,
    stop,
    get on() {
      return stream !== null;
    },
  };
}
