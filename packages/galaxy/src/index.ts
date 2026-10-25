import { PerspectiveCamera, Scene, WebGLRenderer } from 'three';
import { AnalyserBank } from './audio/analysers';
import {
  type AnalyserRole, type Levels, SILENT, smooth, syntheticListening, syntheticSpeech,
} from './audio/levels';
import { approach, stepParams } from './easing';
import { Landscape } from './landscape/landscape';
import { Orb } from './orb/orb';
import { type PerfMode, PerfGovernor, motionScale, qualityFor } from './perf';
import { ORB_DISTANCE, type StageRect, easeRect, placeOrb } from './stage';
import { ORB_STATES, displayedState, type OrbParams, type OrbStateName } from './states';

export { ORB_STATE_NAMES, ORB_STATES, STATE_LABELS, displayedState } from './states';
export type { OrbParams, OrbStateName } from './states';
export type { AnalyserRole, Levels } from './audio/levels';
export type { PerfMode } from './perf';
export type { StageRect } from './stage';

export type Theme = 'dark' | 'light';

export interface GalaxyOptions {
  readonly theme?: Theme;
  readonly perfMode?: PerfMode;
  /** Seed for dust layout; fix it for reproducible snapshots. */
  readonly seed?: number;
  /** Override the prefers-reduced-motion media query. */
  readonly reducedMotion?: boolean;
  /**
   * Called whenever the state the orb *shows* changes, for a live region (SUR-6).
   * That is `arming`, not `listening`, until a microphone is attached.
   */
  readonly onStateChange?: (state: OrbStateName) => void;
  /**
   * Let `listening` pulse gold with no microphone attached. For demos and
   * screenshots only: gold means recording, and without this it only ever does.
   */
  readonly demo?: boolean;
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
  /** Place the orb: centre and diameter in CSS pixels. Eases there unless reduced
   * motion is on or `animate` is false. Without a call it stays centred. */
  setStage(rect: StageRect, opts?: { animate?: boolean }): void;
  destroy(): void;
}

const MAX_DT = 0.1;

export function createGalaxy(canvas: HTMLCanvasElement, opts: GalaxyOptions = {}): Galaxy {
  const renderer = new WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
  renderer.autoClear = false;
  const orbScene = new Scene();
  const orbCamera = new PerspectiveCamera(45, 1, 0.1, 100);
  orbCamera.position.set(0, 0, ORB_DISTANCE);

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
  orbScene.add(orb.group);
  const landscape = new Landscape(quality.dustCount, quality.nodeCount, opts.seed ?? 1);
  renderer.setPixelRatio(quality.pixelRatio);

  const bank = new AnalyserBank();
  let state: OrbStateName = 'idle';
  let params: OrbParams = ORB_STATES.idle;
  let levels: Levels = SILENT;
  let flareLevel = 0;
  let theme: Theme = opts.theme ?? 'dark';
  landscape.setTheme(theme === 'light');
  orb.setTheme(theme === 'light');
  let light = theme === 'light' ? 1 : 0;
  let fps = 60;
  let last = performance.now();
  let clock = 0;
  // Animation time advances at the motion scale, so toggling reduced motion
  // slows everything down without jumping every phase.
  let animClock = 0;
  let raf = 0;
  let destroyed = false;
  // Null until the host calls setStage: the orb stays centred as before.
  let stageTarget: StageRect | null = null;
  let stage: StageRect | null = null;
  let stageJump = false;
  let stageDirty = false;

  const resize = (): void => {
    const w = canvas.clientWidth || 1;
    const h = canvas.clientHeight || 1;
    renderer.setSize(w, h, false);
    orbCamera.aspect = w / h;
    // Keep the orb the same share of the short side on portrait screens.
    orbCamera.zoom = Math.min(1, w / h);
    orbCamera.updateProjectionMatrix();
    // Once placed, placeOrb owns the orb camera's zoom and lens shift.
    if (stage) placeOrb(orbCamera, stage, w, h);
    stageDirty = true;
    landscape.setAspect(w / h);
  };
  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  resize();

  const applyQuality = (): void => {
    const next = qualityFor(perfActive, devicePixelRatio);
    if (next.orbDetail !== quality.orbDetail) orb.setDetail(next.orbDetail);
    if (next.dustCount !== quality.dustCount || next.nodeCount !== quality.nodeCount) {
      landscape.setCounts(next.dustCount, next.nodeCount);
    }
    if (next.pixelRatio !== quality.pixelRatio) renderer.setPixelRatio(next.pixelRatio);
    quality = next;
    opts.onPerfChange?.(perfActive);
  };
  if (perfActive) opts.onPerfChange?.(true);

  // What the orb shows can differ from what the host asked for: `listening` with no
  // microphone attached shows `arming`, so gold never lights without a recording.
  const shown = (): OrbStateName => displayedState(state, bank.has('mic'), opts.demo === true);
  let announced: OrbStateName = 'idle';
  const announce = (): void => {
    const s = shown();
    if (s === announced) return;
    announced = s;
    opts.onStateChange?.(s);
  };

  const targetLevels = (s: OrbStateName): Levels => {
    if (s === 'speaking') {
      return bank.has('playback') ? bank.read('playback') : syntheticSpeech(clock);
    }
    if (s === 'listening') {
      // Without a mic this is reachable only in demo mode (displayedState).
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
    animClock += dt * motion;
    const showing = shown();
    params = stepParams(params, ORB_STATES[showing], dt);
    levels = smooth(levels, targetLevels(showing));
    flareLevel = approach(flareLevel, 0, 0.05, dt);
    light = approach(light, theme === 'light' ? 1 : 0, 0.08, dt);
    // The network answers her voice (and her thinking), not the user's mic.
    const voiced = showing === 'speaking' || showing === 'processing';

    orb.update({ params, levels, time: animClock, dt, flare: flareLevel, motion });
    landscape.update({
      time: animClock, dt, motion, light,
      voice: voiced ? levels.level : 0,
      bass: voiced ? levels.bass : 0,
      orbColor: params.color,
      pointScale: renderer.domElement.height / 2,
    });

    if (stageTarget && (stageDirty || stage !== stageTarget)) {
      stage = easeRect(stage ?? stageTarget, stageTarget, dt, stageJump || reduced);
      // Snap the last fraction of a pixel so easing ends and the matrix work stops.
      if (Math.abs(stage.x - stageTarget.x) < 0.1 && Math.abs(stage.y - stageTarget.y) < 0.1
        && Math.abs(stage.size - stageTarget.size) < 0.1) stage = stageTarget;
      stageJump = false;
      stageDirty = false;
      placeOrb(orbCamera, stage, canvas.clientWidth || 1, canvas.clientHeight || 1);
    }

    renderer.clear();
    landscape.render(renderer);
    renderer.clearDepth();
    renderer.render(orbScene, orbCamera);
  };
  raf = requestAnimationFrame(frame);

  return {
    setState(next) {
      if (destroyed || next === state) return;
      state = next;
      announce();
    },
    /** The state the host asked for. The orb may be showing `arming` instead. */
    getState: () => state,
    attachAnalyser(node, role) {
      if (destroyed) return () => {};
      const detach = bank.attach(node, role);
      announce();
      return () => {
        detach();
        if (!destroyed) announce();
      };
    },
    flare(strength = 1) {
      flareLevel = Math.min(1.5, flareLevel + Math.max(0, strength));
    },
    addSatellite() {},
    removeSatellite() {},
    pulseArc() {},
    setPerfMode(mode) {
      if (destroyed) return;
      governor.setMode(mode);
      const was = perfActive;
      perfActive = governor.sample(fps, performance.now());
      if (perfActive !== was) applyQuality();
    },
    setTheme(next) {
      if (destroyed) return;
      theme = next;
      landscape.setTheme(next === 'light');
      orb.setTheme(next === 'light');
    },
    setStage(rect, stageOpts) {
      if (destroyed) return;
      stageTarget = { x: rect.x, y: rect.y, size: rect.size };
      if (stageOpts?.animate === false) stageJump = true;
      stageDirty = true;
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      cancelAnimationFrame(raf);
      observer.disconnect();
      media?.removeEventListener('change', onMotionChange);
      bank.detachAll();
      orb.dispose();
      landscape.dispose();
      renderer.dispose();
      // Browsers cap live WebGL contexts; release ours now rather than at GC.
      renderer.forceContextLoss();
    },
  };
}
