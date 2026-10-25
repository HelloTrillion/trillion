/**
 * The Names section: what she calls the user, what the user calls her, and her tone.
 *
 * The same privileged path onboarding uses (`PUT /api/settings/persona`); the model
 * never reaches it. "Change them by talking" runs the conversational version instead.
 */

import type { Host } from './panel';
import type { Api } from './settings';

type Directness = 'blunt' | 'balanced' | 'gentle';

interface Persona {
  readonly onboarding: string;
  readonly user_name: string | null;
  readonly agent_name: string;
  readonly timezone: string;
  readonly directness: Directness;
}

const PROBLEMS: Readonly<Record<string, string>> = {
  name_empty: 'Both names need something in them.',
  name_too_long: 'A name can be at most 40 characters.',
  name_not_allowed: 'A name cannot contain braces or angle brackets, or start with a quote, dash or #.',
  timezone_not_allowed: "That timezone doesn't look like one, such as America/Toronto.",
  timezone_unknown: "That isn't a timezone this computer knows, such as America/Toronto.",
  not_saved: "The data folder couldn't be written.",
};

export interface NamesSettings {
  open(): Promise<void>;
  summary(): string;
  /** Learn the current names without showing anything, for the summary beside "Names". */
  peek(): Promise<void>;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function field(id: string, label: string, control: HTMLInputElement | HTMLSelectElement): HTMLElement {
  const row = el('div', 'settings-row');
  const l = el('label', '', label);
  control.id = id;
  l.htmlFor = id;
  row.append(l, control);
  return row;
}

export function createNamesSettings(
  host: Host,
  api: Api,
  hooks: { readonly talk: () => void; readonly saved: (agentName: string) => void },
): NamesSettings {
  let state: Persona | null = null;
  const form = el('form', 'settings');
  form.addEventListener('submit', (ev) => ev.preventDefault());
  const title = el('h2', '', 'Names');
  title.id = 'names-title';
  host.setAttribute('aria-labelledby', title.id);
  const intro = el(
    'p',
    'settings-note',
    'Kept in two small files in your Trillion data folder. Only you can change them, here or by hand; nothing she does can.',
  );
  const you = el('input');
  you.autocomplete = 'off';
  you.maxLength = 40;
  const her = el('input');
  her.autocomplete = 'off';
  her.maxLength = 40;
  const tone = el('select');
  for (const [value, label] of [
    ['blunt', 'Blunt'],
    ['balanced', 'Balanced'],
    ['gentle', 'Gentler'],
  ] as const) {
    const o = el('option', '', label);
    o.value = value;
    tone.append(o);
  }
  const zone = el('input');
  zone.autocomplete = 'off';
  zone.placeholder = 'For example America/Toronto';
  const status = el('p', 'settings-status');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  const actions = el('div', 'settings-actions');
  const save = el('button', 'approve', 'Save');
  save.type = 'button';
  const talk = el('button', '', 'Change them by talking');
  talk.type = 'button';
  const close = el('button', 'settings-close', 'Close');
  close.type = 'button';
  actions.append(save, talk, close);
  form.append(
    title,
    intro,
    field('names-you', 'Your name', you),
    field('names-her', 'Her name', her),
    field('names-tone', 'How blunt she is', tone),
    field('names-zone', 'Your timezone', zone),
    status,
    actions,
  );
  host.replaceChildren(form);

  function fill(p: Persona): void {
    you.value = p.user_name ?? '';
    her.value = p.agent_name;
    tone.value = p.directness;
    zone.value = p.timezone;
  }

  save.addEventListener('click', () => {
    void (async () => {
      status.textContent = 'Saving…';
      const r = await api('PUT', '/api/settings/persona', {
        user_name: you.value,
        agent_name: her.value,
        timezone: zone.value.trim(),
        directness: tone.value,
      }).catch(() => null);
      if (!r) {
        status.textContent = 'Trillion is not running.';
        return;
      }
      const body = (await r.json().catch(() => ({}))) as Partial<Persona> & { error?: string; suggestion?: string };
      if (!r.ok) {
        const why = PROBLEMS[body.error ?? ''] ?? 'That could not be saved.';
        status.textContent = body.suggestion ? `${why} Did you mean ${body.suggestion}?` : why;
        return;
      }
      state = body as Persona;
      fill(state);
      status.textContent = 'Saved. She uses these from your next message.';
      hooks.saved(state.agent_name);
    })();
  });
  talk.addEventListener('click', () => {
    host.close();
    hooks.talk();
  });
  close.addEventListener('click', () => host.close());

  return {
    async open() {
      if (host.open) return;
      status.textContent = '';
      host.showModal();
      const r = await api('GET', '/api/settings/persona').catch(() => null);
      if (!r?.ok) {
        status.textContent = 'Settings could not be loaded.';
        return;
      }
      state = (await r.json()) as Persona;
      fill(state);
    },
    summary() {
      return state?.user_name ? `${state.user_name} and ${state.agent_name}` : '';
    },
    async peek() {
      const r = await api('GET', '/api/settings/persona').catch(() => null);
      if (r?.ok) state = (await r.json()) as Persona;
    },
  };
}
