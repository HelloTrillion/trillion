/**
 * Settings → Hearing → Microphone: which microphone she listens with, and a meter to
 * see it hear you before you talk to her.
 *
 * The choice is kept in this browser and changes nothing on the computer: its own
 * default input stays as it is for every other app. The microphone is only opened
 * when Test is pressed, and closed again after a short while.
 */

import { constraints } from './mic';

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

const DEFAULT = '';
const TEST_S = 20;
// Exact zeros this long: the device is delivering nothing at all.
const SILENT_S = 1.5;

export interface Microphones {
  /** The card, to be placed in the Hearing section. */
  readonly element: HTMLElement;
  /** Fill in the list; call when the section opens. */
  refresh(): Promise<void>;
  /** Stop a running test; call when the section closes. */
  stopTest(): void;
}

/** How a device is named: the browser's label, or a number until names are allowed. */
export function deviceName(d: { label: string }, index: number): string {
  return d.label.trim() || `Microphone ${index + 1}`;
}

/** The loudness of one moment, 0 to 1, from an analyser's samples. */
export function meterLevel(samples: Float32Array): number {
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += (samples[i] ?? 0) ** 2;
  const rms = Math.sqrt(sum / Math.max(1, samples.length));
  // Speech sits around 0.02 to 0.2: shown on a curve so a normal voice fills most of it.
  return Math.min(1, Math.sqrt(rms * 8));
}

/** The switch for interrupting her by talking. */
export interface BargeSwitch {
  on(): boolean;
  set(on: boolean): void;
}

export function createMicrophones(
  current: () => string | null,
  choose: (id: string | null) => void,
  barge?: BargeSwitch,
): Microphones {
  const card = el('div', 'settings-card');
  const head = el('div', 'settings-card-head');
  head.append(
    el('strong', '', 'Microphone'),
    el('small', '', 'Kept in this browser. Your computer’s own input setting stays as it is.'),
  );
  const row = el('div', 'settings-row');
  const label = el('label', '', 'Listen with');
  const pick = el('select');
  pick.id = 'mic-device';
  label.htmlFor = pick.id;
  const test = el('button', '', 'Test');
  test.type = 'button';
  row.append(label, pick, test);
  const meter = el('div', 'mic-meter');
  meter.setAttribute('role', 'meter');
  meter.setAttribute('aria-label', 'Microphone level');
  meter.setAttribute('aria-valuemin', '0');
  meter.setAttribute('aria-valuemax', '100');
  const fill = el('div', 'mic-meter-fill');
  meter.append(fill);
  meter.hidden = true;
  const note = el('p', 'settings-hint');
  note.setAttribute('role', 'status');
  card.append(head, row, meter, note);
  if (barge) {
    // Talking over her stops her (VOI-9). The page listens for it on this computer only.
    const label = el('label', 'settings-check');
    const box = el('input');
    box.type = 'checkbox';
    box.checked = barge.on();
    box.addEventListener('change', () => barge.set(box.checked));
    const words = el('span');
    words.append(
      el('strong', '', 'Interrupt her by talking'),
      el(
        'small',
        '',
        'While she answers a spoken question, the microphone stays open on this computer to notice you cut in. Nothing is sent until you do. Turn it off if she stops herself when using speakers.',
      ),
    );
    label.append(box, words);
    card.append(label);
  }

  let testing: { stop(): void } | null = null;

  async function refresh(): Promise<void> {
    let found: MediaDeviceInfo[] = [];
    try {
      found = (await navigator.mediaDevices?.enumerateDevices?.()) ?? [];
    } catch {
      found = [];
    }
    const inputs = found.filter((d) => d.kind === 'audioinput' && d.deviceId && d.deviceId !== 'default');
    const chosen = current() ?? DEFAULT;
    const options = [el('option', '', 'Your computer’s default input')];
    (options[0] as HTMLOptionElement).value = DEFAULT;
    inputs.forEach((d, i) => {
      const o = el('option', '', deviceName(d, i));
      o.value = d.deviceId;
      options.push(o);
    });
    if (chosen && !inputs.some((d) => d.deviceId === chosen)) {
      // Unplugged, for now: kept, and the default is used until it is back.
      const o = el('option', '', 'The one you chose (not connected)');
      o.value = chosen;
      options.push(o);
    }
    pick.replaceChildren(...options);
    pick.value = chosen;
    if (inputs.length && inputs.every((d) => !d.label)) {
      note.textContent = 'Press Test once to see your microphones by name.';
    } else if (!testing) {
      note.textContent = '';
    }
  }

  function stopTest(): void {
    testing?.stop();
    testing = null;
    test.textContent = 'Test';
    meter.hidden = true;
  }

  async function startTest(): Promise<void> {
    // Made in the click, so every browser lets it run.
    const ctx = new AudioContext();
    void ctx.resume().catch(() => undefined);
    test.textContent = 'Stop';
    note.textContent = 'Say something…';
    let stream: MediaStream | null = null;
    let frame = 0;
    let timer = 0;
    const end = (): void => {
      cancelAnimationFrame(frame);
      clearTimeout(timer);
      for (const t of stream?.getTracks() ?? []) t.stop();
      void ctx.close().catch(() => undefined);
    };
    testing = { stop: end };
    try {
      stream = await navigator.mediaDevices.getUserMedia(constraints(pick.value || null));
    } catch (error) {
      end();
      testing = null;
      test.textContent = 'Test';
      const name = error instanceof Error ? error.name : '';
      note.textContent =
        name === 'NotAllowedError'
          ? 'The browser did not allow the microphone. Allow it for this page, then test again.'
          : 'That microphone could not be opened. Choose another, or check it is plugged in.';
      return;
    }
    if (!testing) {
      for (const t of stream.getTracks()) t.stop();
      return;
    }
    // Names are shown once the microphone has been allowed.
    void refresh();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    ctx.createMediaStreamSource(stream).connect(analyser);
    const samples = new Float32Array(analyser.fftSize);
    meter.hidden = false;
    let heard = false;
    let silentFor = 0;
    let last = performance.now();
    const tick = (now: number): void => {
      analyser.getFloatTimeDomainData(samples);
      const level = meterLevel(samples);
      fill.style.width = `${Math.round(level * 100)}%`;
      meter.setAttribute('aria-valuenow', String(Math.round(level * 100)));
      const exact = samples.every((v) => v === 0);
      silentFor = exact ? silentFor + (now - last) / 1000 : 0;
      last = now;
      if (level > 0.25 && !heard) {
        heard = true;
        note.textContent = 'She can hear this one.';
      } else if (!heard && silentFor >= SILENT_S) {
        note.textContent =
          'This microphone is sending pure silence. On an audio interface, check which input it is plugged into and its gain, or choose another.';
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    timer = window.setTimeout(stopTest, TEST_S * 1000);
  }

  pick.addEventListener('change', () => {
    choose(pick.value || null);
    if (testing) {
      stopTest();
      void startTest();
    }
  });
  test.addEventListener('click', () => {
    if (testing) stopTest();
    else void startTest();
  });
  navigator.mediaDevices?.addEventListener?.('devicechange', () => void refresh());

  return { element: card, refresh, stopTest };
}
