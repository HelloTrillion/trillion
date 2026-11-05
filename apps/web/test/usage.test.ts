import { describe, expect, it } from 'vitest';
import { type ProviderUsage, type Usage, type UsageRow, amountText, breakdown, capText, dollars, fetchUsage, pill, pillState, sentence, tokens } from '../src/usage';
import type { Api } from '../src/settings';

const row = (over: Partial<UsageRow>): UsageRow => ({
  provider: 'anthropic',
  model: 'claude-test',
  input_tokens: 41_000,
  output_tokens: 9_000,
  cache_read_tokens: 2_000,
  cache_write_tokens: 0,
  cost_usd: 1.2,
  unpriced_turns: 0,
  unpriced_tokens: 0,
  ...over,
});
const priced = row({});
const unpriced = row({
  provider: 'custom',
  model: 'deepseek-chat',
  input_tokens: 300_000,
  output_tokens: 40_000,
  cache_read_tokens: 0,
  cost_usd: null,
  unpriced_turns: 57,
  unpriced_tokens: 340_000,
});
const month = (rows: UsageRow[], total: number | null, loose: number): Usage => ({
  month: '2026-10',
  since_ms: 0,
  rows,
  total_cost_usd: total,
  unpriced_tokens: loose,
});

describe('usage numbers', () => {
  it('shortens token counts', () => {
    expect([950, 1_500, 340_000, 999_400, 999_600, 1_250_000].map(tokens)).toEqual([
      '950', '2k', '340k', '999k', '1M', '1.3M',
    ]);
  });

  it('shows dollars to the cent, and a tiny spend as under a cent', () => {
    expect([1.2, 0, 0.001].map(dollars)).toEqual(['$1.20', '$0.00', '<$0.01']);
  });
});

describe('the pill', () => {
  it('says nothing is used yet in an empty month', () => {
    expect(pill(month([], null, 0))).toBe('No API use yet');
  });

  it('shows only dollars when every model has prices', () => {
    expect(pill(month([priced], 1.2, 0))).toBe('$1.20');
  });

  it('shows dollars and the unpriced tokens when only some have prices', () => {
    expect(pill(month([priced, unpriced], 1.2, 340_000))).toBe('$1.20 · 340k unpriced');
  });

  it('shows tokens, never $0, when no model has prices', () => {
    expect(pill(month([unpriced], null, 340_000))).toBe('340k tokens');
  });

  it('says the whole thing in a sentence for its label', () => {
    expect(sentence(month([], null, 0))).toBe('No API use yet in October.');
    expect(sentence(month([priced], 1.2, 0))).toBe('API usage in October: $1.20.');
    expect(sentence(month([priced, unpriced], 1.2, 340_000))).toBe(
      'API usage in October: $1.20, plus 340k tokens from models without prices.',
    );
    expect(sentence(month([unpriced], null, 340_000))).toBe(
      'API usage in October: 340k tokens, from models without prices.',
    );
  });
});

describe('the breakdown', () => {
  it('lists each provider and model with its cost or no price', () => {
    expect(breakdown(month([priced, unpriced], 1.2, 340_000))).toEqual([
      { name: 'anthropic · claude-test', detail: '$1.20 · 43k in, 9k out' },
      { name: 'custom · deepseek-chat', detail: 'No price · 300k in, 40k out' },
    ]);
  });

  it('shows a priced model\'s unpriced tokens too, so the bill is not understated', () => {
    const mixed = row({ unpriced_turns: 3, unpriced_tokens: 12_000 });
    expect(breakdown(month([mixed], 1.2, 12_000))).toEqual([
      { name: 'anthropic · claude-test', detail: '$1.20 · 43k in, 9k out, 12k unpriced' },
    ]);
  });
});

describe('fetching usage', () => {
  const reply = (body: unknown, status = 200): Api => async () => new Response(JSON.stringify(body), { status });

  it('is null when the request is refused', async () => {
    expect(await fetchUsage(reply({ error: 'nope' }, 401))).toBeNull();
  });

  it('is null when the request fails', async () => {
    const down: Api = async () => {
      throw new TypeError('network down');
    };
    expect(await fetchUsage(down)).toBeNull();
  });

  it('is null when the reply has no rows', async () => {
    expect(await fetchUsage(reply({}))).toBeNull();
  });

  it('is the usage when the reply is valid', async () => {
    const valid = month([priced], 1.2, 0);
    expect(await fetchUsage(reply(valid))).toEqual(valid);
  });
});

const provider = (over: Partial<ProviderUsage>): ProviderUsage => ({
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
const withProviders = (providers: ProviderUsage[]): Usage => ({ ...month([priced], 1.2, 0), providers });

describe('amounts in their unit', () => {
  it('names a cap the way the core does', () => {
    expect(capText(50, 'dollars')).toBe('$50');
    expect(capText(12.5, 'dollars')).toBe('$12.50');
    expect(capText(12_000_000, 'tokens')).toBe('12M tokens');
    expect(capText(1_000_000, 'chars')).toBe('1M characters');
    expect(capText(90_000, 'seconds')).toBe('1,500 minutes');
  });

  it('names an amount spent', () => {
    expect(amountText(16.5, 'dollars')).toBe('$16.50');
    expect(amountText(340_000, 'tokens')).toBe('340k tokens');
    expect(amountText(60, 'seconds')).toBe('1 minute');
  });
});

describe('the pill at the caps', () => {
  it('is plain while every provider is below its warning', () => {
    expect(pillState(withProviders([provider({})]))).toEqual({ state: 'ok', note: '', title: '' });
    expect(pillState(month([priced], 1.2, 0))).toEqual({ state: 'ok', note: '', title: '' });
  });

  it('is amber close to the warning, and names the provider and its limits', () => {
    const s = pillState(withProviders([provider({ state: 'warned', spent: 16.4 })]));
    expect(s.state).toBe('warned');
    expect(s.note).toBe('Anthropic is close to your $20 warning ($16.40 so far).');
    expect(s.title).toBe('Anthropic: $16.40 so far. Warns at $20, stops at $50.');
  });

  it('is amber past the warning', () => {
    const s = pillState(withProviders([provider({ state: 'over_soft', spent: 23.1 })]));
    expect(s.state).toBe('warned');
    expect(s.note).toBe('Anthropic is past your $20 warning ($23.10 so far).');
  });

  it('says a provider with no limits has none', () => {
    const s = pillState(withProviders([provider({ state: 'warned', soft: 20, hard: null })]));
    expect(s.title).toBe('Anthropic: $3.20 so far. Warns at $20.');
  });

  it('is red at a hard cap, the worst state across providers winning', () => {
    const s = pillState(
      withProviders([
        provider({ state: 'warned' }),
        provider({ provider_id: 'elevenlabs', leg: 'voice', name: 'ElevenLabs', state: 'capped', hard: 50 }),
      ]),
    );
    expect(s.state).toBe('capped');
    expect(s.note).toBe('ElevenLabs has hit its $50 stop for this month.');
    expect(s.title).toBe('ElevenLabs: $3.20 so far. Warns at $20, stops at $50.');
  });
});

describe('the breakdown in its own units', () => {
  it('shows characters and minutes as what they are, never as tokens', () => {
    const voice = row({ provider: 'elevenlabs', model: 'v3', unit: 'chars', input_tokens: 12_000, output_tokens: 0, cache_read_tokens: 0, cost_usd: 0.6 });
    const ears = row({ provider: 'deepgram', model: 'flux', unit: 'seconds', input_tokens: 600, output_tokens: 0, cache_read_tokens: 0, cost_usd: null, unpriced: 600, unpriced_turns: 2 });
    expect(breakdown(month([voice, ears], 0.6, 0))).toEqual([
      { name: 'elevenlabs · v3', detail: '$0.60 · 12k characters' },
      { name: 'deepgram · flux', detail: 'No price · 10 minutes' },
    ]);
  });
});

describe('the pill counts what the caps count', () => {
  // The rows price $7.19, but the caps also count earlier tokens at the user's price.
  const rows = [row({ cost_usd: 7.19, unpriced_tokens: 12_000, unpriced_turns: 3 })];
  const at = (providers: ProviderUsage[]): Usage => ({ ...month(rows, 7.19, 12_000), providers });

  it('shows the dollars the caps count, and no unpriced tokens that are counted', () => {
    const u = at([provider({ spent: 21.45 })]);
    expect(pill(u)).toBe('$21.45');
    expect(sentence(u)).toBe('API usage in October: $21.45.');
  });

  it('mentions only tokens that are really uncounted', () => {
    const u = at([provider({ spent: 21.45, unestimated: { units: 12_000, unit: 'tokens' } })]);
    expect(pill(u)).toBe('$21.45 · 12k unpriced');
    expect(sentence(u)).toBe('API usage in October: $21.45, plus 12k tokens from models without prices.');
  });

  it('adds every dollar provider, and counts a provider capped in tokens as unpriced', () => {
    const u = at([
      provider({ spent: 20 }),
      provider({ provider_id: 'elevenlabs', leg: 'voice', name: 'ElevenLabs', spent: 1.45 }),
      provider({ provider_id: 'custom:x', name: 'x', unit: 'tokens', spent: 340_000 }),
      provider({ provider_id: null, name: 'Your model', spent: 0, soft: null, hard: null }),
    ]);
    expect(pill(u)).toBe('$21.45 · 340k unpriced');
  });

  it('shows tokens alone when nothing is counted in dollars', () => {
    const u = at([provider({ provider_id: 'custom:x', name: 'x', unit: 'tokens', spent: 340_000 })]);
    expect(pill(u)).toBe('340k tokens');
  });

  it('keeps the old fields for the details by model', () => {
    expect(breakdown(at([provider({ spent: 21.45 })]))[0]?.detail).toBe('$7.19 · 43k in, 9k out, 12k unpriced');
  });
});
