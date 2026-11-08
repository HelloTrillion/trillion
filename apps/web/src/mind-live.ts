/**
 * The Living Mind's live feed: `/ws/observe`, read-only. It says hello with the token,
 * waits for `observe.ready`, then hands each event on. It reconnects with a growing
 * wait (1 s doubling to 15 s), closes while the tab is hidden (nothing is drawn then,
 * and a queue of stale events would only burst on return), and stops when refused.
 */

import { type ObserveEvent, parseObserveEvent } from '@hellotrillion/galaxy';

export type LiveState = 'connecting' | 'live' | 'retrying' | 'refused' | 'closed';

/** The server's close code for a bad token or origin (api/app.py CLOSE_FORBIDDEN). */
export const CLOSE_FORBIDDEN = 4403;
const FIRST_WAIT_MS = 1000;
const LONGEST_WAIT_MS = 15_000;

interface SocketLike {
  readyState: number;
  send(data: string): void;
  close(): void;
  addEventListener(type: 'open' | 'close' | 'message' | 'error', listener: (ev: { data?: unknown; code?: number }) => void): void;
}

export interface LiveOptions {
  readonly url: string;
  readonly token: string;
  readonly onEvent: (event: ObserveEvent) => void;
  readonly onState?: (state: LiveState) => void;
  readonly socket?: (url: string) => SocketLike;
  readonly setTimer?: (fn: () => void, ms: number) => unknown;
  readonly clearTimer?: (handle: unknown) => void;
  /** Whether the page is hidden now; defaults to `document.hidden`. */
  readonly hidden?: () => boolean;
}

export interface Live {
  /** The tab was hidden or shown again. */
  visibility(hidden: boolean): void;
  close(): void;
}

export function connectObserve(opts: LiveOptions): Live {
  const make = opts.socket ?? ((url: string) => new WebSocket(url) as unknown as SocketLike);
  const setTimer = opts.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
  const clearTimer = opts.clearTimer ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>));
  let ws: SocketLike | null = null;
  let wait = FIRST_WAIT_MS;
  let timer: unknown = null;
  let stopped = false;
  let paused = opts.hidden?.() ?? (typeof document !== 'undefined' && document.hidden);

  const state = (s: LiveState): void => opts.onState?.(s);

  const open = (): void => {
    if (stopped || paused) return;
    state('connecting');
    const sock = make(opts.url);
    ws = sock;
    sock.addEventListener('open', () => {
      sock.send(JSON.stringify({ type: 'hello', token: opts.token }));
    });
    sock.addEventListener('message', (ev) => {
      if (ws !== sock) return;
      let msg: unknown;
      try {
        msg = JSON.parse(String(ev.data));
      } catch {
        return;
      }
      if ((msg as { type?: unknown })?.type === 'observe.ready') {
        wait = FIRST_WAIT_MS;
        state('live');
        return;
      }
      const event = parseObserveEvent(msg);
      if (event) opts.onEvent(event);
    });
    sock.addEventListener('close', (ev) => {
      if (ws !== sock) return;
      ws = null;
      if (stopped || paused) return;
      if (ev.code === CLOSE_FORBIDDEN) {
        stopped = true;
        state('refused');
        return;
      }
      state('retrying');
      timer = setTimer(open, wait);
      wait = Math.min(wait * 2, LONGEST_WAIT_MS);
    });
  };

  const drop = (): void => {
    if (timer !== null) clearTimer(timer);
    timer = null;
    const sock = ws;
    ws = null;
    sock?.close();
  };

  open();
  return {
    visibility(hidden) {
      if (stopped || hidden === paused) return;
      paused = hidden;
      if (hidden) {
        drop();
        state('closed');
      } else {
        wait = FIRST_WAIT_MS;
        open();
      }
    },
    close() {
      stopped = true;
      drop();
      state('closed');
    },
  };
}
