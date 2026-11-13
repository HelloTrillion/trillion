// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { createConfirmations } from '../src/confirmations';
import { answer, begin, newlyAsked, type Profile, type Step } from '../src/onboarding';
import type { Host } from '../src/panel';
import { SEAMLESS_PATH, asksLine, asksLoader } from '../src/seamless';

const json = (body: unknown, status = 200): Response => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const flush = (): Promise<void> => new Promise((r) => setTimeout(r, 0));
const week = (shown: number, quick: number) => ({ asks: [{ days: 7, shown, quick }, { days: 28, shown: shown * 4, quick: quick * 4 }] });

describe('the asks line', () => {
  it('says the last 7 days in one line', () => {
    expect(asksLine(week(12, 9))).toBe('Last 7 days: 12 asks, 9 approved within 30 seconds unchanged.');
    expect(asksLine(week(1, 0))).toBe('Last 7 days: 1 ask, 0 approved within 30 seconds unchanged.');
    expect(asksLine(week(0, 0))).toBe('Last 7 days: no asks.');
  });

  it('is left out when there is nothing to read', () => {
    expect(asksLine(null)).toBe('');
    expect(asksLine({})).toBe('');
    expect(asksLine({ asks: [] })).toBe('');
    expect(asksLine({ asks: [{ days: 7, shown: '3', quick: 1 }] })).toBe('');
  });

  it('reads GET /api/seamless, and leaves the line out if that fails', async () => {
    const calls: string[] = [];
    const ok = asksLoader((method, path) => {
      calls.push(`${method} ${path}`);
      return Promise.resolve(json(week(3, 2)));
    });
    expect(await ok()).toBe('Last 7 days: 3 asks, 2 approved within 30 seconds unchanged.');
    expect(calls).toEqual([`GET ${SEAMLESS_PATH}`]);
    expect(await asksLoader(() => Promise.resolve(json({ error: 'unauthorized' }, 401)))()).toBe('');
    expect(await asksLoader(() => Promise.reject(new Error('offline')))()).toBe('');
  });
});

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
  return host as unknown as Host;
}

afterEach(() => {
  document.body.replaceChildren();
});

describe('in Settings → Confirmations', () => {
  const state = { now_ms: 0, days: 30, status: 'all', counts: { all: 0 }, actions: [] };

  it('shows the one line under the note, and only reads', async () => {
    const root = document.createElement('div');
    document.body.append(root);
    const methods: string[] = [];
    const api = (method: string, path: string): Promise<Response> => {
      methods.push(method);
      return Promise.resolve(json(path === SEAMLESS_PATH ? week(12, 9) : state));
    };
    const section = createConfirmations(fakeHost(root), api, () => undefined, () => undefined, asksLoader(api));
    await section.open();
    await flush();
    const line = root.querySelector<HTMLElement>('.confirmations-asks')!;
    expect(line.hidden).toBe(false);
    expect(line.textContent).toBe('Last 7 days: 12 asks, 9 approved within 30 seconds unchanged.');
    expect(line.previousElementSibling?.classList.contains('settings-note')).toBe(true);
    expect(root.querySelectorAll('.confirmations-asks')).toHaveLength(1);
    expect(methods.every((m) => m === 'GET')).toBe(true);
  });

  it('stays hidden without the measures', async () => {
    const root = document.createElement('div');
    document.body.append(root);
    const api = (): Promise<Response> => Promise.resolve(json(state));
    const section = createConfirmations(fakeHost(root), api, () => undefined);
    await section.open();
    expect(root.querySelector<HTMLElement>('.confirmations-asks')!.hidden).toBe(true);
  });
});

describe('onboarding questions, counted', () => {
  const FRESH: Profile = { userName: '', agentName: 'Trillion', timezone: 'UTC', directness: 'balanced' };

  it('counts each step that asks, once, on a first run only', () => {
    const told = new Set<Step>();
    let o = begin(FRESH);
    expect(newlyAsked(o, told)).toBe('user_name');
    told.add('user_name');
    // Asked again after an answer it could not use: the same question, not a new one.
    expect(newlyAsked(o, told)).toBeNull();
    o = answer(o, "I'm Sam");
    expect(newlyAsked(o, told)).toBe(o.step);
    expect(newlyAsked(null, told)).toBeNull();
    // Someone changing names later is not on a first run.
    expect(newlyAsked(begin({ ...FRESH, userName: 'Sam' }), new Set())).toBeNull();
  });

  it('does not count the page sharing a folder, or a finished introduction', () => {
    const o = begin(FRESH);
    expect(newlyAsked({ ...o, step: 'sharing' }, new Set())).toBeNull();
    expect(newlyAsked({ ...o, step: 'done' }, new Set())).toBeNull();
    expect(newlyAsked({ ...o, step: 'demo' }, new Set())).toBe('demo');
  });
});
