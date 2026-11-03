import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CUE_GAIN, type Cues, createCues, parseCueFrame, routeFrame } from '../src/cues';

function frame(kind: number, id: number, samples: number[]): ArrayBuffer {
  const buf = new ArrayBuffer(9 + samples.length * 2);
  const view = new DataView(buf);
  view.setUint8(0, kind);
  view.setUint32(1, id, true);
  view.setUint32(5, 0, true);
  samples.forEach((s, i) => view.setInt16(9 + i * 2, s, true));
  return buf;
}

// Just enough of the Web Audio API to see what is connected to what, on a clock that stands still.
interface Node {
  kind: string;
  to: unknown[];
  gain?: { value: number };
  started?: number | undefined;
  stopped?: boolean;
  onended?: (() => void) | null;
}

class FakeContext {
  state: AudioContextState = 'running';
  currentTime = 1;
  outputLatency = 0.04;
  baseLatency = 0;
  destination = { kind: 'destination' };
  nodes: Node[] = [];
  createGain() {
    const node = { kind: 'gain', to: [] as unknown[], gain: { value: 1 }, connect: (n: unknown) => node.to.push(n) };
    this.nodes.push(node);
    return node;
  }
  createBuffer(_channels: number, length: number, rate: number) {
    return { duration: length / rate, sampleRate: rate, copyToChannel() {} };
  }
  createBufferSource() {
    const node = {
      kind: 'source',
      to: [] as unknown[],
      buffer: null,
      started: undefined as number | undefined,
      stopped: false,
      onended: null as (() => void) | null,
      connect: (n: unknown) => node.to.push(n),
      stop: () => {
        node.stopped = true;
      },
      start: (at: number) => {
        node.started = at;
      },
    };
    this.nodes.push(node);
    return node;
  }
}

const loud = [0, 0, 9000, 9000, 9000, 9000, 9000, 9000, 9000, 9000]; // 10 ms at 1 kHz, sound from 2 ms

describe('cue frames', () => {
  it('reads the id and the samples of a kind-2 frame', () => {
    const parsed = parseCueFrame(frame(2, 5, [1, -2]));
    expect(parsed?.id).toBe(5);
    expect(Array.from(parsed?.pcm ?? [])).toEqual([1, -2]);
  });

  it('is not her reply, and her reply is not a cue', () => {
    expect(parseCueFrame(frame(1, 5, [1]))).toBeNull();
    expect(parseCueFrame(new ArrayBuffer(4))).toBeNull();
    expect(parseCueFrame(frame(2, 5, [1, 2]).slice(0, 12))).toBeNull(); // half a sample
  });
});

describe('where a binary frame goes', () => {
  it('sends a cue to the cue player and her reply to her voice', () => {
    const voice = { audio: vi.fn() };
    const cues = { audio: vi.fn() };
    const cue = frame(2, 1, loud);
    const reply = frame(1, 1, loud);
    routeFrame(cue, voice, cues);
    routeFrame(reply, voice, cues);
    expect(cues.audio).toHaveBeenCalledExactlyOnceWith(cue);
    expect(voice.audio).toHaveBeenCalledExactlyOnceWith(reply);
  });
});

describe('the cue player', () => {
  let ctx: FakeContext;
  let replying: boolean;
  let flares: number[];
  let started: [number, number][];
  let cues: Cues;

  beforeEach(() => {
    ctx = new FakeContext();
    replying = false;
    flares = [];
    started = [];
    cues = createCues({
      context: () => ctx as unknown as AudioContext,
      replying: () => replying,
      flare: (s) => flares.push(s),
      started: (id, inMs) => started.push([id, inMs]),
    });
  });

  const sources = (): Node[] => ctx.nodes.filter((n) => n.kind === 'source');
  const gains = (): Node[] => ctx.nodes.filter((n) => n.kind === 'gain');

  it('plays softly through its own gain, straight to the speaker', () => {
    cues.announce(4, 1000);
    cues.audio(frame(2, 4, loud));
    expect(gains()).toHaveLength(1);
    const gain = gains()[0];
    expect(gain?.gain?.value).toBe(CUE_GAIN);
    expect(gain?.to).toEqual([ctx.destination]); // no analyser: the orb does not "speak" a cue
    expect(sources()).toHaveLength(1);
    expect(sources()[0]?.to).toEqual([gain]);
  });

  it('nods the orb and says when the cue will be heard', () => {
    cues.announce(4, 1000);
    cues.audio(frame(2, 4, loud));
    expect(flares).toEqual([0.3]);
    // 60 ms lead, 2 ms of silence before the sound, 40 ms for the device.
    expect(started).toEqual([[4, 102]]);
  });

  it('reuses its gain for the next cue', () => {
    cues.announce(1, 1000);
    cues.audio(frame(2, 1, loud));
    cues.announce(2, 1000);
    cues.audio(frame(2, 2, loud));
    expect(gains()).toHaveLength(1);
    expect(started.map(([id]) => id)).toEqual([1, 2]);
  });

  it('drops a cue that arrives while her reply plays', () => {
    replying = true;
    cues.announce(4, 1000);
    cues.audio(frame(2, 4, loud));
    expect(sources()).toHaveLength(0);
    expect(flares).toEqual([]);
    expect(started).toEqual([]);
  });

  it('drops audio whose cue was never announced', () => {
    cues.audio(frame(2, 9, loud));
    cues.announce(4, 1000);
    cues.audio(frame(2, 5, loud));
    expect(sources()).toHaveLength(0);
    expect(started).toEqual([]);
  });

  it('plays a cue once', () => {
    cues.announce(4, 1000);
    cues.audio(frame(2, 4, loud));
    cues.audio(frame(2, 4, loud));
    expect(started).toHaveLength(1);
  });

  it('drops a cue while the speaker is asleep, rather than play it late', () => {
    ctx.state = 'suspended';
    cues.announce(4, 1000);
    cues.audio(frame(2, 4, loud));
    expect(sources()).toHaveLength(0);
    expect(flares).toEqual([]);
    expect(started).toEqual([]);
  });

  it('stops a sounding cue when told to, and forgets finished ones', () => {
    cues.announce(1, 1000);
    cues.audio(frame(2, 1, loud));
    cues.announce(2, 1000);
    cues.audio(frame(2, 2, loud));
    sources()[0]?.onended?.(); // the first has finished
    cues.stop();
    expect(sources().map((s) => s.stopped)).toEqual([false, true]);
    cues.stop(); // nothing left to stop
  });

  it('plays nothing when her voice is not made here', () => {
    const quiet = createCues({ context: () => null, replying: () => false, flare: (s) => flares.push(s), started: (id, ms) => started.push([id, ms]) });
    quiet.announce(4, 1000);
    quiet.audio(frame(2, 4, loud));
    expect(flares).toEqual([]);
    expect(started).toEqual([]);
  });
});
