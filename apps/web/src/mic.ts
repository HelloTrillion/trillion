/**
 * The microphone: capture, convert to what the core expects (16 kHz mono PCM16,
 * VOI-1), and hand it over in small binary frames.
 *
 * Audio is only captured between `start` and `stop`, and it goes to the core on
 * this machine and nowhere else from here.
 *
 * Written for Safari's engine as well as Chrome's, which differ in three ways that
 * each fail silently, with a microphone that is allowed and audio that is all zeros:
 *
 * - The audio context runs at the device's own rate. Asked for 16 kHz while the
 *   microphone runs at 48, Safari hands over silence; the framer converts instead.
 * - It is made, and resumed, in the click or key press that starts listening, and
 *   kept for the next time. Made after waiting for permission, Safari leaves it
 *   paused, and a paused context hears nothing.
 * - The capture node feeds a silent output. Safari only runs nodes that lead to the
 *   speakers; this one leads there at zero volume.
 *
 * An audio interface (a Focusrite Scarlett, say) is one device with several inputs,
 * and the microphone may be on any of them; an empty input is pure digital silence.
 * So every channel is read, and the loudest is the voice. A microphone can also be
 * chosen here, apart from the computer's own default input. And a second of exact
 * silence, which no real microphone produces, is said plainly at once.
 */

export const TARGET_RATE = 16_000;
/** About 60 ms per frame: small enough to feel live, large enough not to flood the socket. */
export const FRAME_SAMPLES = 960;

export type MicProblem = 'denied' | 'no_device' | 'unsupported' | 'failed' | 'silent';

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

/** The channel carrying the voice: the one with the most energy in this block. */
export function loudest(channels: readonly Float32Array[]): Float32Array {
  let best = channels[0] ?? new Float32Array(0);
  let bestEnergy = -1;
  for (const c of channels) {
    let energy = 0;
    for (let i = 0; i < c.length; i++) energy += (c[i] ?? 0) * (c[i] ?? 0);
    if (energy > bestEnergy) {
      best = c;
      bestEnergy = energy;
    }
  }
  return best;
}

/** The id of the input named `name` now, when that is not `old`; null when there is none. */
export async function sameByName(name: string | null, old: string | null): Promise<string | null> {
  if (!name) return null;
  let found: MediaDeviceInfo[] = [];
  try {
    found = (await navigator.mediaDevices?.enumerateDevices?.()) ?? [];
  } catch {
    return null;
  }
  const match = found.find(
    (d) => d.kind === 'audioinput' && d.deviceId && d.deviceId !== 'default' && d.label.trim() === name,
  );
  return match && match.deviceId !== old ? match.deviceId : null;
}

/** What getUserMedia is asked for: the chosen microphone, or the computer's default. */
export function constraints(device: string | null): MediaStreamConstraints {
  return {
    audio: {
      // No channel count: an interface's every input arrives, and the loudest is used.
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      ...(device ? { deviceId: { exact: device } } : {}),
    },
  };
}

export function problemOf(error: unknown): MicProblem {
  const name = error instanceof Error ? error.name : '';
  if (name === 'NotAllowedError' || name === 'SecurityError') return 'denied';
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'no_device';
  return 'failed';
}

/** What the page's audio setup is doing, for diagnosis: no sound, only numbers. */
export interface MicSetup {
  readonly rate: number;
  readonly device_rate: number | null;
  readonly state: string;
  /** How many inputs the device delivers: an audio interface has several. */
  readonly channels: number;
  /** "chosen", "default", or "default, the chosen one is missing". */
  readonly device: string;
  /** The microphone track: live or ended, and whether the system muted it. */
  readonly track: string;
  /** The first second was exact zeros: the browser is handing over nothing. */
  readonly silent: boolean;
}

export interface MicHooks {
  onFrame(pcm: ArrayBuffer): void;
  /** The microphone opened, or the audio setup changed state. */
  onSetup?(setup: MicSetup): void;
  /** The microphone chosen in Settings, or null for the computer's default. */
  device?(): string | null;
  /** The chosen microphone's name, to find it when its id has changed. */
  deviceName?(): string | null;
  /** The chosen microphone was found by name under a new id: remember the id. */
  onDeviceFound?(id: string): void;
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
  let loaded: Promise<void> | null = null;
  let graph: AudioNode[] = [];
  let detach: (() => void) | null = null;
  // Each start gets a number, so a slow permission prompt answered after a stop does nothing.
  let run = 0;

  /** The audio context, made and resumed now, while the click that asked is still live. */
  function contextNow(): AudioContext {
    if (!context || context.state === 'closed') {
      context = new AudioContext();
      loaded = null;
    }
    void context.resume().catch(() => undefined);
    return context;
  }

  function stop(): void {
    run++;
    detach?.();
    detach = null;
    for (const node of graph) node.disconnect();
    graph = [];
    for (const track of stream?.getTracks() ?? []) track.stop();
    stream = null;
    // The context is kept: made again outside a click, Safari would leave it paused.
  }

  /** Exact silence for this long: no real microphone, however quiet the room. */
  const SILENT_S = 1.2;

  async function start(): Promise<void> {
    if (stream || !navigator.mediaDevices?.getUserMedia || typeof AudioWorkletNode === 'undefined') {
      if (!stream) hooks.onProblem('unsupported');
      return;
    }
    const mine = ++run;
    // Before anything is awaited: this is the moment a browser allows audio to start.
    const ctx = contextNow();
    try {
      await open(ctx, mine);
    } catch (error) {
      if (mine !== run) return;
      stop();
      hooks.onProblem(problemOf(error));
    }
  }

  async function open(ctx: AudioContext, mine: number): Promise<void> {
    const chosen = hooks.device?.() ?? null;
    let device = chosen ? 'chosen' : 'default';
    let got: MediaStream;
    try {
      got = await navigator.mediaDevices.getUserMedia(constraints(chosen));
    } catch (error) {
      // The chosen one is unplugged: the computer's default is better than nothing.
      const gone = chosen && ['NotFoundError', 'OverconstrainedError'].includes((error as Error).name);
      if (!gone) throw error;
      device = 'default, the chosen one is missing';
      got = await navigator.mediaDevices.getUserMedia(constraints(null));
      // Allowed now, so devices have names: the chosen one may be here under a new id.
      const again = await sameByName(hooks.deviceName?.() ?? null, chosen);
      if (again && mine === run) {
        try {
          const found = await navigator.mediaDevices.getUserMedia(constraints(again));
          for (const track of got.getTracks()) track.stop();
          got = found;
          device = 'chosen, found by name';
          hooks.onDeviceFound?.(again);
        } catch {
          // Keep the default.
        }
      }
    }
    if (mine !== run) {
      for (const track of got.getTracks()) track.stop();
      return;
    }
    stream = got;
    loaded ??= ctx.audioWorklet.addModule(new URL('mic-worklet.js', document.baseURI).href);
    await loaded;
    if (mine !== run) return;
    const source = ctx.createMediaStreamSource(got);
    const node = new AudioWorkletNode(ctx, 'mic-capture', { numberOfInputs: 1, numberOfOutputs: 1 });
    // To the speakers at zero volume: nothing is heard, and Safari runs the node.
    const mute = ctx.createGain();
    mute.gain.value = 0;
    const framer = new Framer(ctx.sampleRate);
    let seen = 0;
    let sound = false;
    let channels = 1;
    const report = (silent = false): void => {
      if (mine !== run) return;
      const track = got.getAudioTracks()[0];
      hooks.onSetup?.({
        rate: ctx.sampleRate,
        device_rate: track?.getSettings().sampleRate ?? null,
        state: ctx.state,
        channels,
        device,
        track: track ? `${track.readyState}${track.muted ? ', muted' : ''}` : 'none',
        silent,
      });
    };
    node.port.onmessage = (ev: MessageEvent<Float32Array[]>) => {
      if (mine !== run) return;
      channels = ev.data.length;
      const block = loudest(ev.data);
      if (!sound) {
        sound = block.some((v) => v !== 0);
        seen += block.length;
        if (!sound && seen >= ctx.sampleRate * SILENT_S) {
          report(true);
          stop();
          hooks.onProblem('silent');
          return;
        }
      }
      for (const frame of framer.push(block)) hooks.onFrame(frame);
    };
    source.connect(node);
    node.connect(mute);
    mute.connect(ctx.destination);
    graph = [source, node, mute];
    detach = hooks.attach(source);
    ctx.onstatechange = () => report();
    report();
    // Unplugged, or permission withdrawn, while she was listening.
    for (const track of got.getTracks()) {
      track.addEventListener('ended', () => {
        if (mine === run && stream === got) {
          stop();
          hooks.onProblem('no_device');
        }
      });
    }
    await ctx.resume();
  }

  return {
    start,
    stop,
    get on() {
      return stream !== null;
    },
  };
}
