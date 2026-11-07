/**
 * The page the snapshot tests photograph (GAL-12). It owns the clock: nothing is
 * drawn until the test asks, and then exactly `frames` frames at 60 fps, so every
 * run draws the same pictures. The dust is laid out from a fixed seed.
 */

import '../tokens.css';

// While the orb is drawn, its loop runs on this clock only. Outside that, the page
// keeps the browser's own, which Playwright needs to see the page settle.
const realFrame = window.requestAnimationFrame.bind(window);
const realCancel = window.cancelAnimationFrame.bind(window);
const realNow = performance.now.bind(performance);
let owned = false;
let now = 0;
let pending: FrameRequestCallback | null = null;
window.requestAnimationFrame = (cb) => {
  if (!owned) return realFrame(cb);
  pending = cb;
  return -1;
};
window.cancelAnimationFrame = (id) => {
  if (id === -1) pending = null;
  else realCancel(id);
};
performance.now = () => (owned ? now : realNow());

const FRAME_MS = 1000 / 60;

declare global {
  interface Window {
    /** Draw one state and theme, settled, and say when the picture is ready. */
    snap(state: string, theme: 'dark' | 'light', frames: number, nova?: { tier: string; frames: number }): Promise<void>;
  }
}

const { createGalaxy } = await import('../src/index');
const canvas = document.getElementById('galaxy') as HTMLCanvasElement;

window.snap = async (state, theme, frames, nova) => {
  document.documentElement.dataset.theme = theme;
  owned = true;
  now = 0;
  const galaxy = createGalaxy(canvas, {
    theme,
    seed: 1,
    perfMode: 'off',
    reducedMotion: false,
    // `listening` shows gold with no microphone only in demo mode.
    demo: true,
  });
  galaxy.setState(state as Parameters<typeof galaxy.setState>[0]);
  const draw = (n: number): void => {
    for (let i = 0; i < n; i++) {
      const step = pending;
      pending = null;
      now += FRAME_MS;
      step?.(now);
    }
  };
  draw(frames);
  // A celebration fired once the state has settled, drawn `nova.frames` frames in (GAL-9).
  if (nova) {
    galaxy.supernova(nova.tier as Parameters<typeof galaxy.supernova>[0]);
    draw(nova.frames);
  }
  // The orb's next frame stays pending forever: the picture holds still.
  owned = false;
  // Drawing in software runs behind the page. Reading the picture back waits for all
  // of it, so the screenshot that follows is quick and of the last frame.
  const copy = document.createElement('canvas').getContext('2d');
  copy?.drawImage(canvas, 0, 0, 1, 1);
  copy?.getImageData(0, 0, 1, 1);
  await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
};

document.body.dataset.ready = 'true';
