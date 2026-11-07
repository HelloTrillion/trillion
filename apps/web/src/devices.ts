/**
 * Devices (SEC-11, the security alerts spec): this page's own id, sent in its hello,
 * and the Settings section that lists the devices she knows and forgets one.
 *
 * A device is recognised by this id, never by an address. It is random, kept in this
 * browser's storage, and the core keeps only a hash of it. Forgetting is soft: the
 * device's next sign-in is told about, and it comes back.
 */

import type { Host } from './panel';
import type { Api } from './settings';
import { relative } from './when';

const DEVICE_KEY = 'trillion-device';

export interface Device {
  readonly id: string;
  readonly label: string;
  readonly host: string;
  readonly first_seen_ms: number;
  readonly last_seen_ms: number;
}

interface Storageish {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function fresh(random: (a: Uint8Array) => Uint8Array): string {
  const bytes = random(new Uint8Array(24));
  let out = '';
  for (const b of bytes) out += String.fromCharCode(b);
  return btoa(out).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** This page's device id: made once and kept. Null where storage is blocked: the hello then has none. */
export function deviceId(
  storage: Storageish | null = safeStorage(),
  random: (a: Uint8Array) => Uint8Array = (a) => crypto.getRandomValues(a),
): string | null {
  if (!storage) return null;
  try {
    const kept = storage.getItem(DEVICE_KEY);
    if (kept && /^[\w-]{16,128}$/.test(kept)) return kept;
    const made = fresh(random);
    storage.setItem(DEVICE_KEY, made);
    return made;
  } catch {
    return null;
  }
}

function safeStorage(): Storageish | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null;
  }
}

/** The id the core keeps for this page's id: the first 32 hex characters of its SHA-256. */
export async function keptId(raw: string): Promise<string | null> {
  try {
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(raw));
    return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('').slice(0, 32);
  } catch {
    return null;
  }
}

/** One device's line: first and last seen. */
export function seenLine(d: Pick<Device, 'first_seen_ms' | 'last_seen_ms'>, now: Date, mine: boolean): string {
  const last = mine ? 'in use now' : `last seen ${relative(new Date(d.last_seen_ms), now)}`;
  return `First seen ${relative(new Date(d.first_seen_ms), now)} · ${last}`;
}

export interface DevicesSettings {
  open(): Promise<void>;
  summary(): string;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function createDevicesSettings(host: Host, api: Api, now: () => Date = () => new Date()): DevicesSettings {
  let devices: readonly Device[] | null = null;
  const form = el('form', 'settings');
  form.addEventListener('submit', (ev) => ev.preventDefault());
  const title = el('h2', '', 'Devices');
  title.id = 'devices-title';
  host.setAttribute('aria-labelledby', title.id);
  const intro = el(
    'p',
    'settings-note',
    "The browsers and apps that have signed in on this computer. She tells you when a new one does. Forget one you don't recognise: if it signs in again, she tells you again.",
  );
  const list = el('div', 'devices-list');
  const status = el('p', 'settings-status');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  const actions = el('div', 'settings-actions');
  const close = el('button', 'settings-close', 'Close');
  close.type = 'button';
  close.addEventListener('click', () => host.close());
  actions.append(close);
  form.append(title, intro, list, status, actions);
  host.replaceChildren(form);

  async function draw(): Promise<void> {
    const raw = deviceId();
    const mine = raw ? await keptId(raw) : null;
    list.replaceChildren();
    if (!devices || devices.length === 0) {
      list.append(el('p', 'settings-note', 'None yet.'));
      return;
    }
    for (const d of devices) {
      const row = el('div', 'devices-row');
      const words = el('div', 'devices-words');
      const name = el('strong', '', d.label);
      if (d.id === mine) name.append(el('span', 'devices-mine', 'This device'));
      words.append(name, el('small', '', seenLine(d, now(), d.id === mine)));
      const forget = el('button', 'quiet', 'Forget');
      forget.type = 'button';
      forget.setAttribute('aria-label', `Forget ${d.label}`);
      forget.addEventListener('click', () => void drop(d));
      row.append(words, forget);
      list.append(row);
    }
  }

  async function load(): Promise<void> {
    try {
      const r = await api('GET', '/api/devices');
      if (!r.ok) throw new Error(String(r.status));
      const body = (await r.json()) as { devices?: Device[] };
      devices = Array.isArray(body.devices) ? body.devices : [];
      status.textContent = '';
    } catch {
      status.textContent = "The devices couldn't be read.";
    }
    await draw();
  }

  async function drop(d: Device): Promise<void> {
    status.textContent = 'Forgetting…';
    let said = "It couldn't be forgotten.";
    try {
      const r = await api('POST', '/api/devices/forget', { id: d.id });
      if (r.ok) said = `${d.label} forgotten.`;
    } catch {
      // Said below.
    }
    await load();
    status.textContent = said;
  }

  return {
    async open() {
      host.showModal();
      await load();
    },
    summary() {
      if (!devices) return '';
      return devices.length === 1 ? '1 device' : `${devices.length} devices`;
    },
  };
}
