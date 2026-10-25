import '../tokens.css';
import './demo.css';
import {
  ORB_STATE_NAMES, STATE_LABELS, createGalaxy, defaultRect,
  type OrbStateName, type PerfMode, type StageRect, type Theme,
} from '../src/index';

const byId = <T extends HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`demo: missing #${id}`);
  return el as T;
};

const status = byId<HTMLParagraphElement>('status');
const note = byId<HTMLParagraphElement>('note');
const stateButtons = new Map<OrbStateName, HTMLButtonElement>();

const galaxy = createGalaxy(byId<HTMLCanvasElement>('galaxy'), {
  // The state buttons show every look, including gold with no microphone. A real host
  // leaves this off, so gold only ever means the microphone is live.
  demo: true,
  onStateChange: (s) => {
    status.textContent = STATE_LABELS[s];
    for (const [name, btn] of stateButtons) btn.setAttribute('aria-pressed', String(name === s));
  },
  onPerfChange: (active) => {
    note.textContent = active ? 'Performance mode is on.' : '';
  },
});

const statesGroup = byId<HTMLDivElement>('states');
for (const name of ORB_STATE_NAMES) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.textContent = name;
  btn.setAttribute('aria-pressed', String(name === 'idle'));
  btn.addEventListener('click', () => galaxy.setState(name));
  stateButtons.set(name, btn);
  statesGroup.append(btn);
}

let audio: AudioContext | null = null;
const ctx = (): AudioContext => (audio ??= new AudioContext());

// Microphone: arming until the stream is live, then listening.
const micBtn = byId<HTMLButtonElement>('mic');
let mic: { stream: MediaStream; detach: () => void } | null = null;
let arming = false;
micBtn.addEventListener('click', async () => {
  if (arming) return;
  if (mic) {
    mic.detach();
    for (const t of mic.stream.getTracks()) t.stop();
    mic = null;
    micBtn.textContent = 'Microphone: off';
    galaxy.setState('idle');
    return;
  }
  galaxy.setState('arming');
  arming = true;
  let stream: MediaStream | null = null;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const c = ctx();
    await c.resume();
    const detach = galaxy.attachAnalyser(c.createMediaStreamSource(stream), 'mic');
    mic = { stream, detach };
    micBtn.textContent = 'Microphone: on';
    note.textContent = '';
    galaxy.setState('listening');
  } catch {
    for (const t of stream?.getTracks() ?? []) t.stop();
    galaxy.setState('error');
    note.textContent = "I can't hear your microphone. Check the browser's permission for this page.";
  } finally {
    arming = false;
  }
});

// Speaking: a quiet tone with a speech-like amplitude wobble, through the playback analyser.
let toneId = 0;
byId<HTMLButtonElement>('speak').addEventListener('click', async () => {
  const mine = ++toneId;
  const c = ctx();
  await c.resume();
  const osc = c.createOscillator();
  const lfo = c.createOscillator();
  const lfoGain = c.createGain();
  const out = c.createGain();
  osc.frequency.value = 196;
  lfo.frequency.value = 4.3;
  lfoGain.gain.value = 0.03;
  out.gain.value = 0.04;
  lfo.connect(lfoGain).connect(out.gain);
  osc.connect(out).connect(c.destination);
  const detach = galaxy.attachAnalyser(out, 'playback');
  galaxy.setState('speaking');
  osc.start();
  lfo.start();
  const stopAt = c.currentTime + 3;
  osc.stop(stopAt);
  lfo.stop(stopAt);
  osc.onended = () => {
    detach();
    if (mine === toneId && galaxy.getState() === 'speaking') {
      galaxy.setState(mic ? 'listening' : 'idle');
    }
  };
});

byId<HTMLButtonElement>('flare-btn').addEventListener('click', () => galaxy.flare());

// Position: where a host might put the orb. The rect follows the window as it resizes.
type Stage = 'centre' | 'panel' | 'docked';
const stageRect = (stage: Stage): StageRect => {
  const w = innerWidth;
  const h = innerHeight;
  if (stage === 'docked') return { x: w - 24 - 48, y: h - 24 - 48, size: 96 };
  if (stage === 'panel') {
    // The default look, centred in the right 70% of the window.
    const r = defaultRect(w * 0.7, h);
    return { x: w * 0.3 + r.x, y: r.y, size: r.size };
  }
  return defaultRect(w, h);
};
const stageButtons = [...document.querySelectorAll<HTMLButtonElement>('[data-stage]')];
let stage: Stage | null = null;
for (const b of stageButtons) {
  b.addEventListener('click', () => {
    stage = (b.dataset['stage'] ?? 'centre') as Stage;
    galaxy.setStage(stageRect(stage));
    for (const o of stageButtons) o.setAttribute('aria-pressed', String(o === b));
  });
}
addEventListener('resize', () => {
  if (stage) galaxy.setStage(stageRect(stage), { animate: false });
});

const perfBtn = byId<HTMLButtonElement>('perf');
const perfModes: PerfMode[] = ['auto', 'on', 'off'];
let perfIndex = 0;
perfBtn.addEventListener('click', () => {
  perfIndex = (perfIndex + 1) % perfModes.length;
  const mode = perfModes[perfIndex] ?? 'auto';
  galaxy.setPerfMode(mode);
  perfBtn.textContent = `Performance: ${mode}`;
});

// Theme: a two-option switch, remembered per browser, defaulting to the OS setting.
const THEME_KEY = 'galaxy-demo-theme';
const readTheme = (): Theme => {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === 'dark' || saved === 'light') return saved;
  } catch {
    // Storage can be blocked; fall through to the OS preference.
  }
  return matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
};
const themeButtons = [...document.querySelectorAll<HTMLButtonElement>('[data-theme-choice]')];
const applyTheme = (theme: Theme): void => {
  document.documentElement.dataset['theme'] = theme;
  galaxy.setTheme(theme);
  for (const b of themeButtons) {
    b.setAttribute('aria-checked', String(b.dataset['themeChoice'] === theme));
  }
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // Not remembered; still applied.
  }
};
for (const b of themeButtons) {
  b.addEventListener('click', () => applyTheme(b.dataset['themeChoice'] === 'light' ? 'light' : 'dark'));
}
applyTheme(readTheme());
