/**
 * Settings → Your voice: teach her the user's voice (§5.3a).
 *
 * Three short phrases, read aloud. Each is recorded here (16 kHz mono PCM16, the same
 * frames the microphone sends her) and sent to the core on this machine, which checks
 * it and keeps only a voiceprint, in a file only the user can read. Nothing about the
 * voice comes back to the page: only whether a phrase was fine, or what was wrong.
 *
 * Once she knows the voice, a spoken yes counts only in it, and to anyone else she
 * keeps the user's things to herself. A tap on a card works as it always has.
 *
 * Once a voice is enrolled, a switch lets the user's own words approve what can be
 * undone (SPN-20). Off until they turn it on; the core checks everything else.
 * If the voiceprint file is there but can't be read, she can't check any voice: every
 * approval needs a tap until the user teaches her again or forgets it. Said plainly
 * here, never as "not set up".
 */

import type { Host } from './panel';
import { megabytes } from './listening';
import type { Api } from './settings';

export interface VoiceprintState {
  readonly installed: boolean;
  readonly model: { readonly ready: boolean; readonly size: number; readonly credit: string; readonly licence_url: string };
  readonly enrolled: boolean;
  readonly enrolled_ms: number | null;
  /** Enrolled, and the model is here to check each voice. */
  readonly working: boolean;
  readonly prompts: readonly string[];
  readonly recorded: readonly number[];
  /** The user's own words approve things that can be undone (SPN-20). */
  readonly own_words?: boolean;
  /** The voiceprint file is there but can't be read: no voice is checked. */
  readonly unreadable?: 'locked' | 'damaged' | null;
}

export const OWN_WORDS_LABEL = 'Let my own words approve things I can undo';
export const OWN_WORDS_HINT =
  'When you say exactly what to keep, drop or forget, she does it without asking again. Only for things that can be undone, and only in your voice. Spending, sending and anything that can’t be undone always ask.';

/** The switch is offered only once she knows the voice, not while teaching her, and not
 * while the voice file can't be read (no voice can be checked then). */
export function showsOwnWords(s: VoiceprintState | null, teaching: boolean): boolean {
  return !!s?.enrolled && !s.unreadable && !teaching;
}

/** What the section says when the voiceprint can't be read. */
export const UNREADABLE_TEXT =
  "Your voice file can't be read, so she can't tell your voice from anyone's. Until you teach her again, every approval needs a tap.";

/** Records one phrase: frames of PCM16 while on. */
export interface Recorder {
  start(onFrame: (pcm: ArrayBuffer) => void): Promise<void>;
  stop(): void;
}

/** The longest a phrase is recorded for, in seconds: it stops on its own. */
export const PHRASE_S = 8;
/** The fewest seconds worth sending: shorter is "too short" without asking. */
export const MIN_S = 1;
const BYTES_PER_S = 32_000;

const PROBLEMS: Readonly<Record<string, string>> = {
  quiet: 'That was too quiet. Come a little closer to the microphone and read it again.',
  short: 'That was too short. Read the whole phrase, at your usual pace.',
  loud: 'That was so loud it distorted. Move back a little and read it again.',
  voices: 'That sounded like more than one voice. Read it again on your own.',
  bad_audio: 'That recording could not be read. Try again.',
  model: 'Something went wrong checking it. Try again.',
  not_installed: 'This part of Trillion is not installed yet.',
  needs_download: 'Get the voice model first.',
  phrases_missing: 'Read every phrase first.',
  not_saved: "Your voice couldn't be saved in the data folder.",
  not_deleted: "Your voice couldn't be removed from the data folder.",
  not_enrolled: 'Teach her your voice first.',
  download_failed: 'The download could not be fetched. Check the connection and try again.',
  download_mismatch: 'The downloaded file did not match what was expected, so it was thrown away. Try again.',
  unreachable: 'Could not reach the download. Check the connection and try again.',
  timeout: 'The download took too long. Try again.',
  disk: 'The file could not be written. Is the disk full?',
};

export function problemText(code: string | null | undefined): string {
  return PROBLEMS[code ?? ''] ?? 'That did not work. Try again.';
}

/** One line on where things stand, beside "Your voice" and at the top of the section. */
export function statusText(s: VoiceprintState | null): string {
  if (!s) return '';
  if (s.unreadable) return "Your voice file can't be read";
  if (s.enrolled && !s.working) return 'Set up, but the voice model is missing';
  if (s.enrolled) return 'She knows your voice';
  return 'Not set up';
}

/** The first phrase not yet recorded, or null when all are. */
export function nextPhrase(recorded: readonly number[], count: number): number | null {
  for (let i = 0; i < count; i++) if (!recorded.includes(i)) return i;
  return null;
}

/** Frames joined and base64-encoded, as the core takes a phrase. */
export function encodePhrase(frames: readonly ArrayBuffer[]): string {
  let binary = '';
  for (const f of frames) {
    const bytes = new Uint8Array(f);
    for (let i = 0; i < bytes.length; i += 0x8000) {
      binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    }
  }
  return btoa(binary);
}

export function seconds(frames: readonly ArrayBuffer[]): number {
  return frames.reduce((n, f) => n + f.byteLength, 0) / BYTES_PER_S;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(text: string, cls = ''): HTMLButtonElement {
  const b = el('button', cls, text);
  b.type = 'button';
  return b;
}

export interface VoiceprintSettings {
  open(): Promise<void>;
  summary(): string;
  peek(): Promise<void>;
}

export function createVoiceprintSettings(
  host: Host,
  api: Api,
  recorder: Recorder,
  hooks: { readonly changed: () => void; readonly confirm?: (text: string) => boolean } = { changed: () => {} },
): VoiceprintSettings {
  let state: VoiceprintState | null = null;
  let recording = false;
  let frames: ArrayBuffer[] = [];
  let timer: ReturnType<typeof setTimeout> | null = null;
  // Teaching her is under way: the phrases are shown even once she knows the voice.
  let teaching = false;

  const form = el('form', 'settings');
  form.addEventListener('submit', (ev) => ev.preventDefault());
  const title = el('h2', '', 'Your voice');
  title.id = 'voiceprint-title';
  host.setAttribute('aria-labelledby', title.id);
  const intro = el(
    'p',
    'settings-note',
    'Teach her your voice, and a spoken yes to a card counts only when it is yours. With anyone else in the room she keeps your things to herself. A tap on a card always works.',
  );
  const privacy = el(
    'p',
    'settings-hint',
    'Kept on this computer only, in a file in your Trillion data folder that only you can read. It is never sent anywhere.',
  );
  const where = el('p', 'settings-note');
  const credit = el('p', 'settings-hint');
  const phraseBox = el('div', 'settings-row');
  const phraseLabel = el('p', 'settings-hint');
  const phraseText = el('blockquote', 'settings-note');
  phraseBox.append(phraseLabel, phraseText);
  const status = el('p', 'settings-status');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  const ownRow = el('label', 'settings-check');
  const ownBox = el('input');
  ownBox.type = 'checkbox';
  ownBox.setAttribute('role', 'switch');
  ownBox.id = 'voiceprint-own-words';
  const ownWords = el('span');
  ownWords.append(el('strong', '', OWN_WORDS_LABEL), el('small', '', OWN_WORDS_HINT));
  ownRow.append(ownBox, ownWords);
  const actions = el('div', 'settings-actions');
  const download = button('Get the voice model', 'approve');
  const teach = button('Teach her your voice', 'approve');
  const record = button('Record', 'approve');
  const again = button('Teach her again');
  const forget = button('Forget my voice');
  const close = button('Close', 'settings-close');
  actions.append(download, teach, record, again, forget, close);
  form.append(title, intro, where, phraseBox, ownRow, status, actions, privacy, credit);
  host.replaceChildren(form);

  function render(): void {
    const s = state;
    const said = s?.unreadable ? UNREADABLE_TEXT : statusText(s) ? `${statusText(s)}.` : '';
    where.textContent = said;
    const ready = !!s?.installed && !!s.model.ready;
    download.hidden = !s || !s.installed || s.model.ready;
    download.textContent = s ? `Get the voice model (${megabytes(s.model.size)} download)` : 'Get the voice model';
    credit.replaceChildren();
    if (s) {
      credit.append(`Voice model: ${s.model.credit}. `);
      const link = el('a', '', 'Licence');
      link.href = s.model.licence_url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      credit.append(link);
    }
    if (s && !s.installed) {
      where.textContent = `${said} Telling voices apart needs an optional part of Trillion: run "uv sync --extra speaker" where Trillion is installed, then come back.`;
    }
    const next = s ? nextPhrase(s.recorded, s.prompts.length) : null;
    const showPhrases = ready && teaching && next !== null;
    phraseBox.hidden = !showPhrases;
    if (showPhrases && s && next !== null) {
      phraseLabel.textContent = `Phrase ${next + 1} of ${s.prompts.length}. Press Record, read it aloud, then press Stop.`;
      phraseText.textContent = s.prompts[next] ?? '';
    }
    teach.hidden = !ready || teaching || !!s?.enrolled;
    again.hidden = !ready || teaching || !s?.enrolled;
    record.hidden = !showPhrases;
    record.textContent = recording ? 'Stop' : 'Record';
    forget.hidden = !s?.enrolled || teaching;
    ownRow.hidden = !showsOwnWords(s, teaching);
    ownBox.checked = s?.own_words === true;
  }

  async function call(method: 'GET' | 'POST' | 'PUT' | 'DELETE', path: string, body?: unknown): Promise<Record<string, unknown> | null> {
    const r = await api(method, path, method === 'GET' ? undefined : (body ?? {})).catch(() => null);
    if (!r) {
      status.textContent = 'Trillion is not running.';
      return null;
    }
    const json = (await r.json().catch(() => ({}))) as Record<string, unknown>;
    if (!r.ok) {
      status.textContent = problemText(json.error as string | undefined);
      return null;
    }
    return json;
  }

  async function load(): Promise<void> {
    const s = await call('GET', '/api/voiceprint');
    if (s) state = s as unknown as VoiceprintState;
    render();
  }

  function stopTimer(): void {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  }

  async function finishPhrase(): Promise<void> {
    stopTimer();
    recorder.stop();
    recording = false;
    const s = state;
    const index = s ? nextPhrase(s.recorded, s.prompts.length) : null;
    const taken = frames;
    frames = [];
    render();
    if (!s || index === null) return;
    if (seconds(taken) < MIN_S) {
      status.textContent = problemText('short');
      return;
    }
    status.textContent = 'Checking…';
    const r = await call('POST', '/api/voiceprint/phrase', { index, pcm: encodePhrase(taken) });
    if (!r) return;
    state = { ...s, recorded: (r.recorded as number[]) ?? s.recorded };
    if (r.ok !== true) {
      status.textContent = problemText(r.problem as string);
      render();
      return;
    }
    if (nextPhrase(state.recorded, state.prompts.length) !== null) {
      status.textContent = 'Got it.';
      render();
      return;
    }
    status.textContent = 'Saving…';
    const saved = await call('POST', '/api/voiceprint/save');
    if (!saved) return;
    if (saved.ok !== true) {
      state = { ...state, recorded: (saved.recorded as number[]) ?? [] };
      const which = typeof saved.phrase === 'number' ? ` Read phrase ${saved.phrase + 1} again.` : '';
      status.textContent = `One phrase didn't sound like the others.${which}`;
      render();
      return;
    }
    state = saved as unknown as VoiceprintState;
    teaching = false;
    status.textContent = 'Done. She knows your voice now.';
    render();
    hooks.changed();
  }

  ownBox.addEventListener('change', () => {
    const on = ownBox.checked;
    void (async () => {
      ownBox.disabled = true;
      const s = await call('PUT', '/api/voiceprint', { own_words: on });
      ownBox.disabled = false;
      if (s) {
        state = s as unknown as VoiceprintState;
        status.textContent = state.own_words ? 'On. Your own words approve things you can undo.' : 'Off. Everything asks on a card, as before.';
      }
      render();
    })();
  });

  download.addEventListener('click', () => {
    void (async () => {
      status.textContent = 'Downloading…';
      download.disabled = true;
      const s = await call('POST', '/api/voiceprint/model');
      download.disabled = false;
      if (s) {
        state = s as unknown as VoiceprintState;
        status.textContent = 'The voice model is ready.';
      }
      render();
    })();
  });

  async function begin(): Promise<void> {
    const s = await call('DELETE', '/api/voiceprint/draft');
    if (s) state = s as unknown as VoiceprintState;
    teaching = true;
    status.textContent = '';
    render();
  }
  teach.addEventListener('click', () => void begin());
  again.addEventListener('click', () => void begin());

  record.addEventListener('click', () => {
    if (recording) {
      void finishPhrase();
      return;
    }
    void (async () => {
      frames = [];
      recording = true;
      status.textContent = 'Listening…';
      render();
      try {
        await recorder.start((pcm) => {
          if (recording) frames.push(pcm);
        });
      } catch {
        recording = false;
        status.textContent = 'The microphone could not be opened.';
        render();
        return;
      }
      timer = setTimeout(() => void finishPhrase(), PHRASE_S * 1000);
    })();
  });

  forget.addEventListener('click', () => {
    const ask = hooks.confirm ?? ((t: string) => window.confirm(t));
    if (!ask('Forget your voice? A spoken yes will count from any voice again, as before you set this up.')) return;
    void (async () => {
      const s = await call('DELETE', '/api/voiceprint');
      if (s) {
        state = s as unknown as VoiceprintState;
        status.textContent = 'Forgotten. Your voiceprint is gone from this computer.';
        hooks.changed();
      }
      render();
    })();
  });

  close.addEventListener('click', () => {
    if (recording) {
      stopTimer();
      recorder.stop();
      recording = false;
      frames = [];
    }
    teaching = false;
    host.close();
  });

  return {
    async open() {
      if (host.open) return;
      status.textContent = '';
      teaching = false;
      host.showModal();
      await load();
    },
    summary() {
      return statusText(state);
    },
    async peek() {
      const r = await api('GET', '/api/voiceprint').catch(() => null);
      if (r?.ok) state = (await r.json()) as VoiceprintState;
    },
  };
}
