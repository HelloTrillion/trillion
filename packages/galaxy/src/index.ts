import { PerspectiveCamera, Scene, WebGLRenderer } from 'three';
import { AnalyserBank } from './audio/analysers';
import {
  type AnalyserRole, type Levels, SILENT, smooth, syntheticListening, syntheticSpeech,
} from './audio/levels';
import { approach, stepParams } from './easing';
import { Landscape } from './landscape/landscape';
import { Orb } from './orb/orb';
import { type PerfMode, PerfGovernor, motionScale, qualityFor } from './perf';
import { ORB_STATES, type OrbParams, type OrbStateName } from './states';

export { ORB_STATE_NAMES, ORB_STATES, STATE_LABELS } from './states';
export type { OrbParams, OrbStateName } from './states';
export type { AnalyserRole, Levels } from './audio/levels';
export type { PerfMode } from './perf';

export type Theme = 'dark' | 'light';

export interface GalaxyOptions {
  readonly theme?: Theme;
  readonly perfMode?: PerfMode;
  /** Seed for dust layout; fix it for reproducible snapshots. */
  readonly seed?: number;
  /** Override the prefers-reduced-motion media query. */
  readonly reducedMotion?: boolean;
  /** Called whenever the state changes, for a live region (SUR-6). */
  readonly onStateChange?: (state: OrbStateName) => void;
  /** Called when perf mode switches on or off. */
  readonly onPerfChange?: (active: boolean) => void;
}

/** Reserved for the constellation (GAL-7, Phase 1). */
export interface SatelliteSpec {
  readonly id: string;
  readonly accent: string;
}

export interface Galaxy {
  setState(state: OrbStateName): void;
  getState(): OrbStateName;
  /** Tap an audio node for reactivity. Returns a function that detaches it. */
  attachAnalyser(node: AudioNode, role: AnalyserRole): () => void;
  /** A brief surge of light, for acknowledgements and small wins. */
  flare(strength?: number): void;
  /** GAL-7, Phase 1: no-op until the constellation lands. */
  addSatellite(spec: SatelliteSpec): void;
  /** GAL-7, Phase 1: no-op until the constellation lands. */
  removeSatellite(id: string): void;
  /** GAL-8, Phase 1: no-op until energy arcs land. */
  pulseArc(targetId: string): void;
  setPerfMode(mode: PerfMode): void;
  setTheme(theme: Theme): void;
  destroy(): void;
}

const MAX_DT = 0.1;

export function createGalaxy(canvas: HTMLCanvasElement, opts: GalaxyOptions = {}): Galaxy {
  const renderer = new WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
  const scene = new Scene();
  const camera = new PerspectiveCamera(45, 1, 0.1, 200);
  camera.position.set(0, 0, 4.2);

  const governor = new PerfGovernor(opts.perfMode ?? 'auto');
  const media = typeof matchMedia === 'function'
    ? matchMedia('(prefers-reduced-motion: reduce)')
    : null;
  let reduced = opts.reducedMotion ?? media?.matches ?? false;
  const onMotionChange = (e: MediaQueryListEvent): void => {
    if (opts.reducedMotion === undefined) reduced = e.matches;
  };
  media?.addEventListener('change', onMotionChange);

  let perfActive = governor.sample(60, 0);
  let quality = qualityFor(perfActive, devicePixelRatio);
  const orb = new Orb(quality.orbDetail);
  const landscape = new Landscape(quality.dustCount, opts.seed ?? 1);
  scene.add(landscape.group, orb.group);
  renderer.setPixelRatio(quality.pixelRatio);

  const bank = new AnalyserBank();
  let state: OrbStateName = 'idle';
  let params: OrbParams = ORB_STATES.idle;
  let levels: Levels = SILENT;
  let flareLevel = 0;
  let theme: Theme = opts.theme ?? 'dark';
  let light = theme === 'light' ? 1 : 0;
  let fps = 60;
  let last = performance.now();
  let clock = 0;
  let raf = 0;

  const resize = (): void => {
    const w = canvas.clientWidth || 1;
    const h = canvas.clientHeight || 1;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };
  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  resize();

  const applyQuality = (): void => {
    const next = qualityFor(perfActive, devicePixelRatio);
    if (next.orbDetail !== quality.orbDetail) orb.setDetail(next.orbDetail);
    if (next.dustCount !== quality.dustCount) landscape.setDustCount(next.dustCount);
    if (next.pixelRatio !== quality.pixelRatio) renderer.setPixelRatio(next.pixelRatio);
    quality = next;
    opts.onPerfChange?.(perfActive);
  };

  const targetLevels = (): Levels => {
    if (state === 'speaking') {
      return bank.has('playback') ? bank.read('playback') : syntheticSpeech(clock);
    }
    if (state === 'listening') {
      return bank.has('mic') ? bank.read('mic') : syntheticListening(clock);
    }
    return SILENT;
  };

  const frame = (now: number): void => {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(MAX_DT, Math.max(0, (now - last) / 1000));
    last = now;
    clock += dt;
    if (dt > 0) fps += (1 / dt - fps) * 0.1;

    const wasActive = perfActive;
    perfActive = governor.sample(fps, now);
    if (perfActive !== wasActive) applyQuality();

    const motion = motionScale(reduced);
    params = stepParams(params, ORB_STATES[state], dt);
    levels = smooth(levels, targetLevels());
    flareLevel = approach(flareLevel, 0, 0.05, dt);
    light = approach(light, theme === 'light' ? 1 : 0, 0.08, dt);

    orb.update({ params, levels, time: clock, dt, flare: flareLevel, motion });
    landscape.update({ time: clock, dt, motion, light, pixelRatio: quality.pixelRatio });
    renderer.render(scene, camera);
  };
  raf = requestAnimationFrame(frame);

  return {
    setState(next) {
      if (next === state) return;
      state = next;
      opts.onStateChange?.(next);
    },
    getState: () => state,
    attachAnalyser: (node, role) => bank.attach(node, role),
    flare(strength = 1) {
      flareLevel = Math.min(1.5, flareLevel + Math.max(0, strength));
    },
    addSatellite() {},
    removeSatellite() {},
    pulseArc() {},
    setPerfMode(mode) {
      governor.setMode(mode);
      const was = perfActive;
      perfActive = governor.sample(fps, performance.now());
      if (perfActive !== was) applyQuality();
    },
    setTheme(next) {
      theme = next;
    },
    destroy() {
      cancelAnimationFrame(raf);
      observer.disconnect();
      media?.removeEventListener('change', onMotionChange);
      bank.detachAll();
      orb.dispose();
      landscape.dispose();
      renderer.dispose();
    },
  };
}
