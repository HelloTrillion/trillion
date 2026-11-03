// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { createListeningSettings } from '../src/listening';
import type { Host } from '../src/panel';

const flush = async (): Promise<void> => {
  for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 0));
};

const json = (body: unknown, status = 200): Response => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

function fakeHost(root: HTMLElement): Host {
  const listeners: (() => void)[] = [];
  const host = {
    open: false,
    showModal() {
      host.open = true;
    },
    close() {
      host.open = false;
      for (const l of listeners) l();
    },
    replaceChildren: (...nodes: (Node | string)[]) => root.replaceChildren(...nodes),
    setAttribute: (name: string, value: string) => root.setAttribute(name, value),
    addEventListener: (_: 'close', l: () => void) => listeners.push(l),
  };
  return host;
}

const FLUX = {
  kind: 'deepgram',
  title: 'Deepgram',
  note: '',
  needs_key: true,
  free: false,
  turns: true,
  key_link: null,
  install_hint: null,
  key: { saved: true, ends: 'abcd' },
};

interface Call {
  method: string;
  path: string;
  body: unknown;
}

async function setup(over: Record<string, unknown> = {}) {
  const root = document.createElement('div');
  document.body.append(root);
  let state = { options: [FLUX], saved: { kind: 'deepgram', choice: 'flux' }, think_early: true, keychain: true, cues: true, cues_possible: true, ...over };
  const calls: Call[] = [];
  const api = (method: string, path: string, body?: unknown): Promise<Response> => {
    calls.push({ method, path, body });
    if (path === '/api/settings/listening' && method === 'GET') return Promise.resolve(json(state));
    if (path === '/api/settings/listening/cues') {
      state = { ...state, cues: (body as { on: boolean }).on };
      return Promise.resolve(json(state));
    }
    if (path === '/api/settings/listening/choices') return Promise.resolve(json({ choices: [{ id: 'flux', name: 'Flux', download: null }] }));
    return Promise.resolve(json({ error: 'not_found' }, 404));
  };
  const host = fakeHost(root);
  const panel = createListeningSettings(host, api);
  await panel.open();
  await flush();
  const box = root.querySelector('#listening-cues') as HTMLInputElement;
  const row = box.closest('label') as HTMLElement;
  const change = (next: Record<string, unknown>): void => {
    state = { ...state, ...next };
  };
  return { root, calls, box, row, host, panel, change };
}

afterEach(() => {
  document.body.replaceChildren();
});

describe('the listening cues switch', () => {
  it('is offered when cues are possible, with what it does', async () => {
    const { box, row } = await setup();
    expect(row.hidden).toBe(false);
    expect(box.checked).toBe(true);
    expect(row.textContent).toContain("Say 'mm-hm' while I talk");
    expect(row.textContent).toContain('Short listening sounds in her voice while you think aloud.');
  });

  it('is hidden when cues are not possible', async () => {
    const { row } = await setup({ cues_possible: false });
    expect(row.hidden).toBe(true);
  });

  it('follows a change made elsewhere (her voice) when Hearing is shown again', async () => {
    const { row, host, panel, change } = await setup({ cues_possible: false });
    expect(row.hidden).toBe(true);
    host.close(); // another section, such as Voice
    change({ cues_possible: true });
    await panel.open();
    await flush();
    expect(row.hidden).toBe(false);
  });

  it('is hidden while a provider that does not judge turns is picked', async () => {
    const whisper = { ...FLUX, kind: 'whisper', title: 'Whisper', needs_key: false, turns: false, key: null };
    const { root, row } = await setup({ options: [FLUX, whisper] });
    const radio = root.querySelector('input[value="whisper"]') as HTMLInputElement;
    radio.checked = true;
    radio.dispatchEvent(new Event('change'));
    await flush();
    expect(row.hidden).toBe(true);
  });

  it('shows the saved choice', async () => {
    const { box } = await setup({ cues: false });
    expect(box.checked).toBe(false);
  });

  it('saves on change', async () => {
    const { box, calls } = await setup();
    box.checked = false;
    box.dispatchEvent(new Event('change'));
    await flush();
    expect(calls.filter((c) => c.method === 'PUT')).toEqual([{ method: 'PUT', path: '/api/settings/listening/cues', body: { on: false } }]);
    expect(box.checked).toBe(false);
  });
});
