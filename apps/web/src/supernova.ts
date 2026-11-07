/**
 * The Supernova on the page (GAL-9). The core's attention engine decides when, and the
 * door sends it on the notice it belongs to; the page only fires what arrived. Nothing
 * else on the page may ask the galaxy for one.
 *
 * The door only hands notices to a free page, so a celebration never lands during a
 * reply. The page checks too (VOI-17, gated on both sides): if a reply is open, it
 * waits for the end, and one that has waited longer than a minute is dropped.
 */

import type { SupernovaTier } from '@hellotrillion/galaxy';
import type { ServerMsg } from './view';

const TIERS: ReadonlySet<string> = new Set(['small', 'medium', 'large']);
const RANK: Readonly<Record<SupernovaTier, number>> = { small: 0, medium: 1, large: 2 };
/** A celebration is for now: one held behind a reply longer than this is dropped. */
export const WAIT_MS = 60_000;

/** The tier a scheduled notice brings, or null for any other message. */
export function celebrationOf(msg: ServerMsg): SupernovaTier | null {
  if (msg.type !== 'notice' || msg['code'] !== 'schedule') return null;
  const tier = msg['celebrate'];
  return typeof tier === 'string' && TIERS.has(tier) ? (tier as SupernovaTier) : null;
}

export class Celebrations {
  private waiting: { tier: SupernovaTier; since: number } | null = null;

  constructor(
    private readonly fire: (tier: SupernovaTier) => void,
    /** The user's choice: Settings → Appearance → Celebrations. */
    private readonly on: () => boolean,
    private readonly now: () => number = Date.now,
  ) {}

  /** A message arrived. `live` is whether a reply is open on the page right now. */
  take(msg: ServerMsg, live: boolean): void {
    const tier = celebrationOf(msg);
    // Two waiting behind one reply are one celebration, the larger.
    if (tier !== null && this.on() && (this.waiting === null || RANK[tier] > RANK[this.waiting.tier])) {
      this.waiting = { tier, since: this.waiting?.since ?? this.now() };
    }
    this.settle(live);
  }

  /** The page changed: fire what waits, once nothing is in flight. */
  settle(live: boolean): void {
    const w = this.waiting;
    if (w === null || live) return;
    this.waiting = null;
    if (this.now() - w.since > WAIT_MS || !this.on()) return;
    this.fire(w.tier);
  }
}
