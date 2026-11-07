/**
 * The Supernova on the page (GAL-9). The core's attention engine decides when, and the
 * door sends it on the notice it belongs to; the page only fires what arrived. Nothing
 * else on the page asks the galaxy for one (features/celebrations.ts is the one place).
 *
 * The door only hands notices to a free page, so a celebration never lands during a
 * reply. The page checks too (VOI-17, gated on both sides): if a reply is open, it
 * waits for the end, and one that has waited longer than a minute is dropped.
 */

import type { SupernovaTier } from '@hellotrillion/galaxy';
import type { Host } from './panel';

type Msg = { readonly type: string; readonly [key: string]: unknown };

const TIERS: ReadonlySet<string> = new Set(['small', 'medium', 'large']);
const RANK: Readonly<Record<SupernovaTier, number>> = { small: 0, medium: 1, large: 2 };
/** A celebration is for now: one held behind a reply longer than this is dropped. */
export const WAIT_MS = 60_000;

/** The tier a scheduled notice brings, or null for any other message. */
export function celebrationOf(msg: Msg): SupernovaTier | null {
  if (msg.type !== 'notice' || msg['code'] !== 'schedule') return null;
  const tier = msg['celebrate'];
  return typeof tier === 'string' && TIERS.has(tier) ? (tier as SupernovaTier) : null;
}

export class Celebrations {
  private waiting: { tier: SupernovaTier; since: number } | null = null;

  constructor(
    private readonly fire: (tier: SupernovaTier) => void,
    /** The user's choice: Settings → Celebrations. */
    private readonly on: () => boolean,
    private readonly now: () => number = Date.now,
  ) {}

  /** A message arrived. `live` is whether a reply is open on the page right now. */
  take(msg: Msg, live: boolean): void {
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

// ── The setting, kept in this browser ─────────────────────────────────────

const KEY = 'trillion-celebrations';

/** Whether the orb may celebrate a rare win. On unless turned off. */
export function savedCelebrations(): boolean {
  try {
    return localStorage.getItem(KEY) !== 'off';
  } catch {
    return true;
  }
}

export function saveCelebrations(on: boolean): void {
  try {
    localStorage.setItem(KEY, on ? 'on' : 'off');
  } catch {
    // not remembered, still applied for this page
  }
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text = ''): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}

/** Settings → Celebrations: one switch. */
export function createCelebrationsSettings(
  host: Host,
  changed: () => void,
): { open(): void; summary(): string } {
  const form = el('form', 'settings');
  form.addEventListener('submit', (ev) => ev.preventDefault());
  const title = el('h2', '', 'Celebrations');
  title.id = 'celebrations-title';
  host.setAttribute('aria-labelledby', title.id);
  const row = el('label', 'settings-check');
  const box = el('input');
  box.type = 'checkbox';
  box.setAttribute('role', 'switch');
  box.id = 'celebrations-on';
  const words = el('span');
  words.append(
    el('strong', '', 'Celebrate rare wins'),
    el(
      'small',
      '',
      'A burst of light from the orb for a rare win, such as a new helper finishing its first job or a routine’s 30th morning. With reduced motion, a soft glow. Never a sound.',
    ),
  );
  row.append(box, words);
  box.addEventListener('change', () => {
    saveCelebrations(box.checked);
    changed();
  });
  const actions = el('div', 'settings-actions');
  const close = el('button', 'settings-close', 'Done');
  close.type = 'button';
  close.addEventListener('click', () => host.close());
  actions.append(close);
  form.append(title, el('p', 'settings-note', 'Kept in this browser only.'), row, actions);
  host.replaceChildren(form);
  return {
    open() {
      box.checked = savedCelebrations();
      host.showModal();
    },
    summary: () => (savedCelebrations() ? 'On' : 'Off'),
  };
}
