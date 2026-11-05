// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  type Helper,
  type HelperList,
  buttonLabel,
  createHelpers,
  elapsedText,
  groupOf,
  outcomeText,
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
  const helpers = createHelpers({
    api,
    open: (id) => opened.push(id),
    openStep: () => undefined,
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
  return { helpers, calls, opened, show, root: helpers.element };
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
    helpers.update(list([helper({ state: 'paused' })]));
    expect(d.textContent).toContain('paused');
    press(d, 'Resume');
    await flush();
    expect(calls).toContainEqual({ method: 'POST', path: '/api/routines/aaaaaaaa/resume', body: {} });
  });

  it('turns a watcher off and on through its settings', async () => {
    const watcher = helper({ id: 'watcher:payments', kind: 'watcher', name: 'Payments', job: 'Watches your payments' });
    const { show, calls, helpers } = details(list([watcher]), { 'PUT /api/attention/settings': () => json({}) });
    const d = await show('watcher:payments');
    expect(d.textContent).toContain('Next check: 9:55');
    press(d, 'Turn off');
    await flush();
    expect(calls).toContainEqual({ method: 'PUT', path: '/api/attention/settings', body: { watchers: { payments: false } } });
    helpers.update(list([{ ...watcher, state: 'paused' }]));
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
