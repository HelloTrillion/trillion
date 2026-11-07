/**
 * Features plug in. Each file in `features/` exports `feature`, and `main.ts` loads them
 * all, so adding one edits no shared file (docs/specs/2026-11-07-plug-in-features.md).
 *
 * A feature gets what a Settings section needs: the panel, the API, the session, a way
 * to refresh the summaries and to open a conversation, and every message from the
 * session. It brings its own CSS (imported from its file) and its own icon (`addIcon`).
 */

import type { Panel } from './panel';
import type { Api } from './settings';

/** The Settings groups, in the order they appear. */
export const HER = 'Her';
export const YOU = 'You';
export const COMPUTER = 'This computer';

export type Message = { readonly type: string; readonly [key: string]: unknown };

export interface FeatureContext {
  readonly panel: Panel;
  readonly api: Api;
  /** Sends one message to the session; false when it is not connected. */
  readonly send: (body: Record<string, unknown>) => boolean;
  /** Refresh the summaries under each Settings label. */
  readonly refresh: () => void;
  /** Close Settings and open a conversation. */
  readonly openConversation: (id: string) => void;
  /** Hear every message from the session. */
  on(handler: (msg: Message) => void): void;
}

export interface Feature {
  setup(ctx: FeatureContext): void;
}

export interface Features {
  /** Hand a session message to every feature that asked to hear them. */
  dispatch(msg: Message): void;
  /** The features loaded, by file name: for tests and the About section. */
  readonly names: readonly string[];
}

type Modules = Record<string, { readonly feature?: Feature }>;

/** Set up every feature, in file-name order. Each places its own section (`after`). */
export function loadFeatures(
  ctx: Omit<FeatureContext, 'on'>,
  modules: Modules = import.meta.glob<{ feature?: Feature }>('./features/*.ts', { eager: true }),
): Features {
  const handlers: ((msg: Message) => void)[] = [];
  const names: string[] = [];
  for (const path of Object.keys(modules).sort()) {
    const feature = modules[path]?.feature;
    if (!feature) continue;
    feature.setup({ ...ctx, on: (handler) => void handlers.push(handler) });
    names.push(path.replace(/^.*\/|\.ts$/g, ''));
  }
  return {
    dispatch(msg) {
      for (const handler of handlers) handler(msg);
    },
    names,
  };
}
