// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  type Helper,
  type HelperList,
  askTarget,
  capNameOf,
  createHelpers,
  elapsedText,
  groupOf,
  outcomeText,
  stepTarget,
  statusLine,
  summaryLine,
  timeText,
} from '../src/helpers';
import { COLORS, createPicker, lookOf, picture, pictureLabel } from '../src/look';
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
  paused: false,
  switched_on: null,
  connected: null,
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
    expect(statusLine(helper({ state: 'paused', paused: true }), NOW)).toBe('paused');
    const w = { kind: 'watcher' as const, state: 'paused' as const, next_ms: null };
    expect(statusLine(helper({ ...w, id: 'watcher:payments', switched_on: false, connected: true }), NOW)).toBe('off');
    expect(statusLine(helper({ ...w, id: 'watcher:meetings', switched_on: true, connected: false }), NOW)).toBe("off: Google isn't connected");
    expect(statusLine(helper({ ...w, id: 'watcher:payments', switched_on: true, connected: false }), NOW)).toBe("off: Stripe isn't connected");
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
  const said: string[] = [];
  const helpers = createHelpers({
    api,
    announce: (text) => said.push(text),
    open: (id) => opened.push(id),
    openStep: (id) => steps.push(id),
    now: () => NOW,
    phone: () => false,
    capName: () => 'Anthropic',
    ...more,
  });
  helpers.onCount((n) => counts.push(n));
  document.body.append(helpers.element);
  return { helpers, calls, sent, opened, steps, counts, said, root: helpers.element };
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
        helper({ id: 'watcher:payments', kind: 'watcher', name: 'Payments', state: 'paused', switched_on: false, connected: true }),
      ]),
    );
    await helpers.open();
    expect(calls[0]).toEqual({ method: 'GET', path: '/api/helpers', body: undefined });
    expect(root.querySelector('.hp-her-name')?.textContent).toBe('Trillion');
    expect(root.querySelector('.hp-summary')?.textContent).toBe('1 working, 1 needs you');
    expect(groups(root)).toEqual(['Needs you', 'Working', 'Idle']);
    expect(rowNames(root, 'Idle')).toEqual(['Morning brief', 'Payments']);
    expect(row(root, 'routine:cccccccc').textContent).toContain('Writing the review · 0:12');
    expect(row(root, 'watcher:payments').textContent).toContain('off');
    // Groups are lists under headings; nothing in the panel is a live region (the page's one speaks).
    expect(root.querySelectorAll('.hp-group ul').length).toBe(3);
    expect(root.querySelectorAll('[aria-live]').length).toBe(0);
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

  it('sends an approval to the conversation it was raised in, where the card waits (SPN-14)', async () => {
    const { helpers, root, sent, opened } = panel(list([]));
    helpers.update(list([helper({ state: 'needs_you', conversation_id: 'c-5', waiting: [approval('p9', 'Send a message')] })]));
    const r = row(root, 'routine:aaaaaaaa');
    expect(r.textContent).toContain('Approve a change');
    // No card and no Approve here: a tap must come from its own conversation.
    expect(r.querySelector('.ap-card, .ap-approve')).toBeNull();
    const open = [...r.querySelectorAll('button')].find((b) => b.textContent === 'Open to approve');
    open?.click();
    await flush();
    expect(opened).toEqual(['c-5']);
    expect(sent).toEqual([]);
    expect(r.querySelector('[aria-live]')).toBeNull();
  });

  it('asks the core for the conversation when the helper has none yet, and offers Open to reply for a question', async () => {
    const calls: string[] = [];
    const { helpers, root, opened } = panel(list([]), {
      api: async (method, path) => {
        calls.push(`${method} ${path}`);
        return method === 'POST' ? json({ conversation_id: 'c-7' }) : json(list([]));
      },
    });
    helpers.update(list([helper({ state: 'needs_you', waiting: [{ type: 'question', id: 'q1', text: 'Which one?', at_ms: null }] })]));
    const r = row(root, 'routine:aaaaaaaa');
    expect(r.textContent).toContain('A question for you');
    [...r.querySelectorAll('button')].find((b) => b.textContent === 'Open to reply')?.click();
    await flush();
    expect(calls).toContain('POST /api/helpers/routine%3Aaaaaaaaa/conversation');
    expect(opened).toEqual(['c-7']);
    // Answered there, the next push takes the button away.
    helpers.update(list([helper()]));
    expect(r.querySelector('.hp-open')).toBeNull();
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

// ── Details, history and actions ─────────────────────────────────────────

type Answer = (body: unknown) => Response;

function details(first: HelperList, answers: Record<string, Answer> = {}) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const api: Api = async (method, path, body) => {
    calls.push({ method, path, body });
    const answer = answers[`${method} ${path}`];
    if (answer) return answer(body);
    if (method === 'GET' && path === '/api/helpers') return json(first);
    if (method === 'GET' && path.endsWith('/runs')) return json({ runs: [] });
    return json({ error: 'not_found' }, 404);
  };
  const opened: string[] = [];
  const steps: string[] = [];
  const said: string[] = [];
  const helpers = createHelpers({
    api,
    announce: (text) => said.push(text),
    open: (id) => opened.push(id),
    openStep: (id) => steps.push(id),
    now: () => NOW,
    phone: () => false,
    capName: () => null,
  });
  document.body.append(helpers.element);
  const show = async (id: string): Promise<HTMLElement> => {
    await helpers.open();
    (row(helpers.element, id).querySelector('.hp-main') as HTMLButtonElement).click();
    await flush();
    return helpers.element.querySelector('.hp-details') as HTMLElement;
  };
  return { helpers, calls, opened, steps, said, show, root: helpers.element };
}

const press = (root: ParentNode, text: string): void => {
  const b = [...root.querySelectorAll('button')].find((x) => x.textContent === text && !x.closest('[hidden]'));
  if (!b) throw new Error(`no button "${text}"`);
  b.click();
};
const hasButton = (root: ParentNode, text: string): boolean => [...root.querySelectorAll('button')].some((x) => x.textContent === text && !x.closest('[hidden]'));

describe('outcome words', () => {
  it('says how each run ended', () => {
    expect(outcomeText({ at_ms: 0, outcome: 'succeeded', reason: null })).toBe('Done');
    expect(outcomeText({ at_ms: 0, outcome: 'nothing', reason: null })).toBe('Nothing for you');
    expect(outcomeText({ at_ms: 0, outcome: 'skipped', reason: 'paused' })).toBe('Skipped: paused');
    expect(outcomeText({ at_ms: 0, outcome: 'skipped', reason: null })).toBe('Skipped');
    expect(outcomeText({ at_ms: 0, outcome: 'failed', reason: 'no model set up' })).toBe('Failed: no model set up');
    expect(outcomeText({ at_ms: 0, outcome: 'failed', reason: null })).toBe('Failed');
  });
});

describe('a helper’s details', () => {
  it('shows its name, job, next run and its last runs, each with Open when it has a conversation', async () => {
    const { show, calls, opened } = details(list([helper()]), {
      'GET /api/helpers/routine%3Aaaaaaaaa/runs': () =>
        json({
          runs: [
            { at_ms: at(7, 0), outcome: 'failed', reason: "couldn't reach the inbox", conversation_id: 'c-1' },
            { at_ms: at(7, 0, 4), outcome: 'nothing', reason: null, conversation_id: null },
            { at_ms: at(7, 0, 3), outcome: 'succeeded', reason: null, conversation_id: 'c-3' },
          ],
        }),
    });
    const d = await show('routine:aaaaaaaa');
    expect(calls.map((c) => c.path)).toContain('/api/helpers/routine%3Aaaaaaaaa/runs');
    expect(d.querySelector('h3')?.textContent).toBe('Morning brief');
    expect(d.textContent).toContain('Reads your inbox and calendar');
    expect(d.textContent).toContain('Next run: 9:55');
    const runs = [...d.querySelectorAll('.hp-runs li')].map((li) => li.textContent);
    expect(runs[0]).toContain("7:00Failed: couldn't reach the inbox");
    expect(runs[1]).toContain('Nothing for you');
    expect(runs[2]).toContain('Done');
    expect(d.querySelectorAll('.hp-runs li')[1]?.querySelector('button')).toBeNull();
    press(d.querySelectorAll('.hp-runs li')[0] as HTMLElement, 'Open');
    expect(opened).toEqual(['c-1']);
  });

  it('says when there are no runs yet, or they cannot be read', async () => {
    const one = details(list([helper()]));
    expect((await one.show('routine:aaaaaaaa')).textContent).toContain('No runs yet.');
    document.body.replaceChildren();
    const two = details(list([helper()]), { 'GET /api/helpers/routine%3Aaaaaaaaa/runs': () => json({}, 500) });
    expect((await two.show('routine:aaaaaaaa')).textContent).toContain("Couldn't read its runs.");
  });

  it('pauses and resumes a routine by the routines routes, and follows the push', async () => {
    const { show, calls, helpers } = details(list([helper()]), {
      'POST /api/routines/aaaaaaaa/pause': () => json({ routines: [] }),
      'POST /api/routines/aaaaaaaa/resume': () => json({ routines: [] }),
    });
    const d = await show('routine:aaaaaaaa');
    press(d, 'Pause');
    await flush();
    expect(calls).toContainEqual({ method: 'POST', path: '/api/routines/aaaaaaaa/pause', body: {} });
    helpers.update(list([helper({ state: 'paused', paused: true, next_ms: null })]));
    expect(d.textContent).toContain('paused');
    press(d, 'Resume');
    await flush();
    expect(calls).toContainEqual({ method: 'POST', path: '/api/routines/aaaaaaaa/resume', body: {} });
  });

  it('turns a watcher off and on through its settings', async () => {
    const watcher = helper({ id: 'watcher:payments', kind: 'watcher', name: 'Payments', job: 'Watches your payments', switched_on: true, connected: true });
    const { show, calls, helpers } = details(list([watcher]), { 'PUT /api/attention/settings': () => json({}) });
    const d = await show('watcher:payments');
    expect(d.textContent).toContain('Next check: 9:55');
    press(d, 'Turn off');
    await flush();
    expect(calls).toContainEqual({ method: 'PUT', path: '/api/attention/settings', body: { watchers: { payments: false } } });
    helpers.update(list([{ ...watcher, state: 'paused', switched_on: false }]));
    press(d, 'Turn on');
    await flush();
    expect(calls).toContainEqual({ method: 'PUT', path: '/api/attention/settings', body: { watchers: { payments: true } } });
  });

  it('cancels a timed ask only after a second, plain button', async () => {
    const ask = helper({ id: 'ask:bbbbbbbb', kind: 'ask', name: 'Remind me at 4', job: 'Today at 16:00' });
    const { show, calls } = details(list([ask]), { 'DELETE /api/schedules/bbbbbbbb': () => json({}) });
    const d = await show('ask:bbbbbbbb');
    expect(hasButton(d, 'Pause')).toBe(false);
    press(d, 'Cancel');
    expect(d.textContent).toContain("Cancel this timed ask? It won't happen.");
    press(d, 'Keep it');
    expect(hasButton(d, 'Cancel it')).toBe(false);
    expect(calls.filter((c) => c.method === 'DELETE')).toEqual([]);
    press(d, 'Cancel');
    press(d, 'Cancel it');
    await flush();
    expect(calls).toContainEqual({ method: 'DELETE', path: '/api/schedules/bbbbbbbb', body: {} });
  });

  it('says so when an action does not go through', async () => {
    const { show } = details(list([helper()]), { 'POST /api/routines/aaaaaaaa/pause': () => json({ error: 'not_found' }, 404) });
    const d = await show('routine:aaaaaaaa');
    press(d, 'Pause');
    await flush();
    expect(d.textContent).toContain("That didn't work. It may have just finished or been removed.");
  });

  it('opens its conversation with Talk to it, the way History does', async () => {
    const { show, calls, opened } = details(list([helper()]), {
      'POST /api/helpers/routine%3Aaaaaaaaa/conversation': () => json({ conversation_id: 'c-9' }),
    });
    const d = await show('routine:aaaaaaaa');
    press(d, 'Talk to it');
    await flush();
    expect(calls).toContainEqual({ method: 'POST', path: '/api/helpers/routine%3Aaaaaaaaa/conversation', body: {} });
    expect(opened).toEqual(['c-9']);
  });

  it('says when there is no conversation to open yet', async () => {
    const { show, opened } = details(list([helper()]), {
      'POST /api/helpers/routine%3Aaaaaaaaa/conversation': () => json({ error: 'no_conversation' }, 404),
    });
    const d = await show('routine:aaaaaaaa');
    press(d, 'Talk to it');
    await flush();
    expect(opened).toEqual([]);
    expect(d.textContent).toContain('There is no conversation for it yet.');
  });

  it('goes back to the list with Back or Escape, focus on its row, and Escape goes no further', async () => {
    const { show, root } = details(list([helper(), helper({ id: 'routine:dddddddd', name: 'Evening wrap' })]));
    let d = await show('routine:dddddddd');
    expect(d.hidden).toBe(false);
    expect((root.querySelector('.hp-list') as HTMLElement).hidden).toBe(true);
    expect(root.contains(document.activeElement)).toBe(true);
    press(d, 'Back to helpers');
    expect(d.hidden).toBe(true);
    expect(document.activeElement).toBe(row(root, 'routine:dddddddd').querySelector('.hp-main'));

    (row(root, 'routine:dddddddd').querySelector('.hp-main') as HTMLButtonElement).click();
    await flush();
    d = root.querySelector('.hp-details') as HTMLElement;
    const page = vi.fn();
    window.addEventListener('keydown', page);
    (document.activeElement as HTMLElement).dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    window.removeEventListener('keydown', page);
    expect(page).not.toHaveBeenCalled();
    expect(d.hidden).toBe(true);
    expect(document.activeElement).toBe(row(root, 'routine:dddddddd').querySelector('.hp-main'));
  });

  it('goes back to the list when its helper leaves it', async () => {
    const { show, helpers, root } = details(list([helper()]));
    const d = await show('routine:aaaaaaaa');
    helpers.update(list([]));
    expect(d.hidden).toBe(true);
    expect((root.querySelector('.hp-list') as HTMLElement).hidden).toBe(false);
  });

  it('opens on the list again each time the panel opens', async () => {
    const { show, helpers, root } = details(list([helper()]));
    await show('routine:aaaaaaaa');
    helpers.closed();
    await helpers.open();
    expect((root.querySelector('.hp-details') as HTMLElement).hidden).toBe(true);
  });
});

describe('a step’s Settings', () => {
  it('goes to the connection the watcher reads, as Home’s Connect does', () => {
    expect(stepTarget('connect:email')).toBe('gmail');
    expect(stepTarget('connect:meetings')).toBe('google-calendar');
    expect(stepTarget('connect:payments')).toBe('stripe');
    expect(stepTarget('connect:other')).toBeNull();
    expect(stepTarget('something')).toBeNull();
  });
});

describe('opening a helper from the chat', () => {
  it('opens the panel at that helper’s details, or on the list when it is gone', async () => {
    const { helpers, root } = details(list([helper()]));
    await helpers.showHelper('routine:aaaaaaaa');
    await flush();
    const d = root.querySelector('.hp-details') as HTMLElement;
    expect(d.hidden).toBe(false);
    expect(d.querySelector('h3')?.textContent).toBe('Morning brief');
    await helpers.showHelper('ask:ffffffff');
    expect(d.hidden).toBe(true);
    expect((root.querySelector('.hp-list') as HTMLElement).hidden).toBe(false);
  });
});

describe('review fixes', () => {
  it('shows the next run once in the details', async () => {
    const { show } = details(list([helper()]));
    const d = await show('routine:aaaaaaaa');
    expect(d.textContent?.match(/9:55/g)).toHaveLength(1);
    expect(d.textContent).toContain('Next run: 9:55');
  });

  it('offers Connect, not Turn on, for a watcher switched on but not connected, to the step’s Settings', async () => {
    const meetings = helper({ id: 'watcher:meetings', kind: 'watcher', name: 'Meetings', state: 'paused', next_ms: null, switched_on: true, connected: false });
    const { show, steps, calls } = details(list([meetings]));
    const d = await show('watcher:meetings');
    expect(d.textContent).toContain("off: Google isn't connected");
    expect(hasButton(d, 'Turn on')).toBe(false);
    press(d, 'Connect');
    expect(steps).toEqual(['connect:meetings']);
    expect(calls.filter((c) => c.method === 'PUT')).toEqual([]);
  });

  it('decides Pause or Resume from the paused flag, not the state: capped is not paused', async () => {
    const { show } = details(list([helper({ state: 'capped', paused: false })]));
    const d = await show('routine:aaaaaaaa');
    expect(hasButton(d, 'Pause')).toBe(true);
  });

  it('keeps focus on a row that moves to another group or place', async () => {
    const { helpers, root } = panel(list([helper(), helper({ id: 'routine:dddddddd', name: 'Evening wrap' })]));
    await helpers.open();
    const button = row(root, 'routine:dddddddd').querySelector('.hp-main') as HTMLButtonElement;
    button.focus();
    helpers.update(list([helper({ id: 'routine:dddddddd', name: 'Evening wrap', state: 'working', doing: 'Working', since_ms: NOW }), helper()]));
    expect(rowNames(root, 'Working')).toEqual(['Evening wrap']);
    expect(document.activeElement).toBe(button);
    helpers.update(list([helper({ id: 'routine:dddddddd', name: 'Evening wrap', next_ms: at(8, 0) }), helper()]));
    expect(rowNames(root, 'Idle')).toEqual(['Evening wrap', 'Morning brief']);
    expect(document.activeElement).toBe(button);
  });

  it('puts focus on the list when the helper shown leaves it', async () => {
    const { show, helpers, root } = details(list([helper()]));
    await show('routine:aaaaaaaa');
    helpers.update(list([]));
    expect(document.activeElement).toBe(root.querySelector('.hp-list'));
  });

  it('says through the page when how many need you changes, and when something fails', async () => {
    const { helpers, said } = panel(list([]));
    helpers.update(list([]));
    helpers.update(list([helper({ state: 'needs_you', waiting: [approval()] })]));
    helpers.update(list([helper({ state: 'needs_you', waiting: [approval()] })]));
    helpers.update(list([]));
    expect(said).toEqual(['1 helper needs you', 'No helper needs you now']);
    const failing = panel(list([]), { api: async () => json({}, 500) });
    await failing.helpers.open();
    expect(failing.said).toEqual(["Couldn't read her helpers. Try again in a moment."]);
  });

  it('ignores a read that a push overtook', async () => {
    let answer: (r: Response) => void = () => undefined;
    const { helpers, root } = panel(list([]), {
      api: () => new Promise<Response>((r) => {
        answer = r;
      }),
    });
    const opening = helpers.open();
    helpers.update(list([helper({ name: 'Fresh' })]));
    answer(json(list([helper({ name: 'Stale' })])));
    await opening;
    expect(root.querySelector('.hp-name')?.textContent).toBe('Fresh');
  });

  it('follows the window between phone and desk while open', async () => {
    let phone = false;
    const { helpers, root } = panel(list([]), { phone: () => phone });
    await helpers.open();
    expect(root.classList.contains('hp-phone')).toBe(false);
    phone = true;
    window.dispatchEvent(new Event('resize'));
    expect(root.classList.contains('hp-phone')).toBe(true);
    helpers.closed();
    phone = false;
    window.dispatchEvent(new Event('resize'));
    expect(root.classList.contains('hp-phone')).toBe(true);
  });

  it('sets every name, job, reason and step only as text', async () => {
    const bad = '<img src=x onerror=alert(1)>';
    const { helpers, root } = panel(list([]));
    helpers.update(
      list([
        helper({ name: bad, job: bad, next_ms: null }),
        helper({ id: 'routine:eeeeeeee', name: 'x', last_run: { at_ms: at(7, 0), outcome: 'failed', reason: bad } }),
        helper({ id: 'watcher:email', kind: 'watcher', name: 'y', state: 'needs_you', waiting: [{ type: 'step', id: 'connect:email', text: bad, at_ms: null }] }),
      ]),
    );
    expect(root.querySelector('img')).toBeNull();
    expect(root.textContent).toContain(bad);
  });

  it('stops its clock once no helper is working', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    const { helpers } = panel(list([helper({ state: 'working', doing: 'Working', since_ms: NOW })]));
    await helpers.open();
    expect(vi.getTimerCount()).toBe(1);
    helpers.update(list([helper()]));
    expect(vi.getTimerCount()).toBe(0);
  });

  it('names the background model at its cap when the page knows it', () => {
    expect(capNameOf([
      { leg: 'voice', name: 'ElevenLabs', state: 'capped' },
      { leg: 'model', name: 'Anthropic', state: 'capped' },
      { leg: 'model', name: 'A local model', state: 'ok' },
    ])).toBe('Anthropic');
    // The first model listed is the usual one, which background work runs on.
    expect(capNameOf([{ leg: 'model', name: 'Anthropic', state: 'ok' }, { leg: 'model', name: 'OpenAI', state: 'capped' }])).toBeNull();
    expect(capNameOf(undefined)).toBeNull();
  });
});

// ── Your helpers: their pictures, details, and making one ────────────────

const OWN = `own:${'a'.repeat(32)}`;
const OWN_PATH = encodeURIComponent(OWN);
const own = (over: Partial<Helper> = {}): Helper =>
  helper({
    id: OWN,
    kind: 'own',
    name: 'Invoices',
    job: 'Keeps track of who owes you',
    next_ms: at(9, 0, 9),
    conversation_id: 'c-inv',
    notes: 'Watch the Stripe account.',
    routines: [{ id: 'r1', name: 'Check what is overdue', next_ms: at(9, 0, 9), paused: false }],
    icon: 'receipt',
    color: 'green',
    ...over,
  });

const key = (target: Element, k: string): void => {
  target.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }));
};

describe('a picture', () => {
  it('has the core’s colours, round the wheel, and no icons of its own', () => {
    expect(COLORS).toEqual(['red', 'orange', 'amber', 'lime', 'green', 'teal', 'blue', 'violet', 'fuchsia', 'rose', 'slate']);
    const core = readFileSync(resolve(__dirname, '../../../src/trillion/helpers/look.py'), 'utf8');
    const listed = /^COLORS = \(([^)]*)\)/m.exec(core)?.[1] ?? '';
    expect([...listed.matchAll(/"(\w+)"/g)].map((m) => m[1]).sort()).toEqual([...COLORS].sort());
  });

  it('is never blank: a colour off the list is slate, and an odd icon the core’s spark', () => {
    expect(lookOf('receipt', 'green')).toEqual({ icon: 'receipt', color: 'green' });
    expect(lookOf('envelope', 'fuchsia')).toEqual({ icon: 'envelope', color: 'fuchsia' });
    expect(lookOf(3, 7)).toEqual({ icon: 'spark', color: 'slate' });
  });

  it('is an orb in its colour, with no icon drawn, and a name a screen reader says', () => {
    const p = picture({ icon: 'receipt', color: 'green' }, 28, 'Invoices');
    expect(p.classList.contains('look')).toBe(true);
    expect(p.dataset['color']).toBe('green');
    expect(p.dataset['size']).toBe('28');
    expect(p.querySelector('svg')).toBeNull();
    expect(p.childElementCount).toBe(0);
    expect(p.dataset['icon']).toBeUndefined();
    expect(p.getAttribute('role')).toBe('img');
    expect(p.getAttribute('aria-label')).toBe('Invoices, green');
    expect(pictureLabel('Inbox Monitor', { icon: 'envelope', color: 'teal' })).toBe('Inbox Monitor, teal');
    expect(picture({ icon: 'spark', color: 'blue' }, 18).getAttribute('aria-hidden')).toBe('true');
  });

  it('is built as her orb is, with colour tokens for light and dark, and the picker fits a phone', () => {
    const css = readFileSync(resolve(__dirname, '../src/app.css'), 'utf8');
    for (const c of COLORS) {
      expect(css).toContain(`--helper-${c}:`);
      expect(css).toContain(`.look[data-color='${c}']`);
    }
    expect(css).toMatch(/:root\[data-theme='light'\][^{]*\{[^}]*--helper-green:/);
    expect(css).toMatch(/\.look,\s*\.look-color\s*\{[^}]*radial-gradient\(\s*circle at 35% 30%,\s*var\(--color-ink-on-dark\) 0,\s*var\(--look/);
    expect(css).toMatch(/\.look-picker\s*\{[^}]*max-width:\s*calc\(100vw - 32px\)/);
    expect(css).not.toContain('.look-icon');
  });
});

describe('the picker', () => {
  function open() {
    const picks: unknown[] = [];
    const close = vi.fn();
    const p = createPicker({ name: 'Invoices', look: { icon: 'receipt', color: 'green' }, onPick: (l) => picks.push(l), onClose: close });
    document.body.append(p.element);
    return { p, picks, close, root: p.element };
  }
  const colorBtn = (root: HTMLElement, id: string): HTMLButtonElement => root.querySelector(`.look-color[data-color="${id}"]`) as HTMLButtonElement;

  it('shows only the colours, each an orb, the chosen one marked and the one tab stop', () => {
    const { root } = open();
    expect(root.getAttribute('aria-label')).toBe('Colour for Invoices');
    expect(root.querySelector('.look-icon, [data-icon], svg')).toBeNull();
    const colors = [...root.querySelectorAll<HTMLButtonElement>('.look-color')];
    expect(colors.map((b) => b.getAttribute('aria-label'))).toEqual([...COLORS]);
    expect(colorBtn(root, 'green').getAttribute('aria-pressed')).toBe('true');
    expect(colors.filter((b) => b.getAttribute('aria-pressed') === 'true')).toHaveLength(1);
    expect(colors.filter((b) => b.tabIndex === 0)).toEqual([colorBtn(root, 'green')]);
  });

  it('saves a tap at once with the icon it had, and shows it chosen; the chosen one again does nothing', () => {
    const { root, picks } = open();
    colorBtn(root, 'blue').click();
    colorBtn(root, 'blue').click();
    colorBtn(root, 'rose').click();
    expect(picks).toEqual([
      { icon: 'receipt', color: 'blue' },
      { icon: 'receipt', color: 'rose' },
    ]);
    expect(colorBtn(root, 'rose').getAttribute('aria-pressed')).toBe('true');
    expect(colorBtn(root, 'green').getAttribute('aria-pressed')).toBe('false');
  });

  it('moves by arrow keys across and down the colours, and closes on Escape', () => {
    const { p, root, close } = open();
    p.focus();
    expect(document.activeElement).toBe(colorBtn(root, 'green'));
    key(document.activeElement as Element, 'ArrowRight');
    expect(document.activeElement).toBe(colorBtn(root, 'teal'));
    key(document.activeElement as Element, 'ArrowLeft');
    key(document.activeElement as Element, 'ArrowLeft');
    expect(document.activeElement).toBe(colorBtn(root, 'lime'));
    key(document.activeElement as Element, 'ArrowDown');
    expect(document.activeElement).toBe(colorBtn(root, 'rose'));
    key(document.activeElement as Element, 'ArrowUp');
    expect(document.activeElement).toBe(colorBtn(root, 'lime'));
    key(document.activeElement as Element, 'End');
    expect(document.activeElement).toBe(colorBtn(root, 'slate'));
    key(document.activeElement as Element, 'Home');
    expect(document.activeElement).toBe(colorBtn(root, 'red'));
    expect([...root.querySelectorAll<HTMLButtonElement>('.look-color')].filter((b) => b.tabIndex === 0)).toEqual([colorBtn(root, 'red')]);
    key(document.activeElement as Element, 'Escape');
    expect(close).toHaveBeenCalledOnce();
  });
});

describe('your helpers in the panel', () => {
  it('shows a row with its picture, the state dot on its edge, and its job when idle', async () => {
    const { helpers, root } = panel(list([own()]));
    await helpers.open();
    const r = row(root, OWN);
    const pic = r.querySelector('.look') as HTMLElement;
    expect(pic.getAttribute('aria-label')).toBe('Invoices, green');
    expect(pic.dataset['size']).toBe('28');
    expect(r.querySelector('.hp-pic .hp-dot')).not.toBeNull();
    expect(r.querySelector('.hp-status')?.textContent).toBe('Keeps track of who owes you');
    helpers.update(list([own({ state: 'working', doing: 'Reading invoices', since_ms: NOW - 5_000 })]));
    expect(r.querySelector('.hp-status')?.textContent).toBe('Reading invoices · 0:05');
    helpers.update(list([own({ color: 'blue' })]));
    expect(r.querySelector('.look')?.getAttribute('aria-label')).toBe('Invoices, blue');
  });

  it('finds the helper whose chat a conversation is', async () => {
    const { helpers } = panel(list([own(), helper()]));
    await helpers.open();
    expect(helpers.ownFor('c-inv')?.name).toBe('Invoices');
    expect(helpers.ownFor('c-other')).toBeNull();
    expect(helpers.ownFor(null)).toBeNull();
  });

  it('shows its details: the picture, job, what it knows, its routines with next runs, and its runs', async () => {
    const { show, calls } = details(list([own()]), {
      [`GET /api/helpers/${OWN_PATH}/runs`]: () => json({ runs: [{ at_ms: at(8, 0), outcome: 'succeeded', reason: null, conversation_id: 'c-inv' }] }),
    });
    const d = await show(OWN);
    expect(calls.some((c) => c.path === `/api/helpers/${OWN_PATH}/runs`)).toBe(true);
    const pic = d.querySelector('.hp-d-pic') as HTMLButtonElement;
    expect((d.querySelector('.hp-d-picwrap') as HTMLElement).hidden).toBe(false);
    expect(pic.getAttribute('aria-label')).toBe('Invoices, green');
    expect(pic.dataset['size']).toBe('44');
    expect(d.textContent).toContain('Keeps track of who owes you');
    expect(d.textContent).toContain('What it knows');
    expect(d.textContent).toContain('Watch the Stripe account.');
    const routines = [...d.querySelectorAll('.hp-routine')].map((r) => r.textContent);
    expect(routines).toHaveLength(1);
    expect(routines[0]).toContain('Check what is overdue');
    expect(routines[0]).toMatch(/next [A-Z][a-z]{2} 9:00/);
    expect(d.textContent).toContain('Done');
    expect(hasButton(d, 'Talk to it')).toBe(true);
    expect(hasButton(d, 'Pause')).toBe(true);
    expect(hasButton(d, 'Delete')).toBe(true);
  });

  it('says when it knows nothing yet and has no routines, and shows none of this for her own helpers', async () => {
    const { show, helpers } = details(list([own({ notes: '', routines: [] }), helper()]));
    let d = await show(OWN);
    expect(d.textContent).toContain('Nothing yet. Tell it in its chat.');
    expect(d.textContent).toContain('No routines yet.');
    await helpers.showHelper('routine:aaaaaaaa');
    await flush();
    d = helpers.element.querySelector('.hp-details') as HTMLElement;
    expect((d.querySelector('.hp-d-picwrap') as HTMLElement).hidden).toBe(true);
    expect((d.querySelector('.hp-d-own') as HTMLElement).hidden).toBe(true);
    expect(hasButton(d, 'Delete')).toBe(false);
  });

  it('opens its own chat with Talk to it, without asking the core', async () => {
    const { show, calls, opened } = details(list([own()]));
    const d = await show(OWN);
    press(d, 'Talk to it');
    await flush();
    expect(opened).toEqual(['c-inv']);
    expect(calls.some((c) => c.path.endsWith('/conversation'))).toBe(false);
  });

  it('pauses and resumes by its own routes, and draws the list they answer with', async () => {
    const paused = own({ state: 'paused', paused: true, next_ms: null });
    const { show, calls } = details(list([own()]), {
      [`POST /api/helpers/${OWN_PATH}/pause`]: () => json(list([paused])),
      [`POST /api/helpers/${OWN_PATH}/resume`]: () => json(list([own()])),
    });
    const d = await show(OWN);
    press(d, 'Pause');
    await flush();
    expect(calls).toContainEqual({ method: 'POST', path: `/api/helpers/${OWN_PATH}/pause`, body: {} });
    expect(hasButton(d, 'Resume')).toBe(true);
    press(d, 'Resume');
    await flush();
    expect(calls).toContainEqual({ method: 'POST', path: `/api/helpers/${OWN_PATH}/resume`, body: {} });
    expect(hasButton(d, 'Pause')).toBe(true);
  });

  it('deletes only after a second, plain button, in the spec’s words', async () => {
    const { show, calls, root } = details(list([own()]), { [`DELETE /api/helpers/${OWN_PATH}`]: () => json(list([])) });
    const d = await show(OWN);
    press(d, 'Delete');
    expect(d.textContent).toContain('Delete Invoices? Its routines stop. Its chat stays in History.');
    expect(document.activeElement?.textContent).toBe('Keep it');
    press(d, 'Keep it');
    expect(hasButton(d, 'Delete it')).toBe(false);
    expect(document.activeElement?.textContent).toBe('Delete');
    expect(calls.filter((c) => c.method === 'DELETE')).toEqual([]);
    press(d, 'Delete');
    press(d, 'Delete it');
    await flush();
    expect(calls).toContainEqual({ method: 'DELETE', path: `/api/helpers/${OWN_PATH}`, body: {} });
    expect(d.hidden).toBe(true);
    expect((root.querySelector('.hp-list') as HTMLElement).hidden).toBe(false);
  });

  it('changes its picture from the details: one tap saves it, Escape closes and focus goes back', async () => {
    const { show, calls, helpers } = details(list([own()]), {
      [`PUT /api/helpers/${OWN_PATH}/look`]: () => json(list([own({ color: 'violet' })])),
    });
    const d = await show(OWN);
    const pic = d.querySelector('.hp-d-pic') as HTMLButtonElement;
    expect(pic.getAttribute('aria-expanded')).toBe('false');
    pic.click();
    const picker = d.querySelector('.look-picker') as HTMLElement;
    expect(picker).not.toBeNull();
    expect(pic.getAttribute('aria-expanded')).toBe('true');
    expect(document.activeElement).toBe(picker.querySelector('.look-color[data-color="green"]'));
    (picker.querySelector('.look-color[data-color="violet"]') as HTMLButtonElement).click();
    // At once, before the core answers.
    expect(pic.getAttribute('aria-label')).toBe('Invoices, violet');
    await flush();
    // The icon it had goes back unchanged: only the colour is picked.
    expect(calls).toContainEqual({ method: 'PUT', path: `/api/helpers/${OWN_PATH}/look`, body: { icon: 'receipt', color: 'violet' } });
    expect(row(helpers.element, OWN).querySelector('.look')?.getAttribute('aria-label')).toBe('Invoices, violet');
    key(document.activeElement as Element, 'Escape');
    expect(d.querySelector('.look-picker')).toBeNull();
    expect(document.activeElement).toBe(pic);
    // Escape in the picker closed it, and went no further: the details stay.
    expect(d.hidden).toBe(false);
  });

  it('closes the picker, and its listener for taps outside, when the panel hides', async () => {
    const { show, helpers } = details(list([own()]));
    const d = await show(OWN);
    const off = vi.spyOn(document, 'removeEventListener');
    (d.querySelector('.hp-d-pic') as HTMLButtonElement).click();
    expect(d.querySelector('.look-picker')).not.toBeNull();
    helpers.closed();
    expect(d.querySelector('.look-picker')).toBeNull();
    expect(off).toHaveBeenCalledWith('pointerdown', expect.any(Function), true);
    expect((d.querySelector('.hp-d-pic') as HTMLElement).getAttribute('aria-expanded')).toBe('false');
    off.mockRestore();
  });

  it('says so, and shows the saved picture again, when a new one does not save', async () => {
    const { show } = details(list([own()]), { [`PUT /api/helpers/${OWN_PATH}/look`]: () => json({ error: 'bad_request' }, 400) });
    const d = await show(OWN);
    const pic = d.querySelector('.hp-d-pic') as HTMLButtonElement;
    pic.click();
    (d.querySelector('.look-color[data-color="rose"]') as HTMLButtonElement).click();
    await flush();
    expect(d.textContent).toContain("That didn't work. It may have just finished or been removed.");
    expect(pic.getAttribute('aria-label')).toBe('Invoices, green');
  });
});

describe('making a helper from the panel', () => {
  it('opens one field at the top, sends the sentence with Ask Trillion, and keeps nothing empty', async () => {
    const asked: string[] = [];
    const { helpers, root } = panel(list([own()]), { newHelper: (text) => asked.push(text) });
    await helpers.open();
    const add = [...root.querySelectorAll('button')].find((b) => b.textContent === 'New helper') as HTMLButtonElement;
    expect(add).toBeTruthy();
    // At the top, under her.
    expect(add.compareDocumentPosition(root.querySelector('.hp-group') as Node) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(add.getAttribute('aria-expanded')).toBe('false');
    add.click();
    expect(add.getAttribute('aria-expanded')).toBe('true');
    const field = root.querySelector('.hp-new input') as HTMLInputElement;
    expect(document.activeElement).toBe(field);
    expect(root.querySelector(`label[for="${field.id}"]`)?.textContent).toBe('What should it do?');
    press(root, 'Ask Trillion');
    expect(asked).toEqual([]);
    field.value = '  Chase my unpaid invoices  ';
    press(root, 'Ask Trillion');
    expect(asked).toEqual(['Chase my unpaid invoices']);
    expect(field.value).toBe('');
  });

  it('closes the field on Escape, with focus back on the button, and the panel stays open', async () => {
    const { helpers, root } = panel(list([]), { newHelper: () => undefined });
    await helpers.open();
    press(root, 'New helper');
    const field = root.querySelector('.hp-new input') as HTMLInputElement;
    const page = vi.fn();
    window.addEventListener('keydown', page);
    key(field, 'Escape');
    window.removeEventListener('keydown', page);
    expect(page).not.toHaveBeenCalled();
    expect((root.querySelector('.hp-new') as HTMLElement).hidden).toBe(true);
    expect(document.activeElement?.textContent).toBe('New helper');
  });

  it('asks in her last chat, never a helper’s: a new conversation when there is none', () => {
    expect(askTarget(['c-1', 'c-2', 'c-inv'], new Set(['c-inv']))).toBe('c-2');
    expect(askTarget(['c-inv'], new Set(['c-inv']))).toBeNull();
    expect(askTarget([], new Set())).toBeNull();
  });
});

describe('the panel at every width', () => {
  it('has the workspace shown at every width: the sheet below the phone breakpoint, beside the sidebar above, with no gap', async () => {
    const { PHONE_MAX } = await import('../src/layout');
    const css = readFileSync(resolve(__dirname, '../src/app.css'), 'utf8');
    // The only width breakpoints the workspace and its panel follow are the phone's, on either side of it.
    const widths = [...css.matchAll(/@media \((max|min)-width: (\d+)px\)/g)].map((m) => [m[1], Number(m[2])] as const);
    expect(widths).toContainEqual(['max', PHONE_MAX - 1]);
    expect(widths).toContainEqual(['min', PHONE_MAX]);
    // Nothing hides it but its own [hidden], and a phone's Folders panel over its body.
    const hides = [...css.matchAll(/([^{}]*)\{[^}]*display:\s*none/g)].map((m) => m[1]!.trim()).filter((sel) => /\.workspace(?![-\w])|\.hp(?![-\w])/.test(sel));
    for (const sel of hides) expect(sel).toMatch(/\[hidden\]|has-folders|settings-close|settings-actions/);
  });
});
