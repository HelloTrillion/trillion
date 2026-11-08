import { describe, expect, it } from 'vitest';
import type { ObserveEvent } from '@hellotrillion/galaxy';
import { CLOSE_FORBIDDEN, CLOSE_UNAUTHENTICATED, type LiveState, connectObserve } from '../src/mind-live';

type Listener = (ev: { data?: unknown; code?: number }) => void;

class FakeSocket {
  readyState = 0;
  sent: string[] = [];
  closed = false;
  private listeners = new Map<string, Listener[]>();
  constructor(readonly url: string) {}
  addEventListener(type: string, l: Listener): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), l]);
  }
  send(d: string): void {
    this.sent.push(d);
  }
  close(): void {
    this.closed = true;
  }
  emit(type: string, ev: { data?: unknown; code?: number } = {}): void {
    for (const l of this.listeners.get(type) ?? []) l(ev);
  }
}

function harness() {
  const sockets: FakeSocket[] = [];
  const timers: { fn: () => void; ms: number }[] = [];
  const events: ObserveEvent[] = [];
  const states: LiveState[] = [];
  const live = connectObserve({
    url: 'ws://127.0.0.1:1/ws/observe',
    token: 'tok',
    onEvent: (e) => events.push(e),
    onState: (s) => states.push(s),
    socket: (url) => {
      const s = new FakeSocket(url);
      sockets.push(s);
      return s;
    },
    setTimer: (fn, ms) => timers.push({ fn, ms }),
    clearTimer: () => undefined,
    hidden: () => false,
  });
  return { live, sockets, timers, events, states };
}

describe('the observe socket', () => {
  it('says hello with the token, waits for ready, then hands on events and nothing else', () => {
    const h = harness();
    const s = h.sockets[0]!;
    s.emit('open');
    expect(JSON.parse(s.sent[0]!)).toEqual({ type: 'hello', token: 'tok' });
    s.emit('message', { data: JSON.stringify({ type: 'observe.ready' }) });
    expect(h.states).toEqual(['connecting', 'live']);
    s.emit('message', { data: JSON.stringify({ type: 'tool.start', name: 'web_search' }) });
    s.emit('message', { data: JSON.stringify({ type: 'mood', name: 'x' }) });
    s.emit('message', { data: 'not json' });
    expect(h.events).toEqual([{ type: 'tool.start', name: 'web_search' }]);
    // It never sends anything after the hello.
    expect(s.sent).toHaveLength(1);
  });

  it('reconnects with a growing wait, up to 15 seconds', () => {
    const h = harness();
    for (let i = 0; i < 6; i++) {
      h.sockets.at(-1)!.emit('close', { code: 1006 });
      h.timers.at(-1)!.fn();
    }
    expect(h.timers.map((t) => t.ms)).toEqual([1000, 2000, 4000, 8000, 15000, 15000]);
    expect(h.sockets).toHaveLength(7);
  });

  it('stops when refused: a wrong token (4401, or its error first) or another origin (4403)', () => {
    for (const code of [CLOSE_UNAUTHENTICATED, CLOSE_FORBIDDEN]) {
      const h = harness();
      h.sockets[0]!.emit('close', { code });
      expect(h.states.at(-1)).toBe('refused');
      expect(h.timers).toHaveLength(0);
    }
    const h = harness();
    const s = h.sockets[0]!;
    s.emit('message', { data: JSON.stringify({ type: 'error', code: 'unauthenticated' }) });
    s.emit('close', { code: CLOSE_UNAUTHENTICATED });
    expect(s.closed).toBe(true);
    expect(h.states.filter((x) => x === 'refused')).toHaveLength(1);
    expect(h.timers).toHaveLength(0);
  });

  it('closes while the tab is hidden and opens again when shown', () => {
    const h = harness();
    h.live.visibility(true);
    expect(h.sockets[0]!.closed).toBe(true);
    h.sockets[0]!.emit('close', { code: 1000 });
    expect(h.timers).toHaveLength(0);
    h.live.visibility(false);
    expect(h.sockets).toHaveLength(2);
    h.live.close();
    expect(h.sockets[1]!.closed).toBe(true);
  });
});
