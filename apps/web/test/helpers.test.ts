// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ARRIVE_MS } from '../src/approvals';
import {
  type Helper,
  type HelperList,
  buttonLabel,
  createHelpers,
  elapsedText,
  groupOf,
  statusLine,
  summaryLine,
  timeText,
} from '../src/helpers';
import type { Api } from '../src/settings';

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

const flush = async (): Promise<void> => {
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0));
};

// Local times, so the clock words do not depend on the machine's zone.
const at = (h: number, m: number, day = 5): number => new Date(2026, 10, day, h, m).getTime();
const NOW = at(9, 30);

const helper = (over: Partial<Helper> = {}): Helper => ({
  id: 'routine:aaaaaaaa',
  kind: 'routine',
  name: 'Morning brief',
  job: 'Reads your inbox and calendar',
  state: 'idle',
  doing: null,
  since_ms: null,
  last_run: null,
  next_ms: at(9, 55),
  waiting: [],
  conversation_id: null,
  ...over,
});

const list = (helpers: Helper[]): HelperList => ({ helpers, needs_you: helpers.filter((h) => h.state === 'needs_you').length });

const approval = (id = 'p1', text = 'Send a message'): Helper['waiting'][number] => ({ type: 'approval', id, text, at_ms: at(9, 0) });

afterEach(() => {
  document.body.replaceChildren();
  vi.useRealTimers();
});

describe('the words', () => {
  it('says a time today as the clock, and another day with its weekday', () => {
    expect(timeText(at(9, 55), NOW)).toBe('9:55');
    expect(timeText(at(14, 5), NOW)).toBe('14:05');
    expect(timeText(at(7, 0, 6), NOW)).toMatch(/^[A-Z][a-z]{2} 7:00$/);
  });

  it('says how long a run has gone', () => {
    expect(elapsedText(12_000)).toBe('0:12');
    expect(elapsedText(75_000)).toBe('1:15');
    expect(elapsedText(3_725_000)).toBe('1:02:05');
    expect(elapsedText(-5)).toBe('0:00');
  });

  it('puts paused and capped helpers in Idle', () => {
    expect(['needs_you', 'working', 'idle', 'paused', 'capped'].map((state) => groupOf(helper({ state: state as Helper['state'] })))).toEqual([
      'needs_you',
      'working',
      'idle',
      'idle',
      'idle',
    ]);
  });

  it('gives each row one short status line, as the spec words it', () => {
    expect(statusLine(helper(), NOW)).toBe('next 9:55');
    expect(statusLine(helper({ state: 'paused' }), NOW)).toBe('paused');
    expect(statusLine(helper({ state: 'capped' }), NOW, 'Anthropic')).toBe('paused: Anthropic is at its cap');
    expect(statusLine(helper({ state: 'capped' }), NOW, null)).toBe('paused: her model is at its cap');
    expect(statusLine(helper({ last_run: { at_ms: at(7, 0), outcome: 'failed', reason: "couldn't reach the inbox" } }), NOW)).toBe(
      "failed at 7:00: couldn't reach the inbox",
    );
    expect(statusLine(helper({ state: 'working', doing: 'Reading your inbox', since_ms: NOW - 12_000 }), NOW)).toBe('Reading your inbox · 0:12');
    expect(statusLine(helper({ state: 'working', doing: null, since_ms: null }), NOW)).toBe('Working');
    expect(statusLine(helper({ state: 'needs_you', waiting: [approval()] }), NOW)).toBe('Approve a change');
    expect(statusLine(helper({ state: 'needs_you', waiting: [{ type: 'question', id: 'q', text: 'Which one?', at_ms: null }] }), NOW)).toBe(
      'A question for you',
    );
    expect(statusLine(helper({ state: 'needs_you', waiting: [{ type: 'step', id: 'connect:email', text: 'Connect Google to use Urgent email', at_ms: null }] }), NOW)).toBe(
      'Connect Google to use Urgent email',
    );
    expect(statusLine(helper({ next_ms: null }), NOW)).toBe('Reads your inbox and calendar');
  });

  it('sums her helpers up in a line', () => {
    expect(summaryLine(list([]))).toBe('All quiet');
    expect(summaryLine(list([helper()]))).toBe('All quiet');
    expect(
      summaryLine(list([helper({ state: 'working' }), helper({ state: 'working' }), helper({ state: 'needs_you', waiting: [approval()] })])),
    ).toBe('2 working, 1 needs you');
    expect(summaryLine(list([helper({ state: 'needs_you' }), helper({ state: 'needs_you' })]))).toBe('2 need you');
  });

  it('names the button with what needs you', () => {
    expect(buttonLabel(0)).toBe('Helpers');
    expect(buttonLabel(1)).toBe('Helpers, 1 needs you');
    expect(buttonLabel(3)).toBe('Helpers, 3 need you');
  });
});

// ── The panel ────────────────────────────────────────────────────────────

function panel(first: HelperList, more: Partial<Parameters<typeof createHelpers>[0]> = {}) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const api: Api = async (method, path, body) => {
    calls.push({ method, path, body });
    if (method === 'GET' && path === '/api/helpers') return json(first);
    return json({ error: 'not_found' }, 404);
  };
  const sent: Record<string, unknown>[] = [];
  const opened: string[] = [];
  const steps: string[] = [];
  const counts: number[] = [];
  const helpers = createHelpers({
    api,
    send: (body) => {
      sent.push(body);
    },
    open: (id) => opened.push(id),
    openStep: (id) => steps.push(id),
    now: () => NOW,
    phone: () => false,
    capName: () => 'Anthropic',
    ...more,
  });
  helpers.onCount((n) => counts.push(n));
  document.body.append(helpers.element);
  return { helpers, calls, sent, opened, steps, counts, root: helpers.element };
}

const groups = (root: HTMLElement): string[] => [...root.querySelectorAll('.hp-group')].filter((g) => !(g as HTMLElement).hidden).map((g) => g.querySelector('h3')?.textContent ?? '');
const rowNames = (root: HTMLElement, group: string): string[] => {
  const g = [...root.querySelectorAll<HTMLElement>('.hp-group')].find((x) => x.querySelector('h3')?.textContent === group);
  return [...(g?.querySelectorAll('.hp-name') ?? [])].map((n) => n.textContent ?? '');
};
const row = (root: HTMLElement, id: string): HTMLElement => {
  const found = root.querySelector<HTMLElement>(`[data-id="${id}"]`);
  if (!found) throw new Error(`no row ${id}`);
  return found;
};

describe('the Helpers panel', () => {
  it('loads on open, heads with her and the summary, and groups in order, each only when not empty', async () => {
    const { helpers, calls, root } = panel(
      list([
        helper({ id: 'ask:bbbbbbbb', kind: 'ask', name: 'Remind me at 4', state: 'needs_you', waiting: [approval()] }),
        helper({ id: 'routine:cccccccc', name: 'Weekly review', state: 'working', doing: 'Writing the review', since_ms: NOW - 12_000 }),
        helper({ id: 'routine:aaaaaaaa' }),
        helper({ id: 'watcher:payments', kind: 'watcher', name: 'Payments', state: 'paused' }),
      ]),
    );
    await helpers.open();
    expect(calls[0]).toEqual({ method: 'GET', path: '/api/helpers', body: undefined });
    expect(root.querySelector('.hp-her-name')?.textContent).toBe('Trillion');
    expect(root.querySelector('.hp-summary')?.textContent).toBe('1 working, 1 needs you');
    expect(groups(root)).toEqual(['Needs you', 'Working', 'Idle']);
    expect(rowNames(root, 'Idle')).toEqual(['Morning brief', 'Payments']);
    expect(row(root, 'routine:cccccccc').textContent).toContain('Writing the review · 0:12');
    expect(row(root, 'watcher:payments').textContent).toContain('paused');
    // Groups are lists under headings; only the summary is a live region.
    expect(root.querySelectorAll('.hp-group ul').length).toBe(3);
    expect([...root.querySelectorAll('[aria-live]')].map((n) => n.className)).toEqual(['hp-summary']);
  });

  it('shows only the groups that have a helper, and says when there is none', async () => {
    const { helpers, root } = panel(list([helper()]));
    await helpers.open();
    expect(groups(root)).toEqual(['Idle']);
    helpers.update(list([]));
    expect(groups(root)).toEqual([]);
    expect(root.textContent).toContain('No helpers yet.');
    expect(root.querySelector('.hp-summary')?.textContent).toBe('All quiet');
  });

  it('says when the list cannot be read', async () => {
    const { helpers, root } = panel(list([]), {
      api: async () => json({ error: 'nope' }, 500),
    });
    await helpers.open();
    expect(root.textContent).toContain("Couldn't read her helpers. Try again in a moment.");
  });

  it('tells the button how many need you, on every update', async () => {
    const { helpers, counts } = panel(list([]));
    helpers.update(list([helper({ state: 'needs_you', waiting: [approval()] })]));
    helpers.update(list([]));
    expect(counts).toEqual([1, 0]);
    expect(helpers.needsYou).toBe(0);
  });

  it('keeps each row, and the focus in it, across a push', async () => {
    const { helpers, root } = panel(list([helper(), helper({ id: 'routine:dddddddd', name: 'Evening wrap', next_ms: at(18, 0) })]));
    await helpers.open();
    const kept = row(root, 'routine:dddddddd');
    const button = kept.querySelector('button') as HTMLButtonElement;
    button.focus();
    helpers.update(list([helper({ next_ms: at(10, 0) }), helper({ id: 'routine:dddddddd', name: 'Evening wrap', next_ms: at(18, 30) })]));
    expect(row(root, 'routine:dddddddd')).toBe(kept);
    expect(document.activeElement).toBe(button);
    expect(kept.textContent).toContain('next 18:30');
    expect(row(root, 'routine:aaaaaaaa').textContent).toContain('next 10:00');
  });

  it('moves a row to its new group, and drops one that left the list', async () => {
    const { helpers, root } = panel(list([helper(), helper({ id: 'routine:dddddddd', name: 'Evening wrap' })]));
    await helpers.open();
    helpers.update(list([helper({ state: 'working', doing: 'Reading', since_ms: NOW })]));
    expect(rowNames(root, 'Working')).toEqual(['Morning brief']);
    expect(root.querySelector('[data-id="routine:dddddddd"]')).toBeNull();
  });

  it('shows an approval card inline, and a tap goes the usual way', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
    const { helpers, root, sent } = panel(list([]));
    helpers.update(list([helper({ state: 'needs_you', waiting: [approval('p9', 'Send a message')] })]));
    const r = row(root, 'routine:aaaaaaaa');
    expect(r.textContent).toContain('Approve a change');
    expect(r.querySelector('.ap-title')?.textContent).toBe('Send a message');
    vi.advanceTimersByTime(ARRIVE_MS);
    (r.querySelector('.ap-approve') as HTMLButtonElement).click();
    expect(sent).toEqual([{ type: 'confirm', pending_id: 'p9' }]);
    // The card says nothing of its own: the summary is the panel's only live region.
    expect(r.querySelector('[aria-live]')).toBeNull();
    // Answered, the core's next push takes it away.
    helpers.update(list([helper()]));
    expect(r.querySelector('.ap-card')).toBeNull();
  });

  it('offers a step with a button to the Settings that does it', async () => {
    const { helpers, root, steps } = panel(list([]));
    helpers.update(
      list([
        helper({
          id: 'watcher:email',
          kind: 'watcher',
          name: 'Urgent email',
          state: 'needs_you',
          waiting: [{ type: 'step', id: 'connect:email', text: 'Connect Google to use Urgent email', at_ms: null }],
        }),
      ]),
    );
    const open = [...row(root, 'watcher:email').querySelectorAll('button')].find((b) => b.textContent === 'Open Settings');
    open?.click();
    expect(steps).toEqual(['connect:email']);
  });

  it('keeps the working time ticking while open', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    let now = NOW;
    const { helpers, root } = panel(list([helper({ state: 'working', doing: 'Reading', since_ms: NOW })]), { now: () => now });
    await helpers.open();
    now += 5_000;
    vi.advanceTimersByTime(1_000);
    expect(row(root, 'routine:aaaaaaaa').textContent).toContain('Reading · 0:05');
    helpers.closed();
    now += 5_000;
    vi.advanceTimersByTime(1_000);
    expect(row(root, 'routine:aaaaaaaa').textContent).toContain('Reading · 0:05');
  });

  it('takes focus on its first helper when opened, or on the list when there is none', async () => {
    const one = panel(list([helper()]));
    await one.helpers.open();
    one.helpers.focus();
    expect(document.activeElement).toBe(row(one.root, 'routine:aaaaaaaa').querySelector('.hp-main'));
    document.body.replaceChildren();
    const none = panel(list([]));
    await none.helpers.open();
    none.helpers.focus();
    expect(document.activeElement).toBe(none.root.querySelector('.hp-list'));
  });

  it('marks itself for a phone', async () => {
    const { helpers, root } = panel(list([]), { phone: () => true });
    await helpers.open();
    expect(root.classList.contains('hp-phone')).toBe(true);
  });
});
