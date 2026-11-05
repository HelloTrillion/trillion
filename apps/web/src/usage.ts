/**
 * This month's API usage, as the core reports it (BRN-4).
 *
 * A model without prices has no cost, and that is shown as tokens, never as $0:
 * a bill must never look smaller than it is. Voice and hearing rows count characters
 * and seconds, and are shown as those, never as tokens.
 *
 * Each provider set up also comes with its caps and where it stands against them
 * (BRN-7): the pill turns amber near a soft cap and red at a hard one.
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
  /** Which cap the row counts against: null on this computer, absent from an older core. */
  readonly provider_id?: string | null;
  /** What `input_tokens` counts: tokens, characters spoken or seconds heard. */
  readonly unit?: Measure | null;
  /** The unpriced quantity in `unit`. */
  readonly unpriced?: number;
}

/** What a row measures. */
export type Measure = 'tokens' | 'chars' | 'seconds';
/** What a cap counts: dollars, or the provider's own unit while nothing prices it. */
export type Unit = 'dollars' | Measure;
export type Leg = 'model' | 'voice' | 'hearing';
export type SpendState = 'ok' | 'warned' | 'over_soft' | 'capped';

/** One provider set up, with its caps and where it stands this month (BRN-7). */
export interface ProviderUsage {
  readonly provider_id: string | null;
  readonly leg: Leg;
  readonly name: string;
  readonly model: string | null;
  readonly spent: number;
  readonly unit: Unit;
  readonly soft: number | null;
  readonly hard: number | null;
  readonly warn_pct: number;
  readonly state: SpendState;
  readonly split: { readonly conversation: number; readonly background: number };
  readonly paused: readonly string[];
  /** A switch made at the cap: `line` is the core's own words for it, in the user's timezone. */
  readonly switched: { readonly to: string; readonly label: string; readonly until_ms: number; readonly line?: string | null } | null;
  readonly unestimated: { readonly units: number; readonly unit: Measure } | null;
  /** `spent` includes an estimate for calls nothing priced. */
  readonly estimated?: boolean;
  readonly estimate: string;
  readonly pricing_url: string | null;
}

export interface Usage {
  readonly month: string;
  readonly since_ms: number;
  readonly rows: readonly UsageRow[];
  readonly total_cost_usd: number | null;
  readonly unpriced_tokens: number;
  readonly providers?: readonly ProviderUsage[];
  /** This month has OpenAI-compatible use from before spend caps, which no cap counts. */
  readonly uncounted_before_caps?: boolean;
}

/** Said with any dollar figure that includes an estimate. */
export const ESTIMATES = 'Includes estimates for calls without a price.';
/** Said once in Settings on an install whose upgrade month had uncounted use. */
export const UNCOUNTED_BEFORE_CAPS =
  'Use of OpenAI-compatible models earlier this month, before the limits were on, is not counted toward them.';

/** Whether the month's dollars include an estimate for calls without a price. */
function hasEstimates(u: Usage): boolean {
  return (u.providers ?? []).some((p) => p.provider_id !== null && p.provider_id !== 'local' && p.unit === 'dollars' && p.estimated === true);
}

function withEstimates(u: Usage, text: string): string {
  return text && hasEstimates(u) ? `${text} ${ESTIMATES}` : text;
}

export function tokens(n: number): string {
  if (n < 1_000) return String(n);
  if (n < 999_500) return `${Math.round(n / 1_000)}k`;
  return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
}

export function dollars(n: number): string {
  return n > 0 && n < 0.005 ? '<$0.01' : `$${n.toFixed(2)}`;
}

function minutes(seconds: number): string {
  const n = Math.round(seconds / 60);
  return `${n.toLocaleString('en')} minute${n === 1 ? '' : 's'}`;
}

const MEASURE_WORDS: Readonly<Record<Measure, string>> = { tokens: 'tokens', chars: 'characters', seconds: 'minutes' };

/** An amount spent in its unit: "$16.50", "340k tokens", "12 minutes". */
export function amountText(n: number, unit: Unit): string {
  if (unit === 'dollars') return dollars(n);
  if (unit === 'seconds') return minutes(n);
  return `${tokens(n)} ${MEASURE_WORDS[unit]}`;
}

/** A cap in its unit, as the core names it: "$50", "12M tokens", "1,500 minutes". */
export function capText(n: number, unit: Unit): string {
  if (unit === 'dollars') return Number.isInteger(n) ? `$${n.toLocaleString('en')}` : dollars(n);
  return amountText(n, unit);
}

function monthName(month: string): string {
  const [year, m] = month.split('-').map(Number);
  return new Date(Date.UTC(year ?? 1970, (m ?? 1) - 1, 1)).toLocaleString('en', { month: 'long', timeZone: 'UTC' });
}

/**
 * The month's total as the caps count it: each dollar-capped provider's `spent`
 * (priced calls, plus earlier units at the user's price or the table's ceiling), and the
 * tokens nothing counts in dollars: those `unestimated`, and those of a provider capped
 * in tokens. Without `providers` (an older core), the rows' own sums.
 */
function totals(u: Usage): { dollars: number | null; loose: number } {
  if (!u.providers) return { dollars: u.total_cost_usd, loose: u.unpriced_tokens };
  let dollars: number | null = null;
  let loose = 0;
  for (const p of u.providers) {
    if (p.provider_id === null || p.provider_id === 'local') continue;
    if (p.unit === 'dollars') dollars = (dollars ?? 0) + p.spent;
    else if (p.unit === 'tokens') loose += p.spent;
    if (p.unestimated?.unit === 'tokens') loose += p.unestimated.units;
  }
  // Float sums drift; a micro-dollar is plenty.
  return { dollars: dollars === null ? null : Math.round(dollars * 1e6) / 1e6, loose };
}

export function pill(u: Usage): string {
  if (!u.rows.length) return 'No API use yet';
  const { dollars: total, loose } = totals(u);
  if (total === null) return `${tokens(loose)} tokens`;
  if (!loose) return dollars(total);
  return `${dollars(total)} · ${tokens(loose)} unpriced`;
}

export function sentence(u: Usage): string {
  const when = monthName(u.month);
  if (!u.rows.length) return `No API use yet in ${when}.`;
  const { dollars: total, loose } = totals(u);
  if (total === null) return `API usage in ${when}: ${tokens(loose)} tokens, from models without prices.`;
  if (!loose) return withEstimates(u, `API usage in ${when}: ${dollars(total)}.`);
  return withEstimates(u, `API usage in ${when}: ${dollars(total)}, plus ${tokens(loose)} tokens from models without prices.`);
}

function rowDetail(r: UsageRow): string {
  const cost = r.cost_usd === null ? 'No price' : dollars(r.cost_usd);
  if (r.unit === 'chars' || r.unit === 'seconds') {
    // Characters spoken or seconds heard are kept in the input column.
    const left = r.unpriced ?? 0;
    const loose = r.cost_usd !== null && left > 0 ? `, ${amountText(left, r.unit)} unpriced` : '';
    return `${cost} · ${amountText(r.input_tokens, r.unit)}${loose}`;
  }
  const loose = r.cost_usd !== null && r.unpriced_tokens > 0 ? `, ${tokens(r.unpriced_tokens)} unpriced` : '';
  return `${cost} · ${tokens(r.input_tokens + r.cache_read_tokens + r.cache_write_tokens)} in, ${tokens(r.output_tokens)} out${loose}`;
}

export function breakdown(u: Usage): { name: string; detail: string }[] {
  return u.rows.map((r) => ({ name: `${r.provider} · ${r.model}`, detail: rowDetail(r) }));
}

export type PillState = 'ok' | 'warned' | 'capped';

const RANK: Readonly<Record<SpendState, number>> = { ok: 0, warned: 1, over_soft: 2, capped: 3 };

/** Where a provider stands against its caps, naming it and the cap; empty while below the warning. */
export function spendNote(p: ProviderUsage): string {
  if (p.state === 'ok') return '';
  if (p.state === 'capped') return `${p.name} has hit its ${capText(p.hard ?? 0, p.unit)} stop for this month.`;
  const soft = capText(p.soft ?? 0, p.unit);
  const spent = amountText(p.spent, p.unit);
  if (p.state === 'over_soft') return `${p.name} is past your ${soft} warning (${spent} so far).`;
  return `${p.name} is close to your ${soft} warning (${spent} so far).`;
}

/** A provider at a glance, for the pill's tooltip: "Anthropic: $16.40 so far. Warns at $20, stops at $50." */
export function glance(p: ProviderUsage): string {
  const parts = [p.soft === null ? '' : `Warns at ${capText(p.soft, p.unit)}`, p.hard === null ? '' : `stops at ${capText(p.hard, p.unit)}`].filter(Boolean);
  const limits = parts.length ? ` ${parts.join(', ').replace(/^stops/, 'Stops')}.` : '';
  return `${p.name}: ${amountText(p.spent, p.unit)} so far.${limits}`;
}

/**
 * The pill's colour, from the worst provider: amber near or past a warning, red at a
 * stop. `note` says where each provider in that state stands, for its accessible name;
 * `title` is the same at a glance, for its tooltip.
 */
export function pillState(u: Usage): { state: PillState; note: string; title: string } {
  const providers = u.providers ?? [];
  const worst = providers.reduce((w, p) => Math.max(w, RANK[p.state] ?? 0), 0);
  if (worst === 0) return { state: 'ok', note: '', title: '' };
  const these = providers.filter((p) => RANK[p.state] === worst);
  return {
    state: worst === RANK.capped ? 'capped' : 'warned',
    note: these.map(spendNote).join(' '),
    title: withEstimates(u, these.map(glance).join(' ')),
  };
}

/** This month's usage, or null when it cannot be read: the pill hides rather than guess. */
export async function fetchUsage(api: Api): Promise<Usage | null> {
  try {
    const response = await api('GET', '/api/usage');
    if (!response.ok) return null;
    const body = (await response.json()) as Usage;
    return Array.isArray(body?.rows) ? body : null;
  } catch {
    return null;
  }
}
