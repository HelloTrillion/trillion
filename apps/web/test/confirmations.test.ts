// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import type { Host } from '../src/panel';
import {
  answeredText,
  channelText,
  chipText,
  chips,
  confirmationsSummary,
  createConfirmations,
  emptyText,
  flagged,
  outcomeText,
  query,
  shownText,
  statusText,
  unknownBanner,
  LIFECYCLE,
  type Confirmation,
  type ConfirmationsState,
  type Counts,
} from '../src/confirmations';

const when = (ms: number): string => `t${ms}`;

const ZERO: Counts = { all: 0, pending: 0, approved: 0, executing: 0, succeeded: 0, failed: 0, unknown: 0, rejected: 0, expired: 0, cancelled: 0 };

function row(over: Partial<Confirmation> = {}): Confirmation {
  return {
    id: 'p1',
    tool: 'file_create',
    tool_label: 'Create a file',
    summary: 'Create notes.txt in Projects',
    reason: 'write',
    status: 'succeeded',
    created_at: 1_000,
    expires_at: 601_000,
    delivered_at: 2_000,
    surface: 'web',
    decided_at: 3_000,
    decided_by: 'c1',
    decided_by_kind: 'asking_conversation',
    decision: 'approved',
    channel: 'tap',
    finished_at: 4_000,
    effect_status: null,
    detail: '',
    cancelled_reason: null,
    conversation_id: null,
    args_hash: 'abc123',
    args: { path: '/projects/notes.txt' },
    history: [],
    ...over,
  };
}

describe('confirmations in words', () => {
  it('names every status the record can hold', () => {
    expect(LIFECYCLE.map(statusText)).toEqual(['Waiting', 'Approved', 'Running', 'Done', 'Failed', 'Unknown', 'Declined', 'Expired', 'Cancelled']);
  });

  it('says how an answer arrived as the user would', () => {
    expect(channelText('tap')).toBe('a tap on the card');
    expect(channelText('voice')).toBe('spoken');
    expect(channelText('text')).toBe('typed');
    expect(channelText('channel')).toBe('a message');
    expect(channelText('elsewhere')).toBe('elsewhere');
  });

  it('never makes up a time for a card nobody saw', () => {
    expect(shownText(row({ delivered_at: null }), when)).toBe('Never shown on a screen');
    expect(shownText(row(), when)).toBe('t2000');
  });

  it('says who answered, how and when, or that nobody did', () => {
    expect(answeredText(row(), when)).toBe('Approved (a tap on the card) in the conversation that asked, t3000');
    expect(answeredText(row({ decision: 'rejected', channel: 'voice' }), when)).toBe('Declined (spoken) in the conversation that asked, t3000');
    expect(answeredText(row({ decision: 'expired', decided_by_kind: 'system' }), when)).toBe('No answer before it expired, t3000');
    expect(answeredText(row({ decision: 'superseded' }), when)).toBe('No answer: a newer request replaced it, t3000');
    expect(answeredText(row({ status: 'pending', decision: null, decided_at: null }), when)).toBe('Not answered yet');
    expect(answeredText(row({ status: 'expired', decision: null, decided_at: null }), when)).toBe('No answer');
  });

  it('only calls a card waiting for your answer once it was shown', () => {
    expect(outcomeText(row({ status: 'pending' }))).toBe('Waiting for your answer, on its card.');
    expect(outcomeText(row({ status: 'pending', delivered_at: null }))).toBe('Waiting, but its card has not been shown yet.');
  });

  it('says what happened, with the recorded reason when it went wrong', () => {
    expect(outcomeText(row())).toBe('Done.');
    expect(outcomeText(row({ status: 'failed', detail: 'the folder is read only' }))).toBe('Failed. Recorded reason: the folder is read only.');
    expect(outcomeText(row({ status: 'unknown' }))).toMatch(/^Unknown: it may or may not have happened/);
    expect(outcomeText(row({ status: 'rejected' }))).toBe('Nothing was done.');
    expect(outcomeText(row({ status: 'cancelled', cancelled_reason: 'superseded' }))).toBe('Nothing was done: a newer request replaced it.');
    expect(outcomeText(row({ status: 'cancelled', cancelled_reason: 'not_sent' }))).toBe('Nothing went out: a check after a restart proved it.');
  });

  it('flags only an unknown outcome, and says how many there are', () => {
    expect(LIFECYCLE.filter((s) => flagged({ status: s }))).toEqual(['unknown']);
    expect(unknownBanner(ZERO)).toBe('');
    expect(unknownBanner({ ...ZERO, unknown: 1 })).toMatch(/^1 action has an unknown outcome/);
    expect(unknownBanner({ ...ZERO, unknown: 3 })).toMatch(/^3 actions have an unknown outcome/);
  });

  it('offers approved and running chips only while any are, or one is chosen', () => {
    expect(chips(ZERO, 'all')).toEqual(['all', 'pending', 'succeeded', 'failed', 'unknown', 'rejected', 'expired', 'cancelled']);
    expect(chips({ ...ZERO, executing: 1 }, 'all')).toContain('executing');
    expect(chips(ZERO, 'approved')).toContain('approved');
    expect(chipText('unknown', { ...ZERO, unknown: 2 })).toBe('Unknown 2');
    expect(chipText('all', ZERO)).toBe('All');
  });

  it('asks for the status and the window it shows', () => {
    expect(query('all', 30)).toBe('/api/confirmations?days=30');
    expect(query('unknown', 7)).toBe('/api/confirmations?days=7&status=unknown');
  });

  it('puts what needs a look first in the sidebar line', () => {
    expect(confirmationsSummary(null)).toBe('');
    expect(confirmationsSummary({ counts: ZERO })).toBe('None yet');
    expect(confirmationsSummary({ counts: { ...ZERO, all: 2, pending: 2 } })).toBe('2 waiting');
    expect(confirmationsSummary({ counts: { ...ZERO, all: 3, pending: 2, unknown: 1 } })).toBe('1 unknown');
    expect(emptyText('all', 7)).toBe('Nothing asked for your yes in the last 7 days.');
    expect(emptyText('failed', 30)).toBe('None failed in the last 30 days.');
  });
});

// ── The section ─────────────────────────────────────────────────────────

function fakeHost(root: HTMLElement): Host & { closes: number } {
  const listeners: (() => void)[] = [];
  const host = {
    open: false,
    closes: 0,
    showModal() {
      host.open = true;
    },
    close() {
      host.open = false;
      host.closes++;
      for (const l of listeners) l();
    },
    replaceChildren: (...nodes: (Node | string)[]) => root.replaceChildren(...nodes),
    setAttribute: (name: string, value: string) => root.setAttribute(name, value),
    addEventListener: (_: 'close', l: () => void) => listeners.push(l),
  };
  return host;
}

const json = (body: unknown, status = 200): Response => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const flush = (): Promise<void> => new Promise((r) => setTimeout(r, 0));

const ROWS: Confirmation[] = [
  row({ id: 'p-wait', status: 'pending', decision: null, decided_at: null, channel: null, decided_by_kind: null, finished_at: null, conversation_id: 'c1' }),
  row({ id: 'p-unknown', status: 'unknown', detail: 'the provider did not answer' }),
  row({ id: 'p-done', args: { path: '/projects/notes.txt', content: { $omitted: { sha256: 'f'.repeat(64), chars: 900 } } } }),
];
const STATE: ConfirmationsState = { now_ms: 10_000, days: 30, status: 'all', counts: { ...ZERO, all: 3, pending: 1, unknown: 1, succeeded: 1 }, actions: ROWS };

function setup() {
  const root = document.createElement('div');
  document.body.append(root);
  const calls: string[] = [];
  const opened: string[] = [];
  const host = fakeHost(root);
  const api = (method: string, path: string): Promise<Response> => {
    calls.push(`${method} ${path}`);
    const status = new URL(path, 'http://x').searchParams.get('status') ?? 'all';
    const actions = status === 'all' ? ROWS : ROWS.filter((r) => r.status === status);
    return Promise.resolve(json({ ...STATE, status, actions }));
  };
  const section = createConfirmations(host, api, () => undefined, (id) => opened.push(id));
  return { root, host, calls, opened, section };
}

afterEach(() => {
  document.body.replaceChildren();
});

describe('the confirmations section', () => {
  it('lists every action newest first, with an unknown one marked', async () => {
    const { root, section } = setup();
    await section.open();
    const cards = [...root.querySelectorAll<HTMLElement>('.confirmations-card')];
    expect(cards.map((c) => c.dataset.id)).toEqual(['p-wait', 'p-unknown', 'p-done']);
    expect(cards[1]!.classList.contains('is-unknown')).toBe(true);
    expect(cards[0]!.classList.contains('is-unknown')).toBe(false);
    expect(root.querySelector('.confirmations-unknown')?.textContent).toMatch(/^1 action has an unknown outcome/);
    expect(cards[1]!.textContent).toContain('the provider did not answer');
    expect(section.summary()).toBe('1 unknown');
  });

  it('shows the host words and the slim arguments as text only', async () => {
    const { root, section } = setup();
    await section.open();
    const done = root.querySelector<HTMLElement>('[data-id="p-done"]')!;
    expect(done.querySelector('strong')?.textContent).toBe('Create a file');
    expect(done.querySelector('.confirmations-summary')?.textContent).toBe('Create notes.txt in Projects');
    expect(done.querySelector('.confirmations-args')?.textContent).toContain('"$omitted"');
  });

  it('filters by status when a chip is pressed', async () => {
    const { root, calls, section } = setup();
    await section.open();
    root.querySelector<HTMLButtonElement>('[data-filter="unknown"]')!.click();
    await flush();
    await flush();
    expect(calls.at(-1)).toBe('GET /api/confirmations?days=30&status=unknown');
    expect([...root.querySelectorAll<HTMLElement>('.confirmations-card')].map((c) => c.dataset.id)).toEqual(['p-unknown']);
    expect(root.querySelector('[data-filter="unknown"]')?.getAttribute('aria-pressed')).toBe('true');
  });

  it('filters by date when the window changes', async () => {
    const { root, calls, section } = setup();
    await section.open();
    const pick = root.querySelector('select')!;
    pick.value = '7';
    pick.dispatchEvent(new Event('change'));
    await flush();
    expect(calls.at(-1)).toBe('GET /api/confirmations?days=7');
  });

  it('takes a waiting row to its card, and Settings closes', async () => {
    const { root, host, opened, section } = setup();
    await section.open();
    const go = [...root.querySelectorAll('button')].find((b) => b.textContent === 'Go to its card')!;
    expect(go.closest<HTMLElement>('.confirmations-card')?.dataset.id).toBe('p-wait');
    go.click();
    expect(opened).toEqual(['c1']);
    expect(host.closes).toBe(1);
  });

  it('cannot approve, decline or change anything: it only ever reads', async () => {
    const { root, calls, section } = setup();
    await section.open();
    // The status chips only filter the list; every other button is named here.
    const actions = [...root.querySelectorAll<HTMLButtonElement>('button:not([data-filter])')].map((b) => b.textContent);
    expect(actions).toEqual(['Go to its card']);
    for (const b of root.querySelectorAll<HTMLButtonElement>('button[data-filter]')) expect(b.hasAttribute('aria-pressed')).toBe(true);
    // Every button the page has, pressed: still nothing but reads.
    for (const b of [...root.querySelectorAll<HTMLButtonElement>('button')]) b.click();
    await flush();
    await flush();
    expect(calls.length).toBeGreaterThan(0);
    expect(calls.every((c) => c.startsWith('GET /api/confirmations?'))).toBe(true);
  });

  it('says so when it cannot load, and when there is nothing', async () => {
    const root = document.createElement('div');
    document.body.append(root);
    const down = createConfirmations(fakeHost(root), () => Promise.reject(new Error('down')), () => undefined);
    await down.open();
    expect(root.textContent).toContain('Confirmations could not be loaded.');
    const empty = document.createElement('div');
    document.body.append(empty);
    const none = createConfirmations(fakeHost(empty), () => Promise.resolve(json({ ...STATE, counts: ZERO, actions: [] })), () => undefined);
    await none.open();
    expect(empty.textContent).toContain('Nothing asked for your yes in the last 30 days.');
    expect(none.summary()).toBe('None yet');
  });
});
