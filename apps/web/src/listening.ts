/**
 * The Mic settings panel: how she hears, or that she does not.
 *
 * Generated from the core's catalog, like the Voice panel, so a new provider
 * appears here without this file changing. A key follows the same rules as
 * every other: sent to the core on this machine, kept in the system keychain,
 * never read back.
 */

import { type Api, errorText } from './settings';
import type { Microphones } from './microphone';
import type { Host } from './panel';

interface Option {
  readonly kind: string;
  readonly title: string;
  readonly note: string;
  readonly needs_key: boolean;
  readonly free: boolean;
  readonly key_link: string | null;
  readonly install_hint: string | null;
  readonly key: { readonly saved: boolean; readonly ends: string } | null;
}

interface State {
  readonly options: readonly Option[];
  readonly saved: { readonly kind: string; readonly choice: string } | null;
  /** The choice made before listening was switched off, offered again when it comes back on. */
  readonly last?: { readonly kind: string; readonly choice: string } | null;
  readonly keychain: boolean;
}

export interface Choice {
  readonly id: string;
  readonly name: string;
  /** Bytes still to download before it can be used, or null when it is ready. */
  readonly download: number | null;
}

const OFF = '';

/** "147 MB", for a download the user is about to agree to. */
export function megabytes(bytes: number): string {
  return `${Math.max(1, Math.round(bytes / 1_000_000))} MB`;
}

export function choiceLabel(c: Choice): string {
  return c.download === null ? c.name : `${c.name} (${megabytes(c.download)} download)`;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

export interface ListeningSettings {
  open(): Promise<void>;
}

export function createListeningSettings(dialog: Host, api: Api, microphones?: Microphones): ListeningSettings {
  let state: State | null = null;
  let kind = OFF;
  let found: readonly Choice[] = [];
  let busy = false;

  const form = el('form', 'settings');
  form.method = 'dialog';
  const title = el('h2', '', 'Her hearing');
  title.id = 'listening-title';
  dialog.setAttribute('aria-labelledby', title.id);
  const intro = el(
    'p',
    'settings-note',
    'Choose how she hears you. The microphone is only on while you are talking with her: after you press Speak, while she answers you out loud (so you can cut in, unless you turn that off below), and for a few seconds after.',
  );
  const choices = el('div', 'settings-kinds');
  choices.setAttribute('role', 'radiogroup');
  choices.setAttribute('aria-label', 'Listening provider');

  const keyRow = el('div', 'settings-row');
  const keyLabel = el('label', '', 'API key');
  const keyInput = el('input');
  keyInput.type = 'password';
  keyInput.id = 'listening-key';
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

  const pickRow = el('div', 'settings-row');
  const pickLabel = el('label', '', 'Language and model');
  const pick = el('select');
  pick.id = 'listening-choice';
  pickLabel.htmlFor = pick.id;
  const check = el('button', '', 'Check');
  check.type = 'button';
  const fetchBtn = el('button', '', 'Download');
  fetchBtn.type = 'button';
  pickRow.append(pickLabel, pick, check, fetchBtn);

  const status = el('p', 'settings-status');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  const actions = el('div', 'settings-actions');
  const save = el('button', 'approve', 'Save');
  save.type = 'button';
  const close = el('button', '', 'Close');
  close.type = 'button';
  actions.append(save, close);
  form.append(title, intro, choices, keyRow, pickRow, status, ...(microphones ? [microphones.element] : []), actions);
  dialog.replaceChildren(form);

  const option = (): Option | null => state?.options.find((o) => o.kind === kind) ?? null;
  const picked = (): Choice | null => found.find((c) => c.id === pick.value) ?? null;

  function setChoices(list: readonly Choice[]): void {
    const keep = pick.value;
    found = list;
    pick.replaceChildren(
      ...list.map((c) => {
        const o = el('option', '', choiceLabel(c));
        o.value = c.id;
        return o;
      }),
    );
    const was = state?.saved ?? state?.last ?? null;
    const saved = was?.kind === kind ? was.choice : '';
    const want = [keep, saved].find((id) => id && list.some((c) => c.id === id));
    if (want) pick.value = want;
    refresh();
  }

  function refresh(): void {
    const o = option();
    for (const input of choices.querySelectorAll<HTMLInputElement>('input')) {
      input.checked = input.value === kind;
      input.disabled = busy;
    }
    keyRow.hidden = !o?.needs_key;
    pickRow.hidden = !o;
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
    const needsDownload = picked()?.download != null;
    check.hidden = !o?.needs_key;
    check.disabled = busy;
    fetchBtn.hidden = !needsDownload;
    fetchBtn.disabled = busy;
    pick.disabled = busy || found.length === 0;
    save.disabled = busy || (o !== null && (!pick.value || needsDownload));
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
    let code = '';
    try {
      const body = (await r.json()) as { error?: unknown };
      code = typeof body.error === 'string' ? body.error : '';
    } catch {
      // fall through to the general message
    }
    const hint = option()?.install_hint;
    if (code === 'not_installed' && hint) {
      return `This part of Trillion is not installed yet. In the Trillion folder, run "${hint}", then start Trillion again.`;
    }
    return errorText(code);
  }

  const keyBody = (): Record<string, unknown> => (keyInput.value.trim() ? { api_key: keyInput.value.trim() } : {});

  async function list(): Promise<void> {
    status.textContent = 'Checking…';
    const r = await api('POST', '/api/settings/listening/choices', { kind, ...keyBody() });
    if (!r.ok) {
      setChoices([]);
      status.textContent = await problem(r);
      return;
    }
    setChoices(((await r.json()) as { choices: Choice[] }).choices);
    status.textContent = option()?.needs_key ? 'It works.' : '';
  }

  function choose(next: string): void {
    kind = next;
    keyInput.value = '';
    status.textContent = '';
    setChoices([]);
    const o = option();
    // Nothing to paste, or a key already saved: say at once what is available.
    if (o && (!o.needs_key || o.key)) void run(list);
  }

  function build(): void {
    choices.replaceChildren();
    const rows = [{ kind: OFF, title: 'Off', note: 'She cannot hear you. You type.' }, ...(state?.options ?? [])];
    for (const o of rows) {
      const label = el('label', 'settings-kind');
      const input = el('input');
      input.type = 'radio';
      input.name = 'listening-kind';
      input.value = o.kind;
      input.addEventListener('change', () => choose(o.kind));
      const text = el('span');
      text.append(el('strong', '', o.title), el('small', '', o.note));
      label.append(input, text);
      choices.append(label);
    }
  }

  pick.addEventListener('change', refresh);
  check.addEventListener('click', () => void run(list));
  fetchBtn.addEventListener('click', () =>
    void run(async () => {
      const c = picked();
      if (!c || c.download === null) return;
      status.textContent = `Downloading ${megabytes(c.download)}. This happens once; keep this window open.`;
      const r = await api('POST', '/api/settings/listening/prepare', { kind, choice: c.id });
      if (!r.ok) {
        status.textContent = await problem(r);
        return;
      }
      setChoices(((await r.json()) as { choices: Choice[] }).choices);
      status.textContent = 'Downloaded and checked. Press Save to use it.';
    }),
  );
  save.addEventListener('click', () =>
    void run(async () => {
      status.textContent = 'Saving…';
      const body = kind === OFF ? { kind: null } : { kind, choice: pick.value, ...keyBody() };
      const r = await api('PUT', '/api/settings/listening', body);
      if (!r.ok) {
        status.textContent = await problem(r);
        return;
      }
      state = (await r.json()) as State;
      dialog.close();
    }),
  );
  forget.addEventListener('click', () =>
    void run(async () => {
      const r = await api('POST', '/api/settings/listening/forget-key', { kind });
      if (!r.ok) {
        status.textContent = await problem(r);
        return;
      }
      state = (await r.json()) as State;
      build();
      setChoices([]);
      status.textContent = 'The key was removed from your keychain.';
    }),
  );
  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {
    keyInput.value = '';
    microphones?.stopTest();
  });

  return {
    async open() {
      if (dialog.open) return;
      status.textContent = '';
      dialog.showModal();
      void microphones?.refresh();
      let loaded = false;
      await run(async () => {
        const r = await api('GET', '/api/settings/listening');
        if (!r.ok) {
          status.textContent = 'Settings could not be loaded.';
          return;
        }
        state = (await r.json()) as State;
        build();
        loaded = true;
      });
      if (loaded) choose(state?.saved?.kind ?? OFF);
    },
  };
}
