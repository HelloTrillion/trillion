/**
 * GAL-10, the Living Mind: `createMind(canvas, options)` puts the mind on a canvas.
 * Bloom, orbit controls, labels for regions and helpers, hover and click, a camera that
 * flies to a node, and a governor that sheds effects on a slow machine. The page owns
 * the data, the HUD and the inspector; this owns three.js.
 */

import { ACESFilmicToneMapping, PerspectiveCamera, Raycaster, Vector2, Vector3, WebGLRenderer } from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import { CSS2DObject, CSS2DRenderer } from 'three/examples/jsm/renderers/CSS2DRenderer.js';
import type { MindMap, MindNode, ObserveEvent, RegionId } from './contract';
import { type DegradeLevel, MindGovernor, shouldAutoRotate } from './governor';
import { ANCHORS } from './layout';
import { type MindPalette, readPalette } from './palette';
import { MindWorld } from './world';

export * from './contract';
export { planEvent, type Effect, type Tint } from './pulses';
export { layoutMind, layoutMemory, ANCHORS, MEMBRANE_RADIUS, MEMORY_RADIUS } from './layout';
export { MindGovernor, shouldAutoRotate, IDLE_ROTATE_MS, type DegradeLevel } from './governor';
export { PALETTE_TOKENS, type MindPalette } from './palette';
export { MindWorld } from './world';

export interface MindOptions {
  /** Colours; read from the page's `--mind-*` tokens when left out. */
  readonly palette?: MindPalette;
  readonly seed?: number;
  /** Override the prefers-reduced-motion media query. */
  readonly reducedMotion?: boolean;
  /** An overlay for the region and helper labels, laid over the canvas. */
  readonly labels?: HTMLElement;
  /** Start with bloom and particles off (a phone, a weak machine). */
  readonly lowPower?: boolean;
  /** The pointer is over a node (or nothing): CSS pixels from the viewport's top-left. */
  readonly onHover?: (id: string | null, at: { readonly x: number; readonly y: number } | null) => void;
  /** A node was clicked (or its label). */
  readonly onSelect?: (id: string) => void;
  /** A double-click on empty space: back to the overview. */
  readonly onOverview?: () => void;
  /** A `memory.written` arrived: read the skeleton again and call `setData`. */
  readonly onRefetch?: () => void;
  /** The governor shed effects (1: bloom off, 2: particles and travelling pulses off). */
  readonly onDegrade?: (level: DegradeLevel) => void;
}

export interface Mind {
  setData(map: MindMap): void;
  /** Fly to a node; null flies back to the overview. False when the node isn't drawn. */
  focus(id: string | null): boolean;
  /** Fly to show a whole region. */
  focusRegion(region: RegionId): void;
  setRegionVisible(region: RegionId, visible: boolean): void;
  regionVisible(region: RegionId): boolean;
  /** An observe event. False when it was dropped (it named nothing shown). */
  apply(event: ObserveEvent): boolean;
  /** Light one node's edges; null clears. */
  highlight(id: string | null): void;
  has(id: string): boolean;
  node(id: string): MindNode | undefined;
  setReducedMotion(on: boolean): void;
  dispose(): void;
}

const OVERVIEW_POS = new Vector3(0, 6, 34);
const OVERVIEW_LOOK = new Vector3(0, 0, 0);
const MAX_DT = 0.1;

export function createMind(canvas: HTMLCanvasElement, opts: MindOptions = {}): Mind {
  const renderer = new WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  renderer.toneMapping = ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;

  const media = typeof matchMedia === 'function' ? matchMedia('(prefers-reduced-motion: reduce)') : null;
  let reduced = opts.reducedMotion ?? media?.matches ?? false;

  const world = new MindWorld({
    palette: opts.palette ?? readPalette(),
    ...(opts.seed !== undefined ? { seed: opts.seed } : {}),
    reducedMotion: reduced,
    ...(opts.onRefetch ? { onRefetch: opts.onRefetch } : {}),
  });
  const scene = world.scene;
  const camera = new PerspectiveCamera(55, 1, 0.1, 600);
  camera.position.copy(OVERVIEW_POS);

  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.07;
  controls.autoRotateSpeed = 0.25;
  controls.minDistance = 3;
  controls.maxDistance = 90;

  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new Vector2(1, 1), 1.15, 0.6, 0.72);
  composer.addPass(bloom);

  const labelRenderer = opts.labels ? new CSS2DRenderer({ element: opts.labels }) : null;

  const governor = new MindGovernor(opts.lowPower ? 2 : 0);
  const applyLevel = (level: DegradeLevel): void => {
    bloom.enabled = level < 1;
    world.setDegrade(level);
  };
  applyLevel(governor.level);

  const resize = (): void => {
    const w = canvas.clientWidth || 1;
    const h = canvas.clientHeight || 1;
    renderer.setSize(w, h, false);
    composer.setSize(w, h);
    bloom.setSize(w, h);
    labelRenderer?.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };
  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  resize();

  // ── Labels: region headings, her name and her helpers' names; never one per node. ──
  let labelObjects: CSS2DObject[] = [];
  const buildLabels = (): void => {
    for (const o of labelObjects) {
      o.element.remove();
      o.removeFromParent();
    }
    labelObjects = [];
    if (!labelRenderer) return;
    for (const l of world.labels) {
      const div = document.createElement('div');
      div.className = `mind-label mind-label-${l.kind}`;
      div.textContent = l.text;
      div.dataset['region'] = l.region;
      div.addEventListener('click', () => {
        touch();
        if (l.id) opts.onSelect?.(l.id);
        else mind.focusRegion(l.region);
      });
      const obj = new CSS2DObject(div);
      obj.position.copy(l.at);
      (world.regionGroup(l.region) ?? scene).add(obj);
      labelObjects.push(obj);
    }
  };

  // ── Camera: glides to a target, cancelled the moment the user takes hold. ──
  let fly: { pos: Vector3; look: Vector3 } | null = null;
  let lastTouch = performance.now();
  const touch = (): void => {
    lastTouch = performance.now();
  };
  const flyTo = (pos: Vector3, look: Vector3): void => {
    touch();
    if (reduced) {
      // No glide under reduced motion: arrive.
      camera.position.copy(pos);
      controls.target.copy(look);
      fly = null;
      return;
    }
    fly = { pos, look };
  };
  const onStart = (): void => {
    fly = null;
    touch();
  };
  controls.addEventListener('start', onStart);

  // ── Picking ──
  const raycaster = new Raycaster();
  const pointer = new Vector2();
  const pick = (ev: PointerEvent | MouseEvent): string | null => {
    const rect = canvas.getBoundingClientRect();
    pointer.set(((ev.clientX - rect.left) / rect.width) * 2 - 1, -((ev.clientY - rect.top) / rect.height) * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    for (const hit of raycaster.intersectObjects(world.pickTargets(), false)) {
      const id = world.idFromHit(hit.object, hit.instanceId);
      if (id) return id;
    }
    return null;
  };
  let hovered: string | null = null;
  const setHover = (id: string | null, ev: PointerEvent | null): void => {
    if (id !== hovered) {
      hovered = id;
      world.highlight(id);
      canvas.style.cursor = id ? 'pointer' : '';
    }
    opts.onHover?.(id, id && ev ? { x: ev.clientX, y: ev.clientY } : null);
  };
  const onMove = (ev: PointerEvent): void => setHover(pick(ev), ev);
  const onLeave = (): void => setHover(null, null);
  let down = { x: 0, y: 0 };
  const onDown = (ev: PointerEvent): void => {
    down = { x: ev.clientX, y: ev.clientY };
    touch();
  };
  const onClick = (ev: MouseEvent): void => {
    // More than 6 px of travel was an orbit, not a click.
    if (Math.hypot(ev.clientX - down.x, ev.clientY - down.y) > 6) return;
    const id = pick(ev);
    if (id) opts.onSelect?.(id);
  };
  const onDouble = (ev: MouseEvent): void => {
    if (!pick(ev)) opts.onOverview?.();
  };
  const onWheel = (): void => touch();
  canvas.addEventListener('pointermove', onMove);
  canvas.addEventListener('pointerleave', onLeave);
  canvas.addEventListener('pointerdown', onDown);
  canvas.addEventListener('click', onClick);
  canvas.addEventListener('dblclick', onDouble);
  canvas.addEventListener('wheel', onWheel, { passive: true });

  const onMotionChange = (e: MediaQueryListEvent): void => {
    if (opts.reducedMotion === undefined) mind.setReducedMotion(e.matches);
  };
  media?.addEventListener('change', onMotionChange);

  // ── Frame loop: nothing is drawn while the tab is hidden. ──
  let raf = 0;
  let last = performance.now();
  let clock = 0;
  let driftPrev = 0;
  let drifting = false;
  let disposed = false;
  const frame = (now: number): void => {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(MAX_DT, Math.max(0, (now - last) / 1000));
    last = now;
    if (typeof document !== 'undefined' && document.hidden) return;
    clock += dt;
    const before = governor.level;
    const level = governor.sample(dt, clock);
    if (level !== before) {
      applyLevel(level);
      opts.onDegrade?.(level);
    }

    controls.autoRotate = !fly && shouldAutoRotate(now - lastTouch, reduced);
    controls.update(dt);
    // While it turns idly, a slow rise and fall, applied as a change so it never
    // fights the controls and stops without a jump.
    if (controls.autoRotate) {
      const cur = Math.sin(clock * 0.22) * 0.5;
      if (drifting) camera.position.y += cur - driftPrev;
      driftPrev = cur;
      drifting = true;
    } else drifting = false;

    if (fly) {
      const k = 1 - Math.pow(0.92, dt * 60);
      camera.position.lerp(fly.pos, k);
      controls.target.lerp(fly.look, k);
      if (camera.position.distanceToSquared(fly.pos) < 0.0025 && controls.target.distanceToSquared(fly.look) < 0.0025) {
        camera.position.copy(fly.pos);
        controls.target.copy(fly.look);
        fly = null;
      }
    }

    world.update(dt);
    composer.render(dt);
    labelRenderer?.render(scene, camera);
  };
  raf = requestAnimationFrame(frame);

  const onVisible = (): void => {
    // Back from hidden: no catch-up burst of time.
    last = performance.now();
  };
  document.addEventListener('visibilitychange', onVisible);

  const mind: Mind = {
    setData(map) {
      if (disposed) return;
      world.setData(map);
      buildLabels();
      if (hovered && !world.has(hovered)) setHover(null, null);
    },
    focus(id) {
      if (disposed) return false;
      if (id === null) {
        flyTo(OVERVIEW_POS.clone(), OVERVIEW_LOOK.clone());
        return true;
      }
      const at = world.positionOf(id);
      if (!at) return false;
      const dir = camera.position.clone().sub(at);
      if (dir.lengthSq() < 1e-4) dir.set(0, 0.4, 1);
      dir.normalize();
      // Her star is big; stand further back from it.
      const node = world.node(id);
      const back = node?.type === 'core' || node?.type === 'ring' ? 10 : 4.5;
      flyTo(at.clone().addScaledVector(dir, back), at.clone());
      return true;
    },
    focusRegion(region) {
      const a = ANCHORS[region];
      const look = new Vector3(a.x, a.y, a.z);
      const dir = camera.position.clone().sub(look);
      if (dir.lengthSq() < 1e-4) dir.set(0, 0.4, 1);
      dir.normalize();
      flyTo(look.clone().addScaledVector(dir, 11), look);
    },
    setRegionVisible(region, visible) {
      world.setRegionVisible(region, visible);
      const n = hovered ? world.node(hovered) : undefined;
      if (n && !visible && n.region === region) setHover(null, null);
    },
    regionVisible: (region) => world.regionVisible(region),
    apply: (event) => !disposed && world.apply(event),
    highlight(id) {
      world.highlight(id);
    },
    has: (id) => world.has(id),
    node: (id) => world.node(id),
    setReducedMotion(on) {
      reduced = on;
      world.setReducedMotion(on);
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      cancelAnimationFrame(raf);
      observer.disconnect();
      media?.removeEventListener('change', onMotionChange);
      document.removeEventListener('visibilitychange', onVisible);
      controls.removeEventListener('start', onStart);
      canvas.removeEventListener('pointermove', onMove);
      canvas.removeEventListener('pointerleave', onLeave);
      canvas.removeEventListener('pointerdown', onDown);
      canvas.removeEventListener('click', onClick);
      canvas.removeEventListener('dblclick', onDouble);
      canvas.removeEventListener('wheel', onWheel);
      for (const o of labelObjects) o.element.remove();
      labelObjects = [];
      controls.dispose();
      world.dispose();
      bloom.dispose();
      composer.dispose();
      renderer.dispose();
      // Browsers cap live WebGL contexts; release ours now rather than at GC.
      renderer.forceContextLoss();
    },
  };
  return mind;
}
