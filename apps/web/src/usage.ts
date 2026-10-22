/**
 * This month's API usage, as the core reports it (BRN-4).
 *
 * A model without prices has no cost, and that is shown as tokens, never as $0:
 * a bill must never look smaller than it is.
 */

import type { Api } from './settings';

export interface UsageRow {
  readonly provider: string;
  readonly model: string;
  readonly input_tokens: number;
  readonly output_tokens: number;
  readonly cache_read_tokens: number;
  readonly cache_write_tokens: number;
  readonly cost_usd: number | null;
  readonly unpriced_turns: number;
  readonly unpriced_tokens: number;
}

export interface Usage {
  readonly month: string;
  readonly since_ms: number;
  readonly rows: readonly UsageRow[];
  readonly total_cost_usd: number | null;
  readonly unpriced_tokens: number;
}

export function tokens(n: number): string {
  if (n < 1_000) return String(n);
  if (n < 999_500) return `${Math.round(n / 1_000)}k`;
  return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
}

export function dollars(n: number): string {
  return n > 0 && n < 0.005 ? '<$0.01' : `$${n.toFixed(2)}`;
}

function monthName(month: string): string {
  const [year, m] = month.split('-').map(Number);
  return new Date(Date.UTC(year ?? 1970, (m ?? 1) - 1, 1)).toLocaleString('en', { month: 'long', timeZone: 'UTC' });
}

export function pill(u: Usage): string {
  if (!u.rows.length) return 'No API use yet';
  if (u.total_cost_usd === null) return `${tokens(u.unpriced_tokens)} tokens`;
  if (!u.unpriced_tokens) return dollars(u.total_cost_usd);
  return `${dollars(u.total_cost_usd)} · ${tokens(u.unpriced_tokens)} unpriced`;
}

export function sentence(u: Usage): string {
  const when = monthName(u.month);
  if (!u.rows.length) return `No API use yet in ${when}.`;
  if (u.total_cost_usd === null) return `API usage in ${when}: ${tokens(u.unpriced_tokens)} tokens, from models without prices.`;
  if (!u.unpriced_tokens) return `API usage in ${when}: ${dollars(u.total_cost_usd)}.`;
  return `API usage in ${when}: ${dollars(u.total_cost_usd)}, plus ${tokens(u.unpriced_tokens)} tokens from models without prices.`;
}

export function breakdown(u: Usage): { name: string; detail: string }[] {
  return u.rows.map((r) => ({
    name: `${r.provider} · ${r.model}`,
    detail: `${r.cost_usd === null ? 'No price' : dollars(r.cost_usd)} · ${tokens(r.input_tokens + r.cache_read_tokens + r.cache_write_tokens)} in, ${tokens(r.output_tokens)} out`,
  }));
}

/** This month's usage, or null when it cannot be read: the pill hides rather than guess. */
export async function fetchUsage(api: Api): Promise<Usage | null> {
  try {
    const response = await api('GET', '/api/usage');
    return response.ok ? ((await response.json()) as Usage) : null;
  } catch {
    return null;
  }
}
