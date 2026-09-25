import '../tokens.css';
import './demo.css';
import {
  ORB_STATE_NAMES, STATE_LABELS, createGalaxy, type OrbStateName, type PerfMode, type Theme,
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
micBtn.addEventListener('click', async () => {
  if (mic) {
    mic.detach();
    for (const t of mic.stream.getTracks()) t.stop();
    mic = null;
    micBtn.textContent = 'Microphone: off';
    galaxy.setState('idle');
    return;
  }
  galaxy.setState('arming');
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const c = ctx();
    await c.resume();
    const detach = galaxy.attachAnalyser(c.createMediaStreamSource(stream), 'mic');
    mic = { stream, detach };
    micBtn.textContent = 'Microphone: on';
    note.textContent = '';
    galaxy.setState('listening');
  } catch {
    galaxy.setState('error');
    note.textContent = "I can't hear your microphone. Check the browser's permission for this page.";
  }
});

// Speaking: a quiet tone with a speech-like amplitude wobble, through the playback analyser.
byId<HTMLButtonElement>('speak').addEventListener('click', async () => {
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
    if (galaxy.getState() === 'speaking') galaxy.setState(mic ? 'listening' : 'idle');
  };
});

byId<HTMLButtonElement>('flare-btn').addEventListener('click', () => galaxy.flare());

const perfBtn = byId<HTMLButtonElement>('perf');
const perfModes: PerfMode[] = ['auto', 'on', 'off'];
let perfIndex = 0;
perfBtn.addEventListener('click', () => {
  perfIndex = (perfIndex + 1) % perfModes.length;
  const mode = perfModes[perfIndex] ?? 'auto';
  galaxy.setPerfMode(mode);
  perfBtn.textContent = `Performance: ${mode}`;
});

const themeBtn = byId<HTMLButtonElement>('theme');
let theme: Theme = 'dark';
themeBtn.addEventListener('click', () => {
  theme = theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset['theme'] = theme;
  galaxy.setTheme(theme);
  themeBtn.textContent = `Theme: ${theme}`;
});
