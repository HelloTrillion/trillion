import { describe, expect, it } from 'vitest';
import { type Usage, type UsageRow, breakdown, dollars, pill, sentence, tokens } from '../src/usage';

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
});
