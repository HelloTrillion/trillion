import { type AnalyserRole, type Levels, SILENT, applyRole, bandLevels } from './levels';

interface Attached {
  readonly analyser: AnalyserNode;
  readonly data: Uint8Array<ArrayBuffer>;
  readonly source: AudioNode;
}

/** Holds at most one analyser per role; reading is allocation-free per frame. */
export class AnalyserBank {
  private readonly attached = new Map<AnalyserRole, Attached>();

  attach(source: AudioNode, role: AnalyserRole): () => void {
    this.detach(role);
    const analyser = source.context.createAnalyser();
    analyser.fftSize = 256;
    analyser.smoothingTimeConstant = 0.6;
    // Tap only: the analyser is a leaf, so it never changes what is heard.
    source.connect(analyser);
    const entry: Attached = {
      analyser,
      data: new Uint8Array(analyser.frequencyBinCount),
      source,
    };
    this.attached.set(role, entry);
    return () => {
      if (this.attached.get(role) === entry) this.detach(role);
    };
  }

  has(role: AnalyserRole): boolean {
    return this.attached.has(role);
  }

  read(role: AnalyserRole): Levels {
    const entry = this.attached.get(role);
    if (!entry) return SILENT;
    entry.analyser.getByteFrequencyData(entry.data);
    return applyRole(bandLevels(entry.data), role);
  }

  detach(role: AnalyserRole): void {
    const entry = this.attached.get(role);
    if (!entry) return;
    try {
      entry.source.disconnect(entry.analyser);
    } catch {
      // Already disconnected by its owner; nothing to undo.
    }
    this.attached.delete(role);
  }

  detachAll(): void {
    this.detach('mic');
    this.detach('playback');
  }
}
