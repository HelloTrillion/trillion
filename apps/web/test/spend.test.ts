// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createUsageSettings } from '../src/browser';
import { createListeningSettings } from '../src/listening';
import type { Host } from '../src/panel';
import { type Api, createSettings } from '../src/settings';
import { createSpeechSettings } from '../src/speech';
import { FREE, type Cost, createCapStep, createSpendCards, speakCapped } from '../src/spend';
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

const anthropicCost: Cost = {
  provider_id: 'anthropic',
  name: 'Anthropic',
  estimate: 'Roughly $0.40 an hour of talk on Sonnet (about 40 short turns), plus routines.',
  pricing_url: 'https://example.com/pricing',
  caps: { soft: 20, hard: 50, warn_pct: 80, unit: 'dollars' },
};
const localCost: Cost = { provider_id: 'local', name: 'this computer', estimate: FREE, pricing_url: null, caps: null };

afterEach(() => {
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

// ── Key steps ─────────────────────────────────────────────────────────────

describe('the caps in a key step', () => {
  it('shows the cost line, the pricing link and the caps it will have', () => {
    const step = createCapStep('t');
    document.body.append(step.element);
    step.show(anthropicCost);
    expect(step.element.textContent).toContain(anthropicCost.estimate);
    expect(step.element.querySelector('a')?.getAttribute('href')).toBe('https://example.com/pricing');
    expect(field(step.element, 'Soft cap a month ($)').value).toBe('20');
    expect(field(step.element, 'Hard cap a month ($)').value).toBe('50');
  });

  it('says a local choice costs nothing and has no cap, with no fields', () => {
    const step = createCapStep('t');
    document.body.append(step.element);
    step.show(localCost);
    expect(step.element.textContent).toContain('Runs on this computer: no cost, no cap.');
    expect(() => field(step.element, 'Soft cap a month ($)')).toThrow();
  });

  it('saves the caps only when they were changed from what the core will keep', async () => {
    const { api, calls } = fakeApi({ 'PUT /api/settings/spend/anthropic': () => json({}) });
    const step = createCapStep('t');
    document.body.append(step.element);
    step.show(anthropicCost);
    expect(await step.save(api)).toBeNull();
    expect(calls).toEqual([]);
    type(field(step.element, 'Hard cap a month ($)'), '80');
    expect(await step.save(api)).toBeNull();
    expect(calls).toEqual([{ method: 'PUT', path: '/api/settings/spend/anthropic', body: { soft: 20, hard: 80 } }]);
  });

  it('refuses a hard cap below the soft one before sending anything', async () => {
    const { api, calls } = fakeApi({});
    const step = createCapStep('t');
    document.body.append(step.element);
    step.show(anthropicCost);
    type(field(step.element, 'Hard cap a month ($)'), '10');
    expect(step.problem()).toBe('The hard cap must be at least the soft cap.');
    expect(await step.save(api)).toBe('The hard cap must be at least the soft cap.');
    expect(calls).toEqual([]);
  });

  it('says plainly when nothing is capped', () => {
    const step = createCapStep('t');
    document.body.append(step.element);
    step.show(anthropicCost);
    type(field(step.element, 'Soft cap a month ($)'), '');
    type(field(step.element, 'Hard cap a month ($)'), '');
    expect(step.element.textContent).toContain('No hard cap: nothing stops spending on Anthropic.');
  });

  it('encodes the provider id in the path', async () => {
    const { api, calls } = fakeApi({ 'PUT /api/settings/spend/custom%3Aopenrouter.ai': () => json({}) });
    const step = createCapStep('t');
    document.body.append(step.element);
    step.show({ ...anthropicCost, provider_id: 'custom:openrouter.ai', name: 'OpenRouter' });
    type(field(step.element, 'Soft cap a month ($)'), '5');
    await step.save(api);
    expect(calls[0]?.path).toBe('/api/settings/spend/custom%3Aopenrouter.ai');
  });

  it('shows a cap the core counts in units in that unit, minutes for hearing', async () => {
    const { api, calls } = fakeApi({ 'PUT /api/settings/spend/deepgram': () => json({}) });
    const step = createCapStep('t');
    document.body.append(step.element);
    step.show({ ...anthropicCost, provider_id: 'deepgram', name: 'Deepgram', caps: { soft: 36_000, hard: 90_000, warn_pct: 80, unit: 'seconds' } });
    expect(field(step.element, 'Hard cap a month (minutes)').value).toBe('1500');
    type(field(step.element, 'Hard cap a month (minutes)'), '2000');
    await step.save(api);
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
  it('shows the chosen provider’s cost and caps, and a local model as free', async () => {
    const root = document.createElement('div');
    document.body.append(root);
    const { api } = fakeApi({ 'GET /api/settings/model': () => json(modelState) });
    await createSettings(fakeHost(root), api).open();
    expect(root.textContent).toContain(anthropicCost.estimate);
    expect(field(root, 'Hard cap a month ($)').value).toBe('50');
    const local = root.querySelector<HTMLInputElement>('input[value="local"]')!;
    local.checked = true;
    local.dispatchEvent(new Event('change'));
    expect(root.textContent).toContain(FREE);
  });

  it('saves the key, then the caps the user changed', async () => {
    const root = document.createElement('div');
    document.body.append(root);
    const { api, calls } = fakeApi({
      'GET /api/settings/model': () => json(modelState),
      'POST /api/settings/model/models': () => json({ models: [{ id: 'claude-x', label: 'Claude X' }], cost: anthropicCost }),
      'PUT /api/settings/model': () => json(modelState),
      'PUT /api/settings/spend/anthropic': () => json({}),
    });
    const host = fakeHost(root);
    await createSettings(host, api).open();
    button(root, 'Check and list models').click();
    await flush();
    type(field(root, 'Soft cap a month ($)'), '10');
    button(root, 'Save').click();
    await flush();
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      'GET /api/settings/model',
      'POST /api/settings/model/models',
      'PUT /api/settings/model',
      'PUT /api/settings/spend/anthropic',
    ]);
    expect(calls[3]?.body).toEqual({ soft: 10, hard: 50 });
    expect(host.open).toBe(false);
  });
});

describe('Settings, Voice and Hearing: the key steps', () => {
  it('shows a paid voice’s cost and caps, and the computer’s voice as free', async () => {
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
    expect(root.textContent).toContain('Roughly $0.30 an hour of speaking.');
    expect(field(root, 'Soft cap a month ($)').value).toBe('20');
    const system = root.querySelector<HTMLInputElement>('input[value="system"]')!;
    system.checked = true;
    system.dispatchEvent(new Event('change'));
    expect(root.textContent).toContain(FREE);
  });

  it('shows hearing’s cost and caps', async () => {
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
    expect(root.textContent).toContain('Roughly $0.46 an hour of listening.');
    expect(field(root, 'Hard cap a month ($)').value).toBe('50');
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
  estimate: 'Roughly $0.40 an hour of talk on Sonnet (about 40 short turns), plus routines.',
  pricing_url: 'https://example.com/pricing',
  ...over,
});
const usageOf = (providers: ProviderUsage[]): Usage => ({ month: '2026-11', since_ms: 0, rows: [], total_cost_usd: null, unpriced_tokens: 0, providers });

async function openUsage(providers: ProviderUsage[], answers: Record<string, (body: unknown) => Response> = {}) {
  const root = document.createElement('div');
  document.body.append(root);
  const { api, calls } = fakeApi({ 'GET /api/settings/spend/prices': () => json({ prices: {} }), ...answers });
  const load = vi.fn(async () => usageOf(providers));
  await createUsageSettings(fakeHost(root), load, api).open();
  await flush();
  const block = (name: string): HTMLElement => {
    const found = [...root.querySelectorAll<HTMLElement>('.spend-provider')].find((b) => b.querySelector('h3')?.textContent === name);
    if (!found) throw new Error(`no block "${name}"`);
    return found;
  };
  return { root, calls, load, block };
}

describe('Settings, Usage: one block per provider', () => {
  it('says what it unlocks, what it costs, the free option, the month so far and the pricing page', async () => {
    const { block } = await openUsage([provider()]);
    const b = block('Anthropic');
    expect(b.textContent).toContain(
      'Anthropic: how she thinks. Roughly $0.40 an hour of talk on Sonnet (about 40 short turns), plus routines. Free option: a local model with Ollama.',
    );
    expect(b.textContent).toContain('This month: $3.20 (conversation $2.10, background $1.10).');
    expect(b.querySelector('a')?.getAttribute('href')).toBe('https://example.com/pricing');
  });

  it('saves the caps and the warning, and shows the core’s refusal inline', async () => {
    let status = 200;
    const { block, calls } = await openUsage([provider()], {
      'PUT /api/settings/spend/anthropic': () => (status === 200 ? json(provider({ soft: 25, hard: 60 })) : json({ error: 'too_high' }, 400)),
    });
    const b = block('Anthropic');
    type(field(b, 'Soft cap a month ($)'), '25');
    type(field(b, 'Hard cap a month ($)'), '60');
    type(field(b, 'Warn at (% of the soft cap)'), '90');
    button(b, 'Save caps').click();
    await flush();
    expect(calls.at(-1)).toEqual({ method: 'PUT', path: '/api/settings/spend/anthropic', body: { soft: 25, hard: 60, warn_pct: 90 } });
    expect(b.textContent).toContain('Caps saved.');
    status = 400;
    button(b, 'Save caps').click();
    await flush();
    expect(b.textContent).toContain('That is higher than a cap can go.');
  });

  it('checks the fields before sending', async () => {
    const { block, calls } = await openUsage([provider()]);
    const b = block('Anthropic');
    type(field(b, 'Warn at (% of the soft cap)'), '0');
    button(b, 'Save caps').click();
    await flush();
    expect(b.textContent).toContain('The warning must be between 1 and 100 percent.');
    type(field(b, 'Warn at (% of the soft cap)'), '80');
    type(field(b, 'Soft cap a month ($)'), 'lots');
    button(b, 'Save caps').click();
    await flush();
    expect(b.textContent).toContain('Caps are numbers of zero or more, or empty for no cap.');
    expect(calls.filter((c) => c.method === 'PUT')).toEqual([]);
  });

  it('shows the core’s field errors in plain words', async () => {
    const { block } = await openUsage([provider()], {
      'PUT /api/settings/spend/anthropic': () => json({ error: 'bad_request', fields: ['hard'] }, 422),
    });
    const b = block('Anthropic');
    button(b, 'Save caps').click();
    await flush();
    expect(b.textContent).toContain('Check the hard cap.');
  });

  it('asks for a price for an unpriced model, and saves it with the prices kept', async () => {
    const other = { 'custom:other.example': { m: { input: 1, output: 2 } } };
    const { block, calls } = await openUsage(
      [provider({ provider_id: 'custom:api.example.com', name: 'api.example.com', model: 'mystery', unit: 'tokens', soft: 5e6, hard: 12e6, unestimated: { units: 340_000, unit: 'tokens' } })],
      {
        'GET /api/settings/spend/prices': () => json({ prices: other }),
        'PUT /api/settings/spend/prices': (body) => json({ prices: body }),
      },
    );
    const b = block('api.example.com');
    expect(b.textContent).toContain('340k tokens not counted: add a price.');
    type(field(b, 'Input, $ per million tokens'), '0.5');
    type(field(b, 'Output, $ per million tokens'), '1.5');
    button(b, 'Save price').click();
    await flush();
    expect(calls.find((c) => c.method === 'PUT')).toEqual({
      method: 'PUT',
      path: '/api/settings/spend/prices',
      body: { ...other, 'custom:api.example.com': { mystery: { input: 0.5, output: 1.5 } } },
    });
    expect(b.textContent).toContain('Price saved.');
  });

  it('shows a refused price in plain words', async () => {
    const { block } = await openUsage([provider({ provider_id: 'custom:api.example.com', name: 'api.example.com', model: 'mystery', unit: 'tokens' })], {
      'PUT /api/settings/spend/prices': () => json({ error: 'zero_price' }, 400),
    });
    const b = block('api.example.com');
    type(field(b, 'Input, $ per million tokens'), '0');
    type(field(b, 'Output, $ per million tokens'), '0');
    button(b, 'Save price').click();
    await flush();
    expect(b.textContent).toContain('A price of zero would stop this model being counted.');
  });

  it('offers a voice a rate override per thousand characters', async () => {
    const { block, calls } = await openUsage(
      [provider({ provider_id: 'elevenlabs', leg: 'voice', name: 'ElevenLabs', model: null })],
      { 'PUT /api/settings/spend/prices': (body) => json({ prices: body }) },
    );
    const b = block('ElevenLabs');
    expect(b.textContent).toContain('Free option: this computer’s own voice.');
    type(field(b, 'Your rate, $ per thousand characters'), '0.2');
    button(b, 'Save rate').click();
    await flush();
    expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ elevenlabs: { '*': { unit: 'chars', per_unit: 0.2 } } });
  });

  it('offers hearing a rate override per minute', async () => {
    const { block, calls } = await openUsage(
      [provider({ provider_id: 'deepgram', leg: 'hearing', name: 'Deepgram', model: null })],
      { 'PUT /api/settings/spend/prices': (body) => json({ prices: body }) },
    );
    const b = block('Deepgram');
    type(field(b, 'Your rate, $ per minute'), '0.01');
    button(b, 'Save rate').click();
    await flush();
    expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ deepgram: { '*': { unit: 'seconds', per_unit: 0.01 } } });
  });

  it('lists paused work in plain words, and a switch with its end', async () => {
    const { block } = await openUsage([
      provider({ state: 'capped', paused: ['routines', 'watchers', 'channels'], switched: { to: 'local/gemma', label: 'Gemma', until_ms: new Date(2026, 11, 1).getTime() } }),
    ]);
    const b = block('Anthropic');
    expect(b.textContent).toContain('Paused until the cap lifts: routines, watchers and channel replies.');
    expect(b.textContent).toContain('Using Gemma in your conversations until Dec 1.');
  });

  it('says a provider on this computer costs nothing and has no cap', async () => {
    const { block } = await openUsage([
      provider({ provider_id: null, name: 'Your model', soft: null, hard: null, spent: 0, estimate: FREE, pricing_url: null, split: { conversation: 0, background: 0 } }),
    ]);
    const b = block('Your model');
    expect(b.textContent).toContain(FREE);
    expect(b.querySelectorAll('input')).toHaveLength(0);
  });
});

// ── The spend card ───────────────────────────────────────────────────────

const card = (over: Record<string, unknown> = {}) => ({
  type: 'spend.card',
  id: 'c1',
  provider_id: 'anthropic',
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

function cards() {
  const root = document.createElement('div');
  document.body.append(root);
  const sent: Record<string, unknown>[] = [];
  const spend = createSpendCards(root, (body) => sent.push(body));
  return { root, sent, spend };
}

describe('the spend card', () => {
  it('shows the line, a raise field prefilled, and a button for each choice, sending nothing on its own', () => {
    const { root, sent, spend } = cards();
    spend.handle(card());
    expect(root.textContent).toContain("I've reached the $50 cap for Anthropic this month");
    expect(field(root, 'New hard cap ($)').value).toBe('75');
    expect([...root.querySelectorAll('button')].map((b) => b.textContent)).toEqual(['Raise the cap', 'Use gemma until Dec 1', 'Stop for now']);
    expect(root.querySelector('[aria-live]')).not.toBeNull();
    expect(sent).toEqual([]);
  });

  it('sends the choice on a click, with the amount typed for a raise', () => {
    const { root, sent, spend } = cards();
    spend.handle(card());
    type(field(root, 'New hard cap ($)'), '100');
    button(root, 'Raise the cap').click();
    expect(sent).toEqual([{ type: 'spend.choice', id: 'c1', choice: 'raise', amount: 100 }]);
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
    type(field(root, 'New hard cap ($)'), 'more');
    button(root, 'Raise the cap').click();
    expect(sent).toEqual([]);
    expect(root.textContent).toContain('Type the new cap as a number.');
  });

  it('shows a short reason when the core refuses, and can be tried again', () => {
    const { root, sent, spend } = cards();
    spend.handle(card());
    button(root, 'Raise the cap').click();
    spend.handle({ type: 'spend.refused', id: 'c1', code: 'bad_amount' });
    expect(root.textContent).toContain('The new cap must be above the cap now and what is spent so far.');
    expect(button(root, 'Raise the cap').disabled).toBe(false);
    spend.handle({ type: 'spend.refused', id: 'c1', code: 'target_capped' });
    expect(root.textContent).toContain('That one has reached its own cap now. Choose another.');
    expect(sent).toHaveLength(1);
  });

  it('goes once the cap is raised or switched', () => {
    const { root, spend } = cards();
    spend.handle(card());
    spend.handle({ type: 'spend.resolved', id: 'c1', status: 'raised' });
    expect(root.textContent).toBe('');
    expect(root.hidden).toBe(true);
  });

  it('stays reachable from the banner after Stop for now', () => {
    const { root, sent, spend } = cards();
    spend.handle(card());
    button(root, 'Stop for now').click();
    spend.handle({ type: 'spend.resolved', id: 'c1', status: 'stopped' });
    expect(root.hidden).toBe(false);
    expect(root.querySelector('.spend-card')).toBeNull();
    expect(root.textContent).toContain('Anthropic is stopped at its $50 cap.');
    button(root, 'Choose again').click();
    expect(button(root, 'Raise the cap').disabled).toBe(false);
    button(root, 'Raise the cap').click();
    expect(sent.at(-1)).toEqual({ type: 'spend.choice', id: 'c1', choice: 'raise', amount: 75 });
  });

  it('shows a stopped card sent again after a reload as the banner line', () => {
    const { root, spend } = cards();
    spend.handle(card({ status: 'stopped' }));
    expect(root.querySelector('.spend-card')).toBeNull();
    expect(button(root, 'Choose again')).toBeTruthy();
  });

  it('offers the voice and hearing choices in their own words, and a raise in minutes for hearing', () => {
    const { root, sent, spend } = cards();
    spend.handle(
      card({ id: 'v', leg: 'voice', provider_id: 'elevenlabs', options: [{ choice: 'raise', amount: 75, unit: 'dollars' }, { choice: 'switch:system', label: "the computer's voice", until_ms: 1, until: 'Dec 1' }, { choice: 'text' }] }),
    );
    expect([...root.querySelectorAll('button')].map((b) => b.textContent)).toEqual(['Raise the cap', "Use the computer's voice until Dec 1", 'Text only']);
    spend.reset();
    spend.handle(card({ id: 'h', leg: 'hearing', provider_id: 'deepgram', unit: 'seconds', cap: 90_000, options: [{ choice: 'raise', amount: 135_000, unit: 'seconds' }, { choice: 'type' }] }));
    expect(field(root, 'New hard cap (minutes)').value).toBe('2250');
    button(root, 'Type for now').click();
    button(root, 'Raise the cap');
    expect(sent).toEqual([{ type: 'spend.choice', id: 'h', choice: 'type' }]);
  });

  it('sends a raise in minutes as seconds', () => {
    const { root, sent, spend } = cards();
    spend.handle(card({ id: 'h', leg: 'hearing', unit: 'seconds', cap: 90_000, options: [{ choice: 'raise', amount: 135_000, unit: 'seconds' }, { choice: 'type' }] }));
    type(field(root, 'New hard cap (minutes)'), '3000');
    button(root, 'Raise the cap').click();
    expect(sent).toEqual([{ type: 'spend.choice', id: 'h', choice: 'raise', amount: 180_000 }]);
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

  it('turns the microphone off when hearing is capped', () => {
    const v = update({ ...INITIAL, link: 'ready', mic: true, hearing: 'half a' }, { type: 'error', code: 'capped', leg: 'hearing', sentence: 'At the cap.' });
    expect(v.mic).toBe(false);
  });

  it('shows the switch lines while they last, and clears them', () => {
    let v = update(INITIAL, { type: 'spend.switch', switches: [{ provider_id: 'anthropic', leg: 'model', to: 'local/gemma', label: 'Gemma', until_ms: 1, line: 'Using Gemma until Dec 1' }] });
    expect(v.switches).toEqual(['Using Gemma until Dec 1']);
    v = update(v, { type: 'spend.switch', switches: [] });
    expect(v.switches).toEqual([]);
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
