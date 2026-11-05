// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createUsageSettings } from '../src/browser';
import { createListeningSettings } from '../src/listening';
import type { Host } from '../src/panel';
import { type Api, createSettings } from '../src/settings';
import { createSpeechSettings } from '../src/speech';
import { revealForCard } from '../src/sidebar';
import { FREE, type Cost, createCapStep, createSpendCards, createSwitchLines, fromField, shortEstimate, speakCapped, spendReveals, toField } from '../src/spend';
import { statusText } from '../src/announce';
import { enterInField } from '../src/hotkey';
import type { ProviderUsage, Usage } from '../src/usage';
import { INITIAL, reduce as update } from '../src/view';

const flush = async (): Promise<void> => {
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0));
};

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

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

/** An api that answers by method and path, and records every call. */
function fakeApi(answers: Record<string, (body: unknown) => Response>): { api: Api; calls: Call[] } {
  const calls: Call[] = [];
  const api: Api = async (method, path, body) => {
    calls.push({ method, path, body });
    const answer = answers[`${method} ${path}`];
    return answer ? answer(body) : json({ error: 'not_found' }, 404);
  };
  return { api, calls };
}

const type = (input: HTMLInputElement, value: string): void => {
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
};

const button = (root: ParentNode, text: string): HTMLButtonElement => {
  const found = [...root.querySelectorAll('button')].find((b) => b.textContent === text && !b.closest('[hidden]'));
  if (!found) throw new Error(`no button "${text}"`);
  return found;
};

const field = (root: ParentNode, label: string): HTMLInputElement => {
  const l = [...root.querySelectorAll('label')].find((x) => x.textContent === label && !x.closest('[hidden]'));
  const input = l ? (root.querySelector(`#${CSS.escape(l.htmlFor)}`) as HTMLInputElement | null) : null;
  if (!input) throw new Error(`no field "${label}"`);
  return input;
};

const ESTIMATE = 'Roughly $0.40 an hour of talk on Sonnet (about 40 short turns), plus routines.';
const anthropicCost: Cost = {
  provider_id: 'anthropic',
  name: 'Anthropic',
  estimate: ESTIMATE,
  pricing_url: 'https://example.com/pricing',
  caps: { soft: 20, hard: 50, warn_pct: 80, unit: 'dollars' },
};
const localCost: Cost = { provider_id: 'local', name: 'this computer', estimate: FREE, pricing_url: null, caps: null };
const WARN = 'Warn me at ($ a month)';
const STOP = 'Stop at ($ a month)';

afterEach(() => {
  document.body.replaceChildren();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('numbers in fields', () => {
  it('are grouped, and read back with commas or spaces', () => {
    expect(toField(5_000_000, 'tokens')).toBe('5,000,000');
    expect(toField(12.5, 'dollars')).toBe('12.5');
    expect(fromField('5,000,000', 'tokens')).toBe(5_000_000);
    expect(fromField('5 000 000', 'tokens')).toBe(5_000_000);
    expect(fromField('$1,500.50', 'dollars')).toBe(1500.5);
    expect(fromField('1,500', 'seconds')).toBe(90_000);
    expect(fromField('lots', 'dollars')).toBeUndefined();
  });
});

describe('Enter in a field', () => {
  it('belongs to the field, never to a page shortcut, except in the message box', () => {
    const box = document.createElement('textarea');
    const other = document.createElement('input');
    document.body.append(box, other);
    expect(enterInField(other, box)).toBe(true);
    expect(enterInField(box, box)).toBe(false);
    expect(enterInField(document.body, box)).toBe(false);
  });
});

describe('the words', () => {
  it('shortens the core’s cost line to what a glance needs, and keeps any other as it is', () => {
    expect(shortEstimate(ESTIMATE)).toBe('About $0.40 an hour of talking.');
    expect(shortEstimate('Roughly $0.30 an hour of speaking (about 40 sentences).')).toBe('About $0.30 an hour of speaking.');
    expect(shortEstimate('No price is known for this voice yet.')).toBe('No price is known for this voice yet.');
  });
});

// ── Key steps ─────────────────────────────────────────────────────────────

function step(cost: Cost) {
  const s = createCapStep('t');
  document.body.append(s.element);
  s.show(cost);
  return s;
}

describe('the limits in a key step', () => {
  it('says in one line what it costs and its limits, with a link to the prices', () => {
    const s = step(anthropicCost);
    expect(s.element.textContent).toContain('About $0.40 an hour of talking. Warns at $20, stops at $50 a month.');
    const link = s.element.querySelector('a');
    expect(link?.getAttribute('href')).toBe('https://example.com/pricing');
    expect(link?.textContent).toBe('Prices (opens in a new tab)');
    expect(() => field(s.element, WARN)).toThrow();
  });

  it('shows the fields on Change, and keeps the line in step with them', () => {
    const s = step(anthropicCost);
    button(s.element, 'Change').click();
    expect(field(s.element, WARN).value).toBe('20');
    type(field(s.element, STOP), '80');
    expect(s.element.textContent).toContain('Warns at $20, stops at $80 a month.');
    // The same provider again (its model list read once more): what was typed stays.
    s.show(anthropicCost);
    expect(field(s.element, STOP).value).toBe('80');
  });

  it('says a local choice costs nothing and has no limit, with no fields', () => {
    const s = step(localCost);
    expect(s.element.textContent).toContain(FREE);
    expect(() => button(s.element, 'Change')).toThrow();
  });

  it('saves the limits only when they were changed from what the core will keep', async () => {
    const { api, calls } = fakeApi({ 'PUT /api/settings/spend/anthropic': () => json({}) });
    const s = step(anthropicCost);
    expect(await s.save(api)).toBeNull();
    expect(calls).toEqual([]);
    button(s.element, 'Change').click();
    type(field(s.element, STOP), '80');
    expect(await s.save(api)).toBeNull();
    expect(calls).toEqual([{ method: 'PUT', path: '/api/settings/spend/anthropic', body: { soft: 20, hard: 80 } }]);
  });

  it('refuses Stop at below Warn me at before sending anything', async () => {
    const { api, calls } = fakeApi({});
    const s = step(anthropicCost);
    button(s.element, 'Change').click();
    type(field(s.element, STOP), '10');
    expect(s.problem()).toBe("Stop at can't be lower than Warn me at.");
    expect(await s.save(api)).toBe("Stop at can't be lower than Warn me at.");
    expect(calls).toEqual([]);
  });

  it('has nothing to complain about while it is not shown', () => {
    const s = step(anthropicCost);
    button(s.element, 'Change').click();
    type(field(s.element, STOP), '10');
    s.element.hidden = true;
    expect(s.problem()).toBeNull();
  });

  it('says plainly, as it is typed, when nothing stops the spending', () => {
    const s = step(anthropicCost);
    button(s.element, 'Change').click();
    type(field(s.element, STOP), '');
    expect(s.element.textContent).toContain("No stop set: Trillion won't stop spending on Anthropic.");
    type(field(s.element, WARN), '');
    expect(s.element.textContent).toContain("No stop set: Trillion won't stop spending on Anthropic. No warning either.");
  });

  it('encodes the provider id in the path', async () => {
    const { api, calls } = fakeApi({ 'PUT /api/settings/spend/custom%3Aopenrouter.ai': () => json({}) });
    const s = step({ ...anthropicCost, provider_id: 'custom:openrouter.ai', name: 'OpenRouter' });
    button(s.element, 'Change').click();
    type(field(s.element, WARN), '5');
    await s.save(api);
    expect(calls[0]?.path).toBe('/api/settings/spend/custom%3Aopenrouter.ai');
  });

  it('shows limits counted in units in that unit, minutes for hearing', async () => {
    const { api, calls } = fakeApi({ 'PUT /api/settings/spend/deepgram': () => json({}) });
    const s = step({ ...anthropicCost, provider_id: 'deepgram', name: 'Deepgram', caps: { soft: 36_000, hard: 90_000, warn_pct: 80, unit: 'seconds' } });
    expect(s.element.textContent).toContain('Warns at 600 minutes, stops at 1,500 minutes a month.');
    button(s.element, 'Change').click();
    expect(field(s.element, 'Stop at (minutes a month)').value).toBe('1,500');
    type(field(s.element, 'Stop at (minutes a month)'), '2000');
    await s.save(api);
    expect(calls[0]?.body).toEqual({ soft: 36_000, hard: 120_000 });
  });
});

const modelState = {
  source: 'settings',
  active: null,
  saved: null,
  keys: {},
  keychain: true,
  defaults: { local: 'http://localhost:11434/v1' },
  costs: {
    anthropic: anthropicCost,
    openai: { ...anthropicCost, provider_id: 'openai', name: 'OpenAI' },
    custom: { provider_id: null, name: null, estimate: 'Free if it runs on this computer; otherwise priced by the server you point it at.', pricing_url: null, caps: null },
    local: localCost,
  },
};

describe('Settings, Model: the key step', () => {
  it('shows the chosen provider’s cost and limits, and a local model as free', async () => {
    const root = document.createElement('div');
    document.body.append(root);
    const { api } = fakeApi({ 'GET /api/settings/model': () => json(modelState) });
    await createSettings(fakeHost(root), api).open();
    expect(root.textContent).toContain('Warns at $20, stops at $50 a month.');
    const local = root.querySelector<HTMLInputElement>('input[value="local"]')!;
    local.checked = true;
    local.dispatchEvent(new Event('change'));
    expect(root.textContent).toContain(FREE);
  });

  it('saves the key, then the limits the user changed, and clears the key', async () => {
    const root = document.createElement('div');
    document.body.append(root);
    const { api, calls } = fakeApi({
      'GET /api/settings/model': () => json(modelState),
      'POST /api/settings/model/models': () => json({ models: [{ id: 'claude-x', label: 'Claude X' }], cost: anthropicCost }),
      'PUT /api/settings/model': () => json(modelState),
      'PUT /api/settings/spend/anthropic': () => json({ error: 'too_high' }, 400),
    });
    const host = fakeHost(root);
    await createSettings(host, api).open();
    (root.querySelector('#settings-key') as HTMLInputElement).value = 'sk-test-key';
    button(root, 'Check and list models').click();
    await flush();
    button(root, 'Change').click();
    type(field(root, WARN), '10');
    button(root, 'Save').click();
    await flush();
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      'GET /api/settings/model',
      'POST /api/settings/model/models',
      'PUT /api/settings/model',
      'PUT /api/settings/spend/anthropic',
    ]);
    expect(calls[3]?.body).toEqual({ soft: 10, hard: 50 });
    // The key is kept, so it is not sent again; the limits can be fixed and saved.
    expect((root.querySelector('#settings-key') as HTMLInputElement).value).toBe('');
    expect(root.textContent).toContain('The model is saved, but its limits are not: That is more than Trillion allows here.');
    expect(host.open).toBe(true);
  });
});

describe('Settings, Voice and Hearing: the key steps', () => {
  it('shows a paid voice’s cost and limits, and the computer’s voice as free', async () => {
    const root = document.createElement('div');
    document.body.append(root);
    const eleven = { ...anthropicCost, provider_id: 'elevenlabs', name: 'ElevenLabs', estimate: 'Roughly $0.30 an hour of speaking.' };
    const state = {
      options: [
        { kind: 'system', title: "This computer's voice", note: '', where: 'client', needs_key: false, free: true, key_link: null, takes_url: false, key: null, cost: { ...localCost, provider_id: null } },
        { kind: 'elevenlabs', title: 'ElevenLabs', note: '', where: 'core', needs_key: true, free: false, key_link: null, takes_url: false, key: null, cost: eleven },
      ],
      saved: { kind: 'elevenlabs', voice: 'v1', base_url: null },
      keychain: true,
    };
    const { api } = fakeApi({ 'GET /api/settings/speech': () => json(state) });
    await createSpeechSettings(fakeHost(root), api).open();
    expect(root.textContent).toContain('About $0.30 an hour of speaking. Warns at $20, stops at $50 a month.');
    const system = root.querySelector<HTMLInputElement>('input[value="system"]')!;
    system.checked = true;
    system.dispatchEvent(new Event('change'));
    expect(root.textContent).toContain(FREE);
  });

  it('shows hearing’s cost and limits', async () => {
    const root = document.createElement('div');
    document.body.append(root);
    const deepgram = { ...anthropicCost, provider_id: 'deepgram', name: 'Deepgram', estimate: 'Roughly $0.46 an hour of listening.' };
    const state = {
      options: [{ kind: 'deepgram', title: 'Deepgram', note: '', needs_key: true, free: false, turns: true, key_link: null, install_hint: null, key: null, cost: deepgram }],
      saved: { kind: 'deepgram', choice: 'en' },
      think_early: true,
      keychain: true,
    };
    const { api } = fakeApi({ 'GET /api/settings/listening': () => json(state) });
    await createListeningSettings(fakeHost(root), api).open();
    await flush();
    expect(root.textContent).toContain('About $0.46 an hour of listening. Warns at $20, stops at $50 a month.');
  });
});

// ── Settings → Usage ─────────────────────────────────────────────────────

const provider = (over: Partial<ProviderUsage> = {}): ProviderUsage => ({
  provider_id: 'anthropic',
  leg: 'model',
  name: 'Anthropic',
  model: 'claude-test',
  spent: 3.2,
  unit: 'dollars',
  soft: 20,
  hard: 50,
  warn_pct: 80,
  state: 'ok',
  split: { conversation: 2.1, background: 1.1 },
  paused: [],
  switched: null,
  unestimated: null,
  estimate: ESTIMATE,
  pricing_url: 'https://example.com/pricing',
  ...over,
});
const usageOf = (providers: ProviderUsage[]): Usage => ({ month: '2026-11', since_ms: 0, rows: [], total_cost_usd: null, unpriced_tokens: 0, providers });
const unpriced = (over: Partial<ProviderUsage> = {}): ProviderUsage =>
  provider({ provider_id: 'custom:api.example.com', name: 'api.example.com', model: 'mystery', unit: 'tokens', soft: 5e6, hard: 12e6, ...over });

async function openUsage(providers: ProviderUsage[] | (() => ProviderUsage[] | null), answers: Record<string, (body: unknown) => Response> = {}) {
  const root = document.createElement('div');
  document.body.append(root);
  const { api, calls } = fakeApi({
    'GET /api/settings/spend/prices': () => json({ prices: {} }),
    'PUT /api/settings/spend/prices': (body) => json({ prices: body }),
    ...answers,
  });
  const read = typeof providers === 'function' ? providers : () => providers;
  const load = vi.fn(async () => {
    const list = read();
    return list ? usageOf(list) : null;
  });
  const settings = createUsageSettings(fakeHost(root), load, api);
  await settings.open();
  await flush();
  const block = (name: string): HTMLElement => {
    const found = [...root.querySelectorAll<HTMLElement>('.spend-provider')].find((b) => b.querySelector('h3')?.textContent === name);
    if (!found) throw new Error(`no block "${name}"`);
    return found;
  };
  return { root, calls, load, block, settings };
}

const more = (b: HTMLElement): HTMLDetailsElement => b.querySelector('details') as HTMLDetailsElement;

describe('Settings, Usage: one block per service', () => {
  it('opens on the plain explanation, with no requirement ids', async () => {
    const { root } = await openUsage([provider()]);
    expect(root.textContent).toContain(
      'You pay each service directly. Trillion warns you at the first amount and stops using that service at the second, until you raise it or the month ends.',
    );
    expect(root.textContent).not.toMatch(/BRN-|soft cap|hard cap/);
  });

  it('says what it does, the month so far, what it costs, the free choice and its prices page', async () => {
    const { block } = await openUsage([provider()]);
    const b = block('Anthropic');
    expect(b.textContent).toContain('How she thinks');
    expect(b.textContent).toContain('$3.20 this month');
    expect(b.textContent).toContain('$2.10 talking with you · $1.10 in the background');
    expect(b.textContent).toContain("About $0.40 an hour of talking. Free instead: a local model with Ollama. See Anthropic's prices (opens in a new tab)");
    expect(b.querySelector('a')?.getAttribute('href')).toBe('https://example.com/pricing');
    // The rest is under More options, closed, with the assumption the estimate makes.
    expect(more(b).open).toBe(false);
    expect(more(b).textContent).toContain(`How that is worked out: ${ESTIMATE}`);
  });

  it('saves both limits and the early heads-up with one Save, and says so', async () => {
    const { block, calls } = await openUsage([provider()], { 'PUT /api/settings/spend/anthropic': () => json(provider()) });
    const b = block('Anthropic');
    type(field(b, WARN), '25');
    type(field(b, STOP), '60');
    type(field(b, 'Early heads-up at (% of Warn me at)'), '90');
    button(b, 'Save').click();
    expect(b.textContent).toContain('Saving…');
    await flush();
    expect(calls.filter((c) => c.method === 'PUT')).toEqual([{ method: 'PUT', path: '/api/settings/spend/anthropic', body: { soft: 25, hard: 60, warn_pct: 90 } }]);
    expect(b.querySelector('[role="status"]')?.textContent).toBe('Saved.');
  });

  it('shows the core’s refusal in the block', async () => {
    const { block } = await openUsage([provider()], { 'PUT /api/settings/spend/anthropic': () => json({ error: 'hard_below_soft' }, 400) });
    const b = block('Anthropic');
    button(b, 'Save').click();
    await flush();
    expect(b.textContent).toContain("Stop at can't be lower than Warn me at.");
  });

  it('checks the fields before sending', async () => {
    const { block, calls } = await openUsage([provider()]);
    const b = block('Anthropic');
    type(field(b, 'Early heads-up at (% of Warn me at)'), '0');
    button(b, 'Save').click();
    expect(b.textContent).toContain('The early heads-up must be between 1 and 100 percent.');
    type(field(b, 'Early heads-up at (% of Warn me at)'), '80');
    type(field(b, WARN), 'lots');
    button(b, 'Save').click();
    expect(b.textContent).toContain('Amounts are numbers of zero or more, or empty for none.');
    expect(calls.filter((c) => c.method === 'PUT')).toEqual([]);
  });

  it('names the field the core found wrong', async () => {
    const { block } = await openUsage([provider()], { 'PUT /api/settings/spend/anthropic': () => json({ error: 'bad_request', fields: ['hard'] }, 422) });
    const b = block('Anthropic');
    button(b, 'Save').click();
    await flush();
    expect(b.textContent).toContain('Check Stop at.');
  });

  it('says plainly, as it is typed, when nothing stops the spending', async () => {
    const { block } = await openUsage([provider()]);
    const b = block('Anthropic');
    type(field(b, STOP), '');
    expect(b.textContent).toContain("No stop set: Trillion won't stop spending on Anthropic.");
  });

  it('asks for a price for an unpriced model, opening More options, and saves it with the limits', async () => {
    const other = { 'custom:other.example': { m: { input: 1, output: 2 } } };
    const { block, calls } = await openUsage([unpriced({ unestimated: { units: 340_000, unit: 'tokens' } })], {
      'GET /api/settings/spend/prices': () => json({ prices: other }),
      'PUT /api/settings/spend/custom%3Aapi.example.com': () => json({}),
    });
    const b = block('api.example.com');
    expect(b.textContent).toContain("340k tokens aren't counted in dollars yet: Trillion doesn't know this model's price. Add it under More options.");
    expect(b.textContent).toContain("Until it has a price, this model's limits count tokens instead of dollars.");
    expect(b.textContent).toContain("Your price for mystery, in $ per million tokens. Leave both empty to use Trillion's own price, if it has one.");
    expect(more(b).open).toBe(true);
    type(field(b, 'Input ($ per million tokens)'), '0.5');
    type(field(b, 'Output ($ per million tokens)'), '1.5');
    button(b, 'Save').click();
    await flush();
    const puts = calls.filter((c) => c.method === 'PUT');
    expect(puts.map((c) => c.path)).toEqual(['/api/settings/spend/custom%3Aapi.example.com', '/api/settings/spend/prices']);
    expect(puts[1]?.body).toEqual({ ...other, 'custom:api.example.com': { mystery: { input: 0.5, output: 1.5 } } });
    expect(b.textContent).toContain('Saved.');
  });

  it('keeps the first price when a second is saved', async () => {
    const two = [unpriced(), unpriced({ provider_id: 'custom:b.example.com', name: 'b.example.com', model: 'other' })];
    const { block, calls } = await openUsage(two, {
      'PUT /api/settings/spend/custom%3Aapi.example.com': () => json({}),
      'PUT /api/settings/spend/custom%3Ab.example.com': () => json({}),
    });
    const a = block('api.example.com');
    type(field(a, 'Input ($ per million tokens)'), '1');
    type(field(a, 'Output ($ per million tokens)'), '2');
    button(a, 'Save').click();
    await flush();
    const b = block('b.example.com');
    type(field(b, 'Input ($ per million tokens)'), '3');
    type(field(b, 'Output ($ per million tokens)'), '4');
    button(b, 'Save').click();
    await flush();
    const prices = calls.filter((c) => c.path === '/api/settings/spend/prices' && c.method === 'PUT');
    expect(prices.at(-1)?.body).toEqual({
      'custom:api.example.com': { mystery: { input: 1, output: 2 } },
      'custom:b.example.com': { other: { input: 3, output: 4 } },
    });
  });

  it('leaves what is typed in another block alone when one is saved', async () => {
    const { block } = await openUsage([provider(), provider({ provider_id: 'elevenlabs', leg: 'voice', name: 'ElevenLabs', model: null })], {
      'PUT /api/settings/spend/anthropic': () => json({}),
    });
    const voice = block('ElevenLabs');
    const typed = field(voice, STOP);
    type(typed, '99');
    typed.focus();
    button(block('Anthropic'), 'Save').click();
    await flush();
    expect(field(block('ElevenLabs'), STOP)).toBe(typed);
    expect(typed.value).toBe('99');
    expect(document.activeElement).toBe(typed);
  });

  it('keeps the blocks, and says so, when usage cannot be read again', async () => {
    let fresh = true;
    const { block, settings, root } = await openUsage(() => (fresh ? [provider()] : null), { 'PUT /api/settings/spend/anthropic': () => json({}) });
    fresh = false;
    button(block('Anthropic'), 'Save').click();
    await flush();
    expect(block('Anthropic').textContent).toContain("Saved. The totals couldn't refresh.");
    await settings.open();
    expect(root.textContent).toContain("Couldn't refresh: these are the last numbers read.");
    expect(block('Anthropic')).toBeTruthy();
  });

  it('starts each opening with no old messages', async () => {
    const { block, settings } = await openUsage([provider()], { 'PUT /api/settings/spend/anthropic': () => json({}) });
    button(block('Anthropic'), 'Save').click();
    await flush();
    await settings.open();
    expect(block('Anthropic').querySelector('[role="status"]')?.textContent).toBe('');
  });

  it('shows a refused price in plain words', async () => {
    const { block } = await openUsage([unpriced()], {
      'PUT /api/settings/spend/custom%3Aapi.example.com': () => json({}),
      'PUT /api/settings/spend/prices': () => json({ error: 'zero_price' }, 400),
    });
    const b = block('api.example.com');
    type(field(b, 'Input ($ per million tokens)'), '0');
    type(field(b, 'Output ($ per million tokens)'), '0');
    button(b, 'Save').click();
    await flush();
    expect(b.textContent).toContain('The limits are saved, but not the price: A price of zero would stop this model being counted.');
  });

  it('offers a voice its own rate per thousand characters', async () => {
    const { block, calls } = await openUsage([provider({ provider_id: 'elevenlabs', leg: 'voice', name: 'ElevenLabs', model: null })], {
      'PUT /api/settings/spend/elevenlabs': () => json({}),
    });
    const b = block('ElevenLabs');
    expect(b.textContent).toContain('How she speaks');
    expect(b.textContent).toContain('Free instead: this computer’s own voice.');
    type(field(b, 'Your rate ($ per thousand characters)'), '0.2');
    button(b, 'Save').click();
    await flush();
    expect(calls.find((c) => c.path === '/api/settings/spend/prices' && c.method === 'PUT')?.body).toEqual({ elevenlabs: { '*': { unit: 'chars', per_unit: 0.2 } } });
  });

  it('offers hearing its own rate per minute, and sends no price that did not change', async () => {
    const { block, calls } = await openUsage([provider({ provider_id: 'deepgram', leg: 'hearing', name: 'Deepgram', model: null })], {
      'PUT /api/settings/spend/deepgram': () => json({}),
    });
    const b = block('Deepgram');
    button(b, 'Save').click();
    await flush();
    expect(calls.filter((c) => c.path === '/api/settings/spend/prices' && c.method === 'PUT')).toEqual([]);
    type(field(b, 'Your rate ($ per minute)'), '0.01');
    button(block('Deepgram'), 'Save').click();
    await flush();
    expect(calls.find((c) => c.path === '/api/settings/spend/prices' && c.method === 'PUT')?.body).toEqual({ deepgram: { '*': { unit: 'seconds', per_unit: 0.01 } } });
  });

  it('lists paused work in plain words', async () => {
    const { block } = await openUsage([provider({ state: 'capped', paused: ['routines', 'watchers', 'channels', 'titles'] })]);
    const b = block('Anthropic');
    expect(b.textContent).toContain('Anthropic has hit its $50 stop for this month.');
    expect(b.textContent).toContain('Paused until the 1st or until you raise Stop at: routines, watchers, channel replies and naming conversations.');
  });

  it('shows a switch in the core’s words, with Switch back now only once the cap allows it', async () => {
    const switched = { to: 'local/gemma', label: 'Gemma', until_ms: 1, line: 'Using Gemma until Dec 1' };
    const capped = await openUsage([provider({ state: 'capped', switched })]);
    expect(capped.block('Anthropic').textContent).toContain('Using Gemma until Dec 1.');
    expect(() => button(capped.block('Anthropic'), 'Switch back now')).toThrow();
    document.body.replaceChildren();

    let now: ProviderUsage = provider({ switched });
    const { block, calls } = await openUsage(() => [now], {
      'POST /api/spend/switch/anthropic/end': () => json({ switches: [] }),
    });
    now = provider();
    button(block('Anthropic'), 'Switch back now').click();
    await flush();
    expect(calls.at(-1)).toEqual({ method: 'POST', path: '/api/spend/switch/anthropic/end', body: {} });
    expect(block('Anthropic').textContent).toContain('Back on Anthropic.');
  });

  it('says why a switch cannot end yet', async () => {
    const { block } = await openUsage([provider({ switched: { to: 'local/gemma', label: 'Gemma', until_ms: 1, line: 'Using Gemma until Dec 1' } })], {
      'POST /api/spend/switch/anthropic/end': () => json({ error: 'still_capped' }, 409),
    });
    button(block('Anthropic'), 'Switch back now').click();
    await flush();
    expect(block('Anthropic').textContent).toContain('Anthropic is still at its cap. Raise Stop at first.');
  });

  it('asks whether to switch back once a save lifts the cap', async () => {
    const switched = { to: 'local/gemma', label: 'Gemma', until_ms: 1, line: 'Using Gemma until Dec 1' };
    const { block } = await openUsage([provider({ switched })], { 'PUT /api/settings/spend/anthropic': () => json({}) });
    button(block('Anthropic'), 'Save').click();
    await flush();
    expect(block('Anthropic').textContent).toContain('Saved. Switch back to Anthropic now?');
  });

  it('clears its last message as soon as a field is edited', async () => {
    const { block } = await openUsage([provider()], { 'PUT /api/settings/spend/anthropic': () => json({}) });
    button(block('Anthropic'), 'Save').click();
    await flush();
    const b = block('Anthropic');
    expect(b.querySelector('[role="status"]')?.textContent).toBe('Saved.');
    type(field(b, STOP), '1');
    expect(b.querySelector('[role="status"]')?.textContent).toBe('');
    expect(b.textContent).toContain("Stop at can't be lower than Warn me at.");
    expect(b.textContent).not.toContain('Saved.');
  });

  it('saves on Enter in a field, and keeps the key from the page', async () => {
    const { block, calls } = await openUsage([provider()], { 'PUT /api/settings/spend/anthropic': () => json({}) });
    const page = vi.fn();
    window.addEventListener('keydown', page);
    const enter = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
    field(block('Anthropic'), STOP).dispatchEvent(enter);
    window.removeEventListener('keydown', page);
    await flush();
    expect(page).not.toHaveBeenCalled();
    expect(enter.defaultPrevented).toBe(true);
    expect(calls.filter((c) => c.method === 'PUT')).toHaveLength(1);
  });

  it('never says a price is unknown once the user has given one', async () => {
    const { block } = await openUsage([unpriced({ unestimated: { units: 1_000, unit: 'tokens' } })], {
      'GET /api/settings/spend/prices': () => json({ prices: { 'custom:api.example.com': { mystery: { input: 1, output: 2 } } } }),
    });
    const b = block('api.example.com');
    expect(b.textContent).toContain("1k tokens aren't counted in dollars yet. Check the price under More options.");
    expect(b.textContent).not.toContain("doesn't know");
  });

  it('shows big numbers grouped', async () => {
    const { block } = await openUsage([unpriced()]);
    expect(field(block('api.example.com'), 'Warn me at (tokens a month)').value).toBe('5,000,000');
  });

  it('says a service on this computer costs nothing and has no limit', async () => {
    const { block } = await openUsage([
      provider({ provider_id: null, name: 'Your model', soft: null, hard: null, spent: 0, estimate: FREE, pricing_url: null, split: { conversation: 0, background: 0 } }),
    ]);
    const b = block('Your model');
    expect(b.textContent).toContain(FREE);
    expect(b.querySelectorAll('input')).toHaveLength(0);
  });

  it('keeps the list by model, closed, under Details by model', async () => {
    const { root } = await openUsage([provider()]);
    const details = [...root.querySelectorAll('details')].find((d) => d.querySelector('summary')?.textContent === 'Details by model');
    expect(details?.open).toBe(false);
  });
});

// ── The spend card ───────────────────────────────────────────────────────

const card = (over: Record<string, unknown> = {}) => ({
  type: 'spend.card',
  id: 'c1',
  provider_id: 'anthropic',
  name: 'Anthropic',
  leg: 'model',
  cap: 50,
  unit: 'dollars',
  cap_text: '$50',
  line: "I've reached the $50 cap for Anthropic this month, so I've stopped before spending more.",
  status: 'open',
  options: [
    { choice: 'raise', amount: 75, unit: 'dollars' },
    { choice: 'switch:local/gemma', label: 'gemma', until_ms: 1, until: 'Dec 1' },
    { choice: 'stop' },
  ],
  ...over,
});

function cards(connected = true) {
  const root = document.createElement('div');
  document.body.append(root);
  const sent: Record<string, unknown>[] = [];
  const spend = createSpendCards(root, (body) => {
    if (!connected) return false;
    sent.push(body);
    return true;
  });
  return { root, sent, spend };
}

const RAISE = 'New cap ($ a month)';
const labels = (root: HTMLElement): string[] => [...root.querySelectorAll('button')].map((b) => b.textContent ?? '');

describe('the spend card', () => {
  it('shows the line, a raise field prefilled, and a button for each choice, sending nothing on its own', () => {
    const { root, sent, spend } = cards();
    spend.handle(card());
    expect(root.textContent).toContain("You've hit your monthly cap");
    expect(root.textContent).toContain("I've reached the $50 cap for Anthropic this month");
    expect(field(root, RAISE).value).toBe('75');
    expect(labels(root)).toEqual(['Raise to $75', 'Use gemma until Dec 1', 'Not now']);
    // Only the note is a live region, so a redraw is never read out whole.
    expect([...root.querySelectorAll('[role="status"], [aria-live]')].map((n) => n.className)).toEqual(['card-note']);
    expect(sent).toEqual([]);
  });

  it('names the raise as it is typed, and sends the amount on a click', () => {
    const { root, sent, spend } = cards();
    spend.handle(card());
    type(field(root, RAISE), '100');
    expect(labels(root)[0]).toBe('Raise to $100');
    button(root, 'Raise to $100').click();
    expect(sent).toEqual([{ type: 'spend.choice', id: 'c1', choice: 'raise', amount: 100 }]);
    expect(root.textContent).toContain('Saving your choice…');
  });

  it('raises on Enter in the field', () => {
    const { root, sent, spend } = cards();
    spend.handle(card());
    const page = vi.fn();
    window.addEventListener('keydown', page);
    field(root, RAISE).dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    window.removeEventListener('keydown', page);
    expect(sent).toEqual([{ type: 'spend.choice', id: 'c1', choice: 'raise', amount: 75 }]);
    // No page shortcut sees the key.
    expect(page).not.toHaveBeenCalled();
  });

  it('sends a switch without an amount, and waits for the answer', () => {
    const { root, sent, spend } = cards();
    spend.handle(card());
    button(root, 'Use gemma until Dec 1').click();
    expect(sent).toEqual([{ type: 'spend.choice', id: 'c1', choice: 'switch:local/gemma' }]);
    expect([...root.querySelectorAll('button')].every((b) => b.disabled)).toBe(true);
  });

  it('refuses a raise that is not a number without sending', () => {
    const { root, sent, spend } = cards();
    spend.handle(card());
    type(field(root, RAISE), 'more');
    button(root, 'Raise').click();
    expect(sent).toEqual([]);
    expect(root.textContent).toContain('Type the new cap as a number.');
  });

  it('says so, and can be tried again, when the page is not connected', () => {
    const { root, spend } = cards(false);
    spend.handle(card());
    button(root, 'Raise to $75').click();
    expect(root.textContent).toContain("Not sent: Trillion isn't connected. Try again.");
    expect(button(root, 'Raise to $75').disabled).toBe(false);
  });

  it('can be tried again when no answer comes', () => {
    vi.useFakeTimers();
    const { root, spend } = cards();
    spend.handle(card());
    button(root, 'Raise to $75').click();
    expect(button(root, 'Raise to $75').disabled).toBe(true);
    vi.advanceTimersByTime(10_000);
    expect(button(root, 'Raise to $75').disabled).toBe(false);
    expect(root.textContent).toContain('No answer yet. Try again.');
  });

  it('shows a short reason when the core refuses, in place, keeping focus', () => {
    const { root, sent, spend } = cards();
    spend.handle(card());
    const raise = button(root, 'Raise to $75');
    raise.click();
    spend.handle({ type: 'spend.refused', id: 'c1', code: 'bad_amount' });
    expect(root.textContent).toContain("Pick a cap above $50 (what you've spent so far).");
    expect(button(root, 'Raise to $75')).toBe(raise);
    expect(raise.disabled).toBe(false);
    raise.focus();
    for (const [code, words] of [
      ['wrong_session', 'Answer this in the conversation where it appeared.'],
      ['closed', "Already sorted: the cap isn't reached any more."],
      ['not_a_tap', 'Tap or click a button to choose.'],
      ['failed', 'Something went wrong. Try again.'],
    ] as const) {
      spend.handle({ type: 'spend.refused', id: 'c1', code });
      expect(root.querySelector('.card-note')?.textContent).toBe(words);
    }
    expect(document.activeElement).toBe(raise);
    expect(sent).toHaveLength(1);
  });

  it('keeps what is typed and the focus when the same card comes again', () => {
    const { root, spend } = cards();
    spend.handle(card());
    const input = field(root, RAISE);
    type(input, '90');
    input.focus();
    spend.handle(card());
    expect(field(root, RAISE)).toBe(input);
    expect(document.activeElement).toBe(input);
  });

  it('goes once the cap is raised or switched', () => {
    const { root, spend } = cards();
    spend.handle(card());
    spend.handle({ type: 'spend.resolved', id: 'c1', status: 'raised' });
    expect(root.textContent).toBe('');
    expect(root.hidden).toBe(true);
  });

  it('stays reachable from the banner after Not now, and Change goes to the raise field', () => {
    const { root, sent, spend } = cards();
    spend.handle(card());
    button(root, 'Not now').click();
    spend.handle({ type: 'spend.resolved', id: 'c1', status: 'stopped' });
    expect(root.hidden).toBe(false);
    expect(root.querySelector('.spend-card')).toBeNull();
    expect(root.textContent).toContain('Anthropic is stopped at its $50 cap.');
    button(root, 'Change').click();
    expect(document.activeElement).toBe(field(root, RAISE));
    button(root, 'Raise to $75').click();
    expect(sent.at(-1)).toEqual({ type: 'spend.choice', id: 'c1', choice: 'raise', amount: 75 });
  });

  it('shows a stopped card sent again after a reload as the banner line', () => {
    const { root, spend } = cards();
    spend.handle(card({ status: 'stopped' }));
    expect(root.querySelector('.spend-card')).toBeNull();
    expect(button(root, 'Change')).toBeTruthy();
  });

  it('offers the voice and hearing choices in their own words, and a raise in minutes for hearing', () => {
    const { root, sent, spend } = cards();
    spend.handle(
      card({ id: 'v', leg: 'voice', provider_id: 'elevenlabs', options: [{ choice: 'raise', amount: 75, unit: 'dollars' }, { choice: 'switch:system', label: "the computer's voice", until_ms: 1, until: 'Dec 1' }, { choice: 'text' }] }),
    );
    expect(labels(root)).toEqual(['Raise to $75', "Use the computer's voice until Dec 1", 'Text only']);
    spend.reset();
    spend.handle(card({ id: 'h', leg: 'hearing', provider_id: 'deepgram', unit: 'seconds', cap: 90_000, options: [{ choice: 'raise', amount: 135_000, unit: 'seconds' }, { choice: 'type' }] }));
    expect(field(root, 'New cap (minutes a month)').value).toBe('2,250');
    button(root, 'Type for now').click();
    expect(sent).toEqual([{ type: 'spend.choice', id: 'h', choice: 'type' }]);
  });

  it('sends a raise in minutes as seconds', () => {
    const { root, sent, spend } = cards();
    spend.handle(card({ id: 'h', leg: 'hearing', unit: 'seconds', cap: 90_000, options: [{ choice: 'raise', amount: 135_000, unit: 'seconds' }, { choice: 'type' }] }));
    type(field(root, 'New cap (minutes a month)'), '3000');
    button(root, 'Raise to 3,000 minutes').click();
    expect(sent).toEqual([{ type: 'spend.choice', id: 'h', choice: 'raise', amount: 180_000 }]);
  });

  it('knows the line it says, so the banner need not say it again, after Not now too', () => {
    const { spend } = cards();
    const line = card().line;
    expect(spend.has(line)).toBe(false);
    spend.handle(card());
    expect(spend.has(line)).toBe(true);
    spend.handle({ type: 'spend.resolved', id: 'c1', status: 'stopped' });
    expect(spend.has(line)).toBe(true);
    spend.handle({ type: 'spend.resolved', id: 'c1', status: 'raised' });
    expect(spend.has(line)).toBe(false);
  });

  it('starts empty for another conversation', () => {
    const { root, spend } = cards();
    spend.handle(card());
    spend.handle({ type: 'session', id: 'other' });
    expect(root.hidden).toBe(true);
  });
});

describe('the banner at a cap', () => {
  it('shows the app’s capped line', () => {
    const line = "I've reached the $50 cap for Anthropic this month, so I've stopped before spending more.";
    const v = update({ ...INITIAL, link: 'ready', busy: true }, { type: 'error', code: 'capped', leg: 'model', provider_id: 'anthropic', cap: 50, unit: 'dollars', sentence: line });
    expect(v.banner).toBe(line);
    expect(v.busy).toBe(false);
  });

  it('is a calm stop, not a failure: no error orb, and its own status', () => {
    let v = update({ ...INITIAL, link: 'ready', busy: true, orb: 'processing' }, { type: 'error', code: 'capped', leg: 'model', sentence: 'At the cap.' });
    v = update(v, { type: 'state', orb: 'error' });
    expect(v.orb).toBe('idle');
    expect(statusText(v)).toBe('Stopped at your cap');
    // A failure that is not a cap still shows as one.
    expect(update({ ...INITIAL, link: 'ready' }, { type: 'state', orb: 'error' }).orb).toBe('error');
    // Raised: the stop is over.
    v = update(v, { type: 'spend.resolved', id: 'c1', status: 'raised' });
    expect(statusText(v)).toBe('Ready');
  });

  it('brings the card into view on a phone, as an approval card is', () => {
    expect(spendReveals({ type: 'spend.card', id: 'c1' })).toBe(true);
    expect(spendReveals({ type: 'error', code: 'capped' })).toBe(true);
    expect(spendReveals({ type: 'error', code: 'provider' })).toBe(false);
    expect(spendReveals({ type: 'spend.resolved', id: 'c1' })).toBe(false);
    // What main does with it: the collapsed sheet on a phone rises to half.
    expect(revealForCard(true, false)).toEqual({ unhide: false, raise: 'half' });
  });

  it('turns the microphone off when hearing is capped', () => {
    const v = update({ ...INITIAL, link: 'ready', mic: true, hearing: 'half a' }, { type: 'error', code: 'capped', leg: 'hearing', sentence: 'At the cap.' });
    expect(v.mic).toBe(false);
  });

  it('keeps the switch lines while they last, and clears them', () => {
    let v = update(INITIAL, { type: 'spend.switch', switches: [{ provider_id: 'anthropic', leg: 'model', to: 'local/gemma', label: 'Gemma', until_ms: 1, line: 'Using Gemma until Dec 1' }] });
    expect(v.switches).toEqual([{ provider_id: 'anthropic', line: 'Using Gemma until Dec 1' }]);
    v = update(v, { type: 'spend.switch', switches: [] });
    expect(v.switches).toEqual([]);
  });

  it('shows a switch with Switch back now once the cap allows it, and why not when the core says no', async () => {
    const root = document.createElement('div');
    document.body.append(root);
    let answer = json({ error: 'still_capped' }, 409);
    const { api, calls } = fakeApi({ 'POST /api/spend/switch/anthropic/end': () => answer });
    const lines = createSwitchLines(root, api);
    const one = [{ provider_id: 'anthropic', line: 'Using Gemma until Dec 1' }];
    lines.render(one, () => ({ name: 'Anthropic', capped: true }));
    expect(root.textContent).toContain('Using Gemma until Dec 1.');
    expect(root.querySelector('button')).toBeNull();
    lines.render(one, () => ({ name: 'Anthropic', capped: false }));
    lines.raised('anthropic');
    expect(root.textContent).toContain('Switch back to Anthropic now?');
    button(root, 'Switch back now').click();
    await flush();
    expect(calls).toEqual([{ method: 'POST', path: '/api/spend/switch/anthropic/end', body: {} }]);
    expect(root.textContent).toContain('Anthropic is still at its cap. Raise Stop at first.');
    answer = json({ switches: [] });
    button(root, 'Switch back now').click();
    await flush();
    lines.render([], () => ({ name: 'Anthropic', capped: false }));
    expect(root.hidden).toBe(true);
  });

  it('speaks the line in the computer’s voice only when asked to, and never when muted', () => {
    const speak = vi.fn();
    vi.stubGlobal(
      'SpeechSynthesisUtterance',
      class {
        constructor(readonly text: string) {}
      },
    );
    const synth = { speak } as unknown as SpeechSynthesis;
    const msg = { type: 'error', code: 'capped', leg: 'voice', sentence: 'At the voice cap.', say: 'system' };
    expect(speakCapped(msg, true, synth)).toBe(false);
    expect(speakCapped({ ...msg, say: undefined }, false, synth)).toBe(false);
    expect(speakCapped({ ...msg, code: 'speech' }, false, synth)).toBe(false);
    expect(speak).not.toHaveBeenCalled();
    expect(speakCapped(msg, false, synth)).toBe(true);
    expect(speak).toHaveBeenCalledTimes(1);
    expect((speak.mock.calls[0]?.[0] as { text: string }).text).toBe('At the voice cap.');
  });
});
