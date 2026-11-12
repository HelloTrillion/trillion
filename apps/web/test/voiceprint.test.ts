// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import type { Host } from '../src/panel';
import {
  type Recorder,
  type VoiceprintState,
  OWN_WORDS_LABEL,
  createVoiceprintSettings,
  encodePhrase,
  nextPhrase,
  problemText,
  seconds,
  showsOwnWords,
  statusText,
} from '../src/voiceprint';

const flush = async (): Promise<void> => {
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0));
};

const BASE: VoiceprintState = {
  installed: true,
  model: { ready: true, size: 26_530_309, credit: 'A model (CC BY 4.0)', licence_url: 'https://creativecommons.org/licenses/by/4.0/' },
  enrolled: false,
  enrolled_ms: null,
  working: false,
  prompts: ['one', 'two', 'three'],
  recorded: [],
};

function fakeHost(root: HTMLElement): Host {
  const host = {
    open: false,
    showModal() {
      host.open = true;
    },
    close() {
      host.open = false;
    },
    replaceChildren: (...nodes: (Node | string)[]) => root.replaceChildren(...nodes),
    setAttribute: (name: string, value: string) => root.setAttribute(name, value),
    addEventListener: () => undefined,
  };
  return host;
}

interface Call {
  method: string;
  path: string;
  body: unknown;
}

function server(initial: VoiceprintState, answers: Record<string, unknown[]> = {}) {
  let state = initial;
  const calls: Call[] = [];
  const api = async (method: string, path: string, body?: unknown): Promise<Response> => {
    calls.push({ method, path, body });
    const queued = answers[`${method} ${path}`];
    if (queued && queued.length) {
      const next = queued.shift() as Record<string, unknown>;
      if ('recorded' in next && Object.keys(next).length > 3) state = next as unknown as VoiceprintState;
      return new Response(JSON.stringify(next), { status: 'error' in next ? 400 : 200 });
    }
    if (method === 'DELETE' && path === '/api/voiceprint') state = { ...state, enrolled: false, working: false };
    if (method === 'DELETE' && path === '/api/voiceprint/draft') state = { ...state, recorded: [] };
    return new Response(JSON.stringify(state), { status: 200 });
  };
  return { api, calls };
}

function recorder(frames: ArrayBuffer[]): Recorder & { started: number; stopped: number } {
  const r = {
    started: 0,
    stopped: 0,
    async start(onFrame: (pcm: ArrayBuffer) => void) {
      r.started++;
      for (const f of frames) onFrame(f);
    },
    stop() {
      r.stopped++;
    },
  };
  return r;
}

function buttons(root: HTMLElement): Map<string, HTMLButtonElement> {
  const out = new Map<string, HTMLButtonElement>();
  for (const b of root.querySelectorAll('button')) if (!b.hidden) out.set(b.textContent ?? '', b);
  return out;
}

const second = (): ArrayBuffer => new ArrayBuffer(32_000);

describe('your voice: words', () => {
  it('says where things stand', () => {
    expect(statusText(null)).toBe('');
    expect(statusText(BASE)).toBe('Not set up');
    expect(statusText({ ...BASE, enrolled: true, working: true })).toBe('She knows your voice');
    expect(statusText({ ...BASE, enrolled: true, working: false })).toMatch(/model is missing/);
  });

  it('says what was wrong with a phrase in plain words', () => {
    for (const code of ['quiet', 'short', 'loud', 'voices']) expect(problemText(code)).not.toMatch(/did not work/);
    expect(problemText('something-new')).toMatch(/did not work/);
  });

  it('goes through the phrases in order, filling gaps first', () => {
    expect(nextPhrase([], 3)).toBe(0);
    expect(nextPhrase([0, 2], 3)).toBe(1);
    expect(nextPhrase([0, 1, 2], 3)).toBeNull();
  });

  it('encodes frames as the core reads them', () => {
    const a = new Uint8Array([1, 0, 2, 0]).buffer;
    const b = new Uint8Array([3, 0]).buffer;
    expect(encodePhrase([a, b])).toBe(btoa(String.fromCharCode(1, 0, 2, 0, 3, 0)));
    expect(seconds([second(), second()])).toBe(2);
  });
});

describe('your voice: the section', () => {
  it('offers the download first, with its size and the licence', async () => {
    const root = document.createElement('div');
    const { api, calls } = server({ ...BASE, model: { ...BASE.model, ready: false } });
    const section = createVoiceprintSettings(fakeHost(root), api, recorder([]));
    await section.open();
    const shown = buttons(root);
    expect([...shown.keys()]).toContain('Get the voice model (27 MB download)');
    expect(shown.has('Teach her your voice')).toBe(false);
    expect(root.textContent).toMatch(/CC BY 4\.0/);
    expect(root.querySelector('a')?.getAttribute('href')).toBe(BASE.model.licence_url);
    expect(calls.every((c) => c.method === 'GET')).toBe(true); // nothing fetched until asked
  });

  it('says how to install the optional part when it is missing', async () => {
    const root = document.createElement('div');
    const { api } = server({ ...BASE, installed: false, model: { ...BASE.model, ready: false } });
    const section = createVoiceprintSettings(fakeHost(root), api, recorder([]));
    await section.open();
    expect(root.textContent).toMatch(/uv sync --extra speaker/);
    expect(buttons(root).has('Teach her your voice')).toBe(false);
  });

  it('records three phrases and saves', async () => {
    const root = document.createElement('div');
    const changed = vi.fn();
    const { api, calls } = server(BASE, {
      'POST /api/voiceprint/phrase': [
        { ok: false, problem: 'quiet', recorded: [] },
        { ok: true, problem: null, recorded: [0] },
        { ok: true, problem: null, recorded: [0, 1] },
        { ok: true, problem: null, recorded: [0, 1, 2] },
      ],
      'POST /api/voiceprint/save': [{ ...BASE, ok: true, enrolled: true, working: true, recorded: [] }],
    });
    const mic = recorder([second(), second()]);
    const section = createVoiceprintSettings(fakeHost(root), api, mic, { changed });
    await section.open();
    buttons(root).get('Teach her your voice')!.click();
    await flush();
    expect(root.textContent).toMatch(/Phrase 1 of 3/);
    for (let i = 0; i < 4; i++) {
      buttons(root).get('Record')!.click();
      await flush();
      buttons(root).get('Stop')!.click();
      await flush();
      if (i === 0) expect(root.textContent).toMatch(/too quiet/);
    }
    const phrases = calls.filter((c) => c.path === '/api/voiceprint/phrase');
    expect(phrases.map((c) => (c.body as { index: number }).index)).toEqual([0, 0, 1, 2]);
    expect(calls.some((c) => c.path === '/api/voiceprint/save')).toBe(true);
    expect(root.textContent).toMatch(/She knows your voice now/);
    expect(section.summary()).toBe('She knows your voice');
    expect(mic.stopped).toBeGreaterThanOrEqual(4);
    expect(changed).toHaveBeenCalled();
  });

  it('does not send a recording too short to be worth checking', async () => {
    const root = document.createElement('div');
    const { api, calls } = server(BASE);
    const section = createVoiceprintSettings(fakeHost(root), api, recorder([new ArrayBuffer(3200)]));
    await section.open();
    buttons(root).get('Teach her your voice')!.click();
    await flush();
    buttons(root).get('Record')!.click();
    await flush();
    buttons(root).get('Stop')!.click();
    await flush();
    expect(calls.some((c) => c.path === '/api/voiceprint/phrase')).toBe(false);
    expect(root.textContent).toMatch(/too short/);
  });

  it('asks which phrase to read again when one does not match', async () => {
    const root = document.createElement('div');
    const { api } = server(
      { ...BASE, recorded: [0, 1] },
      {
        'DELETE /api/voiceprint/draft': [{ ...BASE, recorded: [0, 1] }],
        'POST /api/voiceprint/phrase': [{ ok: true, problem: null, recorded: [0, 1, 2] }],
        'POST /api/voiceprint/save': [{ ok: false, problem: 'voices', phrase: 1, recorded: [0, 2] }],
      },
    );
    const section = createVoiceprintSettings(fakeHost(root), api, recorder([second(), second()]));
    await section.open();
    buttons(root).get('Teach her your voice')!.click();
    await flush();
    buttons(root).get('Record')!.click();
    await flush();
    buttons(root).get('Stop')!.click();
    await flush();
    expect(root.textContent).toMatch(/Read phrase 2 again/);
    expect(root.textContent).toMatch(/Phrase 2 of 3/);
  });

  it('forgets the voice only when the user says so', async () => {
    const root = document.createElement('div');
    const { api, calls } = server({ ...BASE, enrolled: true, working: true });
    let answer = false;
    const section = createVoiceprintSettings(fakeHost(root), api, recorder([]), { changed: () => {}, confirm: () => answer });
    await section.open();
    expect(buttons(root).has('Teach her again')).toBe(true);
    buttons(root).get('Forget my voice')!.click();
    await flush();
    expect(calls.some((c) => c.method === 'DELETE' && c.path === '/api/voiceprint')).toBe(false);
    answer = true;
    buttons(root).get('Forget my voice')!.click();
    await flush();
    expect(calls.some((c) => c.method === 'DELETE' && c.path === '/api/voiceprint')).toBe(true);
    expect(section.summary()).toBe('Not set up');
  });

  it('every write sends a body, as the core requires', async () => {
    const root = document.createElement('div');
    const { api, calls } = server({ ...BASE, model: { ...BASE.model, ready: false } });
    const section = createVoiceprintSettings(fakeHost(root), api, recorder([]));
    await section.open();
    [...buttons(root).values()][0]!.click();
    await flush();
    const writes = calls.filter((c) => c.method !== 'GET');
    expect(writes.length).toBeGreaterThan(0);
    for (const c of writes) expect(c.body).toBeDefined();
  });
});

describe('your voice: your own words as the yes (SPN-20)', () => {
  const ENROLLED: VoiceprintState = { ...BASE, enrolled: true, working: true, own_words: false };
  const box = (root: HTMLElement): HTMLInputElement => root.querySelector<HTMLInputElement>('#voiceprint-own-words')!;
  const row = (root: HTMLElement): HTMLElement => box(root).closest('label') as HTMLElement;

  it('is offered only once she knows the voice, and not while teaching', () => {
    expect(showsOwnWords(null, false)).toBe(false);
    expect(showsOwnWords(BASE, false)).toBe(false);
    expect(showsOwnWords(ENROLLED, false)).toBe(true);
    expect(showsOwnWords(ENROLLED, true)).toBe(false);
  });

  it('is hidden with nobody enrolled', async () => {
    const root = document.createElement('div');
    const { api } = server(BASE);
    const section = createVoiceprintSettings(fakeHost(root), api, recorder([]));
    await section.open();
    expect(row(root).hidden).toBe(true);
  });

  it('is shown off once a voice is enrolled, with what it does and what always asks', async () => {
    const root = document.createElement('div');
    const { api } = server(ENROLLED);
    const section = createVoiceprintSettings(fakeHost(root), api, recorder([]));
    await section.open();
    expect(row(root).hidden).toBe(false);
    expect(box(root).checked).toBe(false);
    expect(box(root).getAttribute('role')).toBe('switch');
    expect(row(root).textContent).toContain(OWN_WORDS_LABEL);
    expect(row(root).textContent).toMatch(/Spending, sending and anything that can.t be undone always ask/);
  });

  it('turns it on and off through the core, which says where it stands', async () => {
    const root = document.createElement('div');
    const { api, calls } = server(ENROLLED, {
      'PUT /api/voiceprint': [
        { ...ENROLLED, own_words: true },
        { ...ENROLLED, own_words: false },
      ],
    });
    const section = createVoiceprintSettings(fakeHost(root), api, recorder([]));
    await section.open();
    box(root).checked = true;
    box(root).dispatchEvent(new Event('change'));
    await flush();
    expect(calls.filter((c) => c.method === 'PUT')).toEqual([{ method: 'PUT', path: '/api/voiceprint', body: { own_words: true } }]);
    expect(box(root).checked).toBe(true);
    expect(root.textContent).toMatch(/On\. Your own words approve/);
    box(root).checked = false;
    box(root).dispatchEvent(new Event('change'));
    await flush();
    expect(calls.filter((c) => c.method === 'PUT').map((c) => c.body)).toEqual([{ own_words: true }, { own_words: false }]);
    expect(box(root).checked).toBe(false);
  });

  it('shows what the core says, not what was clicked, when it refuses', async () => {
    const root = document.createElement('div');
    const { api } = server(ENROLLED, { 'PUT /api/voiceprint': [{ error: 'not_enrolled' }] });
    const section = createVoiceprintSettings(fakeHost(root), api, recorder([]));
    await section.open();
    box(root).checked = true;
    box(root).dispatchEvent(new Event('change'));
    await flush();
    expect(box(root).checked).toBe(false);
    expect(root.textContent).toMatch(/Teach her your voice first/);
  });

  it('goes away when the voice is forgotten', async () => {
    const root = document.createElement('div');
    const { api } = server({ ...ENROLLED, own_words: true });
    const section = createVoiceprintSettings(fakeHost(root), api, recorder([]), { changed: () => {}, confirm: () => true });
    await section.open();
    expect(box(root).checked).toBe(true);
    buttons(root).get('Forget my voice')!.click();
    await flush();
    expect(row(root).hidden).toBe(true);
  });
});
