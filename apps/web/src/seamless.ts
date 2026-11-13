/**
 * The Seamless measures' one line in Settings → Confirmations: how many asks this week,
 * and how many of them were approved within 30 seconds with nothing changed. A nudge,
 * not a dashboard; `trillion doctor --seamless` has the rest. Counted on this computer
 * only (ONB-6), read from `GET /api/seamless`.
 */

import type { Api } from './settings';

export const SEAMLESS_PATH = '/api/seamless';

/** One window of asks, as the server counts them from the decision record. */
export interface AsksWindow {
  readonly days: number;
  /** Cards shown. */
  readonly shown: number;
  /** Of those, approved within 30 seconds of being shown, with nothing changed. */
  readonly quick: number;
}

export interface SeamlessState {
  readonly asks: readonly AsksWindow[];
}

/** "Last 7 days: 12 asks, 9 approved within 30 seconds unchanged." Empty if unknown. */
export function asksLine(s: unknown): string {
  const asks = (s as Partial<SeamlessState> | null)?.asks;
  if (!Array.isArray(asks)) return '';
  const week = (asks as AsksWindow[]).find((a) => a?.days === 7);
  if (!week || !Number.isInteger(week.shown) || !Number.isInteger(week.quick)) return '';
  if (week.shown === 0) return 'Last 7 days: no asks.';
  return `Last 7 days: ${week.shown} ${week.shown === 1 ? 'ask' : 'asks'}, ${week.quick} approved within 30 seconds unchanged.`;
}

/** Reads the line. Anything that goes wrong leaves it out rather than showing an error. */
export function asksLoader(api: Api): () => Promise<string> {
  return async () => {
    try {
      const r = await api('GET', SEAMLESS_PATH);
      return r.ok ? asksLine(await r.json()) : '';
    } catch {
      return '';
    }
  };
}
