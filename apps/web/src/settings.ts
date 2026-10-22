/**
 * The Model settings panel: pick who answers and give it a key.
 *
 * A key typed here goes to the core on this machine and into the system
 * keychain. It is never read back: the page only learns that a key exists and
 * how it ends. Everything is written with textContent, never as markup.
 */

import type { Host } from './panel';

export type Kind = 'anthropic' | 'openai' | 'local' | 'custom';

export interface ModelState {
  readonly source: 'env' | 'settings' | 'none';
  readonly active: { readonly provider: string; readonly model: string } | null;
  readonly saved: { readonly kind: Kind; readonly model: string; readonly base_url: string | null } | null;
  readonly keys: Readonly<Partial<Record<Kind, { readonly saved: boolean; readonly ends: string } | null>>>;
  readonly keychain: boolean;
  readonly defaults: { readonly local: string };
}

interface Choice {
  readonly id: string;
  readonly label: string;
}

export type Api = (method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE', path: string, body?: unknown) => Promise<Response>;

interface Option {
  readonly kind: Kind;
  readonly title: string;
  readonly note: string;
  readonly takesKey: boolean;
  readonly needsKey: boolean;
  readonly takesAddress: boolean;
  readonly keyLink?: string;
}

export const OPTIONS: readonly Option[] = [
  {
    kind: 'anthropic',
    title: 'Anthropic (Claude)',
    note: 'Recommended. Needs an Anthropic API key; you pay Anthropic for what you use.',
    takesKey: true,
    needsKey: true,
    takesAddress: false,
    keyLink: 'https://console.anthropic.com/settings/keys',
  },
  {
    kind: 'openai',
    title: 'OpenAI',
    note: 'Needs an OpenAI API key; you pay OpenAI for what you use.',
    takesKey: true,
    needsKey: true,
    takesAddress: false,
    keyLink: 'https://platform.openai.com/api-keys',
  },
  {
    kind: 'local',
    title: 'A model on this computer',
    note: 'Free and private. Works with Ollama or LM Studio running on this machine. No key.',
    takesKey: false,
    needsKey: false,
    takesAddress: true,
  },
  {
    kind: 'custom',
    title: 'Another compatible service',
    note: 'Any service that speaks the OpenAI chat format, such as OpenRouter, or a model server on another computer on your network.',
    takesKey: true,
    needsKey: false,
    takesAddress: true,
  },
];

const ERRORS: Readonly<Record<string, string>> = {
  key_required: 'Paste a key first.',
  unauthorized: 'The provider rejected that key. Check it was copied whole.',
  forbidden: 'That key is not allowed to do this.',
  rate_limited: 'The provider is rate-limiting requests. Try again in a moment.',
  unreachable: 'Could not reach it. Is it running, and is the address right?',
  timeout: 'It took too long to answer.',
  not_found: 'Nothing answered at that address. It usually ends in /v1.',
  provider_unavailable: 'The provider is having trouble right now.',
  bad_response: 'Something answered, but not with a list of models.',
  bad_url: 'That address is not a web address.',
  not_local: 'That address is not on this computer. Choose "Another compatible service" instead.',
  insecure_url:
    'An address on another machine must start with https. The one exception is a numeric address on your own network, such as http://192.168.1.20:8080/v1.',
  key_needs_https: 'A key is only sent over https. Clear the key, or use an https address.',
  unknown_model: 'That model is not offered. Check again and pick from the list.',
  no_keychain: 'This computer has no system keychain, so a key cannot be saved here.',
  keychain_refused: 'The system keychain refused. Unlock it and try again.',
  unknown_voice: 'That voice is not offered. Check again and pick from the list.',
  unknown_choice: 'That choice is not offered. Check again and pick from the list.',
  needs_download: 'That one needs its download first. Press Download.',
  not_installed: 'This part of Trillion is not installed yet.',
  download_failed: 'The download could not be fetched. Check the connection and try again.',
  download_mismatch: 'The downloaded file did not match what was expected, so it was thrown away. Try again.',
  disk: 'The file could not be written. Is the disk full?',
  payment_required: 'The provider says this account is out of credit.',
  unknown_provider: 'That provider is not available in this version.',
  set_by_environment: 'The model is set by environment variables, so it cannot be changed here.',
};

export function errorText(code: string): string {
  return ERRORS[code] ?? 'That did not work. Try again.';
}

/** The model to preselect: the one already saved, then the recommended default, then the first. */
export function preferred(choices: readonly Choice[], saved: string | null): string {
  const ids = choices.map((c) => c.id);
  if (saved && ids.includes(saved)) return saved;
  if (ids.includes('claude-opus-5-5')) return 'claude-opus-5-5';
  return ids[0] ?? '';
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function problem(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { error?: unknown };
    return errorText(typeof body.error === 'string' ? body.error : '');
  } catch {
    return errorText('');
  }
}

export interface Settings {
  open(): Promise<void>;
}

export function createSettings(dialog: Host, api: Api): Settings {
  let state: ModelState | null = null;
  let kind: Kind = 'anthropic';
  let busy = false;

  const form = el('form', 'settings');
  form.method = 'dialog';
  const title = el('h2', '', 'Who answers');
  title.id = 'settings-title';
  dialog.setAttribute('aria-labelledby', title.id);
  const intro = el(
    'p',
    'settings-note',
    'Your key is kept in this computer’s keychain and sent only to the provider you choose. It never reaches the makers of Trillion: there is no server of ours to send it to.',
  );
  const lock = el('p', 'settings-warn');
  const choices = el('div', 'settings-kinds');
  choices.setAttribute('role', 'radiogroup');
  choices.setAttribute('aria-label', 'Provider');

  const keyRow = el('div', 'settings-row');
  const keyLabel = el('label', '', 'API key');
  const keyInput = el('input');
  keyInput.type = 'password';
  keyInput.id = 'settings-key';
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

  const addressRow = el('div', 'settings-row');
  const addressLabel = el('label', '', 'Address');
  const addressInput = el('input');
  addressInput.type = 'url';
  addressInput.id = 'settings-address';
  addressInput.autocomplete = 'off';
  addressInput.spellcheck = false;
  addressLabel.htmlFor = addressInput.id;
  addressRow.append(addressLabel, addressInput);

  const modelRow = el('div', 'settings-row');
  const modelLabel = el('label', '', 'Model');
  const modelSelect = el('select');
  modelSelect.id = 'settings-model';
  modelLabel.htmlFor = modelSelect.id;
  const check = el('button', '', 'Check and list models');
  check.type = 'button';
  modelRow.append(modelLabel, modelSelect, check);

  const status = el('p', 'settings-status');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');

  const actions = el('div', 'settings-actions');
  const save = el('button', 'approve', 'Save');
  save.type = 'button';
  const close = el('button', '', 'Close');
  close.type = 'button';
  actions.append(save, close);

  form.append(title, intro, lock, choices, keyRow, addressRow, modelRow, status, actions);
  dialog.replaceChildren(form);

  const option = (): Option => OPTIONS.find((o) => o.kind === kind) ?? (OPTIONS[0] as Option);
  const readOnly = (): boolean => state?.source === 'env';

  function setModels(list: readonly Choice[]): void {
    modelSelect.replaceChildren(
      ...list.map((c) => {
        const o = el('option', '', c.label === c.id ? c.id : `${c.label} (${c.id})`);
        o.value = c.id;
        return o;
      }),
    );
    modelSelect.value = preferred(list, state?.saved?.kind === kind ? state.saved.model : null);
    refresh();
  }

  function refresh(): void {
    const o = option();
    const saved = state?.keys[kind] ?? null;
    const locked = readOnly();
    for (const input of choices.querySelectorAll<HTMLInputElement>('input')) {
      input.checked = input.value === kind;
      input.disabled = locked || busy;
    }
    keyRow.hidden = !o.takesKey || locked;
    addressRow.hidden = !o.takesAddress || locked;
    modelRow.hidden = locked;
    keyInput.disabled = busy || !state?.keychain;
    keyInput.placeholder = saved ? 'Leave empty to keep the saved key' : o.needsKey ? 'Paste your key' : 'Optional';
    keyHint.textContent = saved
      ? `A key is saved in your keychain${saved.ends ? `, ending in ${saved.ends}` : ''}.`
      : '';
    keyLink.hidden = !o.keyLink;
    if (o.keyLink) keyLink.href = o.keyLink;
    forget.hidden = !saved;
    forget.disabled = busy;
    addressInput.disabled = busy;
    check.disabled = busy;
    modelSelect.disabled = busy || modelSelect.options.length === 0;
    save.hidden = locked;
    save.disabled = busy || !modelSelect.value;
    lock.hidden = !(locked || (state !== null && !state.keychain));
    lock.textContent = locked
      ? `The model is set by environment variables (${state?.active?.model ?? 'unknown'}). Unset TRILLION_ANTHROPIC_API_KEY and TRILLION_OPENAI_BASE_URL to choose here instead.`
      : 'This computer has no system keychain, so a key cannot be saved here. A model on this computer needs no key.';
  }

  function choose(next: Kind): void {
    kind = next;
    keyInput.value = '';
    status.textContent = '';
    const saved = state?.saved;
    addressInput.value =
      saved?.kind === next && saved.base_url ? saved.base_url : next === 'local' ? (state?.defaults.local ?? '') : '';
    setModels(saved?.kind === next ? [{ id: saved.model, label: saved.model }] : []);
  }

  function body(): Record<string, unknown> {
    const o = option();
    return {
      kind,
      ...(o.takesAddress && addressInput.value.trim() ? { base_url: addressInput.value.trim() } : {}),
      ...(o.takesKey && keyInput.value.trim() ? { api_key: keyInput.value.trim() } : {}),
    };
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

  async function listModels(): Promise<boolean> {
    status.textContent = 'Checking…';
    const r = await api('POST', '/api/settings/model/models', body());
    if (!r.ok) {
      status.textContent = await problem(r);
      return false;
    }
    const found = ((await r.json()) as { models: Choice[] }).models;
    setModels(found);
    status.textContent = !found.length
      ? 'It answered, but offers no models.'
      : `It works. ${found.length} model${found.length === 1 ? '' : 's'} available.`;
    return found.length > 0;
  }

  for (const o of OPTIONS) {
    const label = el('label', 'settings-kind');
    const input = el('input');
    input.type = 'radio';
    input.name = 'kind';
    input.value = o.kind;
    input.addEventListener('change', () => choose(o.kind));
    const text = el('span');
    text.append(el('strong', '', o.title), el('small', '', o.note));
    label.append(input, text);
    choices.append(label);
  }

  check.addEventListener('click', () => void run(async () => void (await listModels())));
  keyInput.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      check.click();
    }
  });
  save.addEventListener('click', () =>
    void run(async () => {
      status.textContent = 'Saving…';
      const r = await api('PUT', '/api/settings/model', { ...body(), model: modelSelect.value });
      if (!r.ok) {
        status.textContent = await problem(r);
        return;
      }
      state = (await r.json()) as ModelState;
      keyInput.value = '';
      dialog.close();
    }),
  );
  forget.addEventListener('click', () =>
    void run(async () => {
      const r = await api('POST', '/api/settings/model/forget-key', { kind });
      if (!r.ok) {
        status.textContent = await problem(r);
        return;
      }
      state = (await r.json()) as ModelState;
      status.textContent = 'The key was removed from your keychain.';
    }),
  );
  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {
    keyInput.value = '';
  });

  return {
    async open() {
      if (dialog.open) return;
      status.textContent = '';
      dialog.showModal();
      await run(async () => {
        const r = await api('GET', '/api/settings/model');
        if (!r.ok) {
          status.textContent = 'Settings could not be loaded.';
          return;
        }
        state = (await r.json()) as ModelState;
        choose(state.saved?.kind ?? 'anthropic');
      });
    },
  };
}
