/**
 * The Voice settings panel: how she speaks, or that she does not.
 *
 * The choices come from the core's catalog, so a new provider appears here
 * without this file changing. Keys follow the same rules as the model's: sent
 * to the core on this machine, kept in the system keychain, never read back.
 */

import { type Api, type Reach, errorText } from './settings';
import type { Host } from './panel';
import { type Cost, FREE, createCapStep } from './spend';

interface Option {
  readonly kind: string;
  readonly title: string;
  readonly note: string;
  readonly where: 'core' | 'client';
  readonly needs_key: boolean;
  readonly free: boolean;
  readonly key_link: string | null;
  readonly takes_url: boolean;
  readonly key: { readonly saved: boolean; readonly ends: string } | null;
  /** Roughly what it costs, and the caps a key for it will have (ONB-9). */
  readonly cost?: Cost;
}

interface State {
  readonly options: readonly Option[];
  readonly saved: { readonly kind: string; readonly voice: string; readonly base_url: string | null } | null;
  /** The choice made before speech was switched off, offered again when it comes back on. */
  readonly last?: { readonly kind: string; readonly voice: string; readonly base_url: string | null } | null;
  readonly keychain: boolean;
}

interface Choice {
  readonly id: string;
  readonly name: string;
}

const OFF = '';
const SAMPLE = 'Short answer: yes. Long answer: also yes.';

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** The voices this computer has, the user's own language first. */
export function systemVoices(all: readonly SpeechSynthesisVoice[], language: string): Choice[] {
  const prefix = language.slice(0, 2).toLowerCase();
  const rank = (v: SpeechSynthesisVoice): number => (v.lang.toLowerCase().startsWith(prefix) ? 0 : 1);
  return [...all]
    .sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name))
    .map((v) => ({ id: v.voiceURI, name: `${v.name} (${v.lang})` }));
}

/** A voice made by someone else's service, with no address of the user's own, is hosted. */
export function speechReach(o: Pick<Option, 'title' | 'where' | 'takes_url'>): Reach {
  return { provider: o.title, hosted: o.where === 'core' && !o.takes_url };
}

export interface SpeechSettings {
  open(): Promise<void>;
}

export function createSpeechSettings(dialog: Host, api: Api): SpeechSettings {
  let state: State | null = null;
  let kind = OFF;
  let busy = false;

  const form = el('form', 'settings');
  // Enter in a field never submits; the buttons do the work.
  form.addEventListener('submit', (ev) => ev.preventDefault());
  const title = el('h2', '', 'Her voice');
  title.id = 'speech-title';
  dialog.setAttribute('aria-labelledby', title.id);
  const intro = el(
    'p',
    'settings-note',
    'Choose how she speaks. The free choices need no account. With a paid voice, each sentence she says is sent to that provider.',
  );
  const choices = el('div', 'settings-kinds');
  choices.setAttribute('role', 'radiogroup');
  choices.setAttribute('aria-label', 'Voice provider');

  const keyRow = el('div', 'settings-row');
  const keyLabel = el('label', '', 'API key');
  const keyInput = el('input');
  keyInput.type = 'password';
  keyInput.id = 'speech-key';
  keyInput.autocomplete = 'off';
  keyInput.spellcheck = false;
  keyLabel.htmlFor = keyInput.id;
  const keyHint = el('p', 'settings-hint');
  const keyLink = el('a', '', 'Get a key');
  keyLink.target = '_blank';
  keyLink.rel = 'noopener noreferrer';
  const forget = el('button', 'quiet', 'Remove saved key');
  forget.type = 'button';
  keyRow.append(keyLabel, keyInput, keyHint, keyLink, forget);

  const urlRow = el('div', 'settings-row');
  const urlLabel = el('label', '', 'Address');
  const urlInput = el('input');
  urlInput.type = 'url';
  urlInput.id = 'speech-url';
  urlInput.placeholder = 'http://192.168.1.20:8880/v1';
  urlInput.spellcheck = false;
  urlLabel.htmlFor = urlInput.id;
  const urlHint = el(
    'p',
    'settings-hint',
    'Plain http works only for a numeric address on your own network. Anything else needs https.',
  );
  urlRow.append(urlLabel, urlInput, urlHint);

  const voiceRow = el('div', 'settings-row');
  const voiceLabel = el('label', '', 'Voice');
  const voiceSelect = el('select');
  voiceSelect.id = 'speech-voice';
  voiceLabel.htmlFor = voiceSelect.id;
  const check = el('button', '', 'Check and list voices');
  check.type = 'button';
  const sample = el('button', '', 'Hear it');
  sample.type = 'button';
  voiceRow.append(voiceLabel, voiceSelect, check, sample);

  // What the chosen voice costs, and its caps, saved with the key (BRN-7, ONB-9).
  const spend = createCapStep('speech');

  const status = el('p', 'settings-status');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  const actions = el('div', 'settings-actions');
  const save = el('button', 'approve', 'Save');
  save.type = 'button';
  const close = el('button', 'settings-close', 'Close');
  close.type = 'button';
  actions.append(save, close);
  form.append(title, intro, choices, keyRow, urlRow, voiceRow, spend.element, status, actions);
  dialog.replaceChildren(form);

  const option = (): Option | null => state?.options.find((o) => o.kind === kind) ?? null;
  const remembered = (): State['saved'] => state?.saved ?? state?.last ?? null;

  function setVoices(list: readonly Choice[]): void {
    voiceSelect.replaceChildren(
      ...list.map((c) => {
        const o = el('option', '', c.name);
        o.value = c.id;
        return o;
      }),
    );
    const was = remembered();
    const saved = was?.kind === kind ? was.voice : '';
    if (list.some((c) => c.id === saved)) voiceSelect.value = saved;
    refresh();
  }

  function refresh(): void {
    const o = option();
    for (const input of choices.querySelectorAll<HTMLInputElement>('input')) {
      input.checked = input.value === kind;
      input.disabled = busy;
    }
    keyRow.hidden = !o?.needs_key;
    urlRow.hidden = !o?.takes_url;
    urlInput.disabled = busy;
    voiceRow.hidden = !o;
    keyInput.disabled = busy || !state?.keychain;
    keyInput.placeholder = o?.key ? 'Leave empty to keep the saved key' : 'Paste your key';
    keyHint.textContent = !state?.keychain
      ? 'This computer has no system keychain, so a key cannot be saved here.'
      : o?.key
        ? `A key is saved in your keychain${o.key.ends ? `, ending in ${o.key.ends}` : ''}.`
        : '';
    keyLink.hidden = !o?.key_link;
    if (o?.key_link) keyLink.href = o.key_link;
    forget.hidden = !o?.key;
    check.hidden = o?.where !== 'core';
    sample.hidden = o?.where !== 'client';
    voiceSelect.disabled = busy || voiceSelect.options.length === 0;
    save.disabled = busy || (o?.where === 'core' && !voiceSelect.value);
  }

  function choose(next: string): void {
    kind = next;
    keyInput.value = '';
    const was = remembered();
    urlInput.value = was?.kind === next ? (was.base_url ?? '') : '';
    status.textContent = '';
    const o = option();
    // The computer's own voice costs nothing, whatever an older core says (ONB-10).
    spend.show(o ? (o.cost ?? (o.where === 'client' ? { provider_id: null, name: null, estimate: FREE, pricing_url: null, caps: null } : null)) : null);
    if (o?.where === 'client') {
      const list = 'speechSynthesis' in window ? systemVoices(speechSynthesis.getVoices(), navigator.language) : [];
      setVoices(list);
      if (!list.length) status.textContent = 'This browser has no voices of its own.';
    } else {
      setVoices(o && was?.kind === next && was.voice ? [{ id: was.voice, name: 'Saved voice' }] : []);
    }
  }

  function build(): void {
    choices.replaceChildren();
    const rows = [{ kind: OFF, title: 'Off', note: 'She answers in text only.' }, ...(state?.options ?? [])];
    for (const o of rows) {
      const label = el('label', 'settings-kind');
      const input = el('input');
      input.type = 'radio';
      input.name = 'speech-kind';
      input.value = o.kind;
      input.addEventListener('change', () => choose(o.kind));
      const text = el('span');
      text.append(el('strong', '', o.title), el('small', '', o.note));
      label.append(input, text);
      choices.append(label);
    }
  }

  async function run(work: () => Promise<void>): Promise<void> {
    if (busy) return;
    busy = true;
    refresh();
    try {
      await work();
    } catch {
      status.textContent = 'Trillion did not answer. Is it still running?';
    } finally {
      busy = false;
      refresh();
    }
  }

  async function problem(r: Response): Promise<string> {
    try {
      const body = (await r.json()) as { error?: unknown };
      const o = option();
      return errorText(typeof body.error === 'string' ? body.error : '', o ? speechReach(o) : undefined);
    } catch {
      return errorText('');
    }
  }

  const keyBody = (): Record<string, unknown> => ({
    ...(keyInput.value.trim() ? { api_key: keyInput.value.trim() } : {}),
    ...(option()?.takes_url ? { base_url: urlInput.value.trim() } : {}),
  });

  check.addEventListener('click', () =>
    void run(async () => {
      status.textContent = 'Checking…';
      const r = await api('POST', '/api/settings/speech/voices', { kind, ...keyBody() });
      if (!r.ok) {
        status.textContent = await problem(r);
        return;
      }
      const listed = (await r.json()) as { voices: Choice[]; cost?: Cost };
      const found = listed.voices;
      if (listed.cost) spend.show(listed.cost);
      setVoices(found);
      status.textContent = found.length
        ? `It works. ${found.length} voice${found.length === 1 ? '' : 's'} available.`
        : 'It answered, but this account has no voices.';
    }),
  );
  sample.addEventListener('click', () => {
    if (!('speechSynthesis' in window)) return;
    speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(SAMPLE);
    const chosen = speechSynthesis.getVoices().find((v) => v.voiceURI === voiceSelect.value);
    if (chosen) utterance.voice = chosen;
    speechSynthesis.speak(utterance);
  });
  save.addEventListener('click', () =>
    void run(async () => {
      const wrong = kind === OFF ? null : spend.problem();
      if (wrong) {
        status.textContent = wrong;
        return;
      }
      status.textContent = 'Saving…';
      const body = kind === OFF ? { kind: null } : { kind, voice: voiceSelect.value, ...keyBody() };
      const r = await api('PUT', '/api/settings/speech', body);
      if (!r.ok) {
        status.textContent = await problem(r);
        return;
      }
      state = (await r.json()) as State;
      // The core gave the voice its caps with the key; a change to them follows.
      const capsWrong = kind === OFF ? null : await spend.save(api);
      if (capsWrong) {
        status.textContent = `The voice is saved, but its caps are not: ${capsWrong}`;
        return;
      }
      dialog.close();
    }),
  );
  forget.addEventListener('click', () =>
    void run(async () => {
      const r = await api('POST', '/api/settings/speech/forget-key', { kind });
      if (!r.ok) {
        status.textContent = await problem(r);
        return;
      }
      state = (await r.json()) as State;
      build();
      choose(kind);
      status.textContent = 'The key was removed from your keychain.';
    }),
  );
  close.addEventListener('click', () => dialog.close());
  // A voice list belongs to the address it came from.
  urlInput.addEventListener('input', () => setVoices([]));
  dialog.addEventListener('close', () => {
    keyInput.value = '';
    if ('speechSynthesis' in window) speechSynthesis.cancel();
  });

  return {
    async open() {
      if (dialog.open) return;
      status.textContent = '';
      dialog.showModal();
      await run(async () => {
        const r = await api('GET', '/api/settings/speech');
        if (!r.ok) {
          status.textContent = 'Settings could not be loaded.';
          return;
        }
        state = (await r.json()) as State;
        build();
        choose(state.saved?.kind ?? OFF);
      });
    },
  };
}
