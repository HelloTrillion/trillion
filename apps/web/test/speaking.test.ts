// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import type { Host } from '../src/panel';
import {
  checkText,
  createSpeaking,
  errorKindText,
  fromMinor,
  plainWhy,
  probeText,
  speakingSummary,
  toMinor,
  type AttentionState,
  type Find,
} from '../src/speaking';

const flush = async (): Promise<void> => {
  for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
};

const json = (body: unknown, status = 200): Response => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

const STATE: AttentionState = {
  settings: {
    watchers: { meetings: true, email: false, payments: false },
    meeting_lead_min: 5,
    payment_limit: { amount_minor: 50000, currency: 'usd' },
    quiet_hours: { on: true, from: '22:00', to: '08:00' },
    meeting: true,
    mic: true,
    focus: true,
  },
  status: {
    watchers: {
      meetings: { last_check_ms: Date.now() - 180_000, last_error: 'TimeoutError', notes: 'Skip the standup.' },
      email: { last_check_ms: null, last_error: null, notes: '' },
      payments: { last_check_ms: null, last_error: null, notes: '' },
    },
    probes: {
      mic: { status: 'works', why: '', note: "a call in this same browser can't be told apart; the meeting check covers it" },
      focus: { status: "can't tell", why: 'macOS keeps Focus private from apps here' },
    },
  },
};

const FINDS: Find[] = [
  { key: 'meeting:e1:1', watcher: 'meetings', first_seen_ms: Date.now() - 60_000, status: 'told', title: 'Meeting soon', detail: 'Planning <b>with</b> Sam at 10:00 am' },
  { key: 'email:m1', watcher: 'email', first_seen_ms: Date.now() - 3_600_000, status: 'lost', title: null, detail: 'From Pat' },
];

function fakeHost(root: HTMLElement): Host {
  const listeners: (() => void)[] = [];
  const host = {
    open: false,
    showModal() {
      host.open = true;
    },
    close() {
      host.open = false;
      for (const l of listeners) l();
    },
    replaceChildren: (...nodes: (Node | string)[]) => root.replaceChildren(...nodes),
    setAttribute: (name: string, value: string) => root.setAttribute(name, value),
    addEventListener: (_: 'close', l: () => void) => listeners.push(l),
  };
  return host;
}

interface Call {
  method: string;
  path: string;
  body: unknown;
}

function setup(answer?: (c: Call) => Response | undefined) {
  const root = document.createElement('div');
  document.body.append(root);
  const calls: Call[] = [];
  const api = (method: string, path: string, body?: unknown): Promise<Response> => {
    const c = { method, path, body };
    calls.push(c);
    const own = answer?.(c);
    if (own) return Promise.resolve(own);
    if (path === '/api/attention/settings') return Promise.resolve(json(STATE));
    if (path === '/api/attention/finds') return Promise.resolve(json({ finds: FINDS }));
    if (path.startsWith('/api/attention/finds/')) return Promise.resolve(json({ finds: FINDS.slice(1) }));
    if (path.startsWith('/api/attention/notes/')) return Promise.resolve(json(STATE));
    return Promise.resolve(json({ error: 'not_found' }, 404));
  };
  const panel = createSpeaking(fakeHost(root), api, () => undefined);
  return { root, calls, panel };
}

const input = (root: HTMLElement, id: string): HTMLInputElement => root.querySelector(`#speaking-${CSS.escape(id)}`) as HTMLInputElement;
const writes = (calls: Call[]): Call[] => calls.filter((c) => c.method !== 'GET');

afterEach(() => {
  document.body.replaceChildren();
});

describe('speaking up, in words', () => {
  it('says what each probe found here', () => {
    expect(probeText({ status: 'works', why: '' })).toBe('Works here');
    expect(probeText({ status: "can't tell", why: 'needs pactl or pw-dump' })).toBe("Can't tell here: needs pactl or pw-dump");
    expect(probeText({ status: "didn't answer", why: 'it took too long' })).toBe("Didn't answer just now");
    expect(probeText(undefined)).toBe('');
  });

  it('says when a watcher last checked, and what last went wrong by its kind', () => {
    const now = 1_000_000;
    expect(checkText({ last_check_ms: now - 30_000, last_error: null, notes: '' }, now)).toBe('Last checked 30 s ago.');
    expect(checkText({ last_check_ms: now - 600_000, last_error: 'TimeoutError', notes: '' }, now)).toBe('Last checked 10 min ago. Last time: it took too long.');
    expect(checkText({ last_check_ms: null, last_error: null, notes: '' }, now)).toBe('Not checked yet.');
    expect(checkText({ last_check_ms: null, last_error: 'AsksFirst', notes: '' }, now)).toBe('Not checked yet. Last time: Stripe is set to ask first, so it can’t check on its own.');
    expect(errorKindText('NotConnected')).toBe('the Google account isn’t connected');
    expect(errorKindText('StripeError')).toBe('Stripe did not answer');
    expect(errorKindText('signed_out')).toBe('the Google sign-in ended; connect it again');
    expect(errorKindText('busy')).toBe('Google said too many requests; it tries again later');
    // A kind with no words of its own is still named, never hidden.
    expect(errorKindText('ValueError')).toBe('an error (ValueError)');
  });

  it('turns an amount into the currency’s minor unit, without floats', () => {
    expect(toMinor('500', 'usd')).toBe(50000);
    expect(toMinor('1,250.5', 'usd')).toBe(125050);
    expect(toMinor('0.29', 'usd')).toBe(29);
    expect(toMinor('5000', 'jpy')).toBe(5000);
    expect(toMinor('', 'usd')).toBeNull();
    expect(toMinor('12.345', 'usd')).toEqual({ error: 'Use at most 2 digits after the point.' });
    expect(toMinor('abc', 'usd')).toHaveProperty('error');
    expect(toMinor('0', 'usd')).toHaveProperty('error');
    expect(fromMinor({ amount_minor: 50000, currency: 'usd' })).toBe('500');
    expect(fromMinor({ amount_minor: 125050, currency: 'usd' })).toBe('1250.50');
    expect(fromMinor({ amount_minor: 5, currency: 'usd' })).toBe('0.05');
    expect(fromMinor(null)).toBe('');
  });

  it('puts a refusal in plain words', () => {
    expect(plainWhy('meeting_lead_min must be a whole number from 2 to 30')).toBe('The heads-up time must be a whole number from 2 to 30.');
    expect(plainWhy('quiet_hours from and to must differ')).toBe('Quiet hours must start and end at different times.');
    expect(plainWhy('currency must be a three-letter code such as usd')).toBe('The currency must be a three-letter code such as usd.');
  });

  it('sums up which watchers are on', () => {
    expect(speakingSummary(null)).toBe('');
    expect(speakingSummary(STATE.settings)).toBe('Meetings');
    expect(speakingSummary({ ...STATE.settings, watchers: { meetings: false, email: false, payments: false } })).toBe('Off');
  });
});

describe('the Speaking up panel', () => {
  it('draws the settings, what each watcher sends, the probes and recent finds', async () => {
    const { root, panel } = setup();
    await panel.open();
    const text = root.textContent ?? '';
    expect(input(root, 'watcher:meetings').checked).toBe(true);
    expect(input(root, 'watcher:email').checked).toBe(false);
    expect(input(root, 'watcher:email').getAttribute('role')).toBe('switch');
    expect(text).toContain("Sends each important email's sender, subject and preview to your model provider.");
    expect(text).toContain('Last checked 3 min ago. Last time: it took too long.');
    expect(input(root, 'lead').value).toBe('5');
    expect(input(root, 'limit').value).toBe('500');
    expect(input(root, 'from').value).toBe('22:00');
    expect(input(root, 'meeting').checked).toBe(true);
    // The meeting hold reads the calendar even with heads-ups off, and says so.
    expect(text).toContain('reads your Google Calendar every minute even with Meetings off');
    expect(text).toContain('Works here');
    expect(text).toContain("A call in this same browser can't be told apart; the meeting check covers it.");
    expect(text).toContain("Can't tell here: macOS keeps Focus private from apps here");
    expect(text).toContain('Skip the standup.');
    expect(text).toContain('Told you');
    expect(text).toContain('Lost: stopped before telling you');
    // A find's detail is someone else's words: text, never markup.
    expect(root.querySelector('b')).toBeNull();
    expect(text).toContain('Planning <b>with</b> Sam at 10:00 am');
    // Every field has its label.
    for (const id of ['lead', 'limit', 'from', 'to']) expect(root.querySelector(`label[for="speaking-${id}"]`)).not.toBeNull();
  });

  it('sends each change alone, as a partial PUT', async () => {
    const { root, calls, panel } = setup();
    await panel.open();
    const flip = (id: string): void => {
      const box = input(root, id);
      box.checked = !box.checked;
      box.dispatchEvent(new Event('change'));
    };
    flip('watcher:email');
    await flush();
    const lead = input(root, 'lead');
    lead.value = '10';
    lead.dispatchEvent(new Event('change'));
    await flush();
    const limit = input(root, 'limit');
    limit.value = '';
    limit.dispatchEvent(new Event('change'));
    await flush();
    const to = input(root, 'to');
    to.value = '07:30';
    to.dispatchEvent(new Event('change'));
    await flush();
    flip('quiet');
    await flush();
    flip('mic');
    await flush();
    flip('meeting');
    await flush();
    expect(writes(calls)).toEqual([
      { method: 'PUT', path: '/api/attention/settings', body: { watchers: { email: true } } },
      { method: 'PUT', path: '/api/attention/settings', body: { meeting_lead_min: 10 } },
      { method: 'PUT', path: '/api/attention/settings', body: { payment_limit: null } },
      { method: 'PUT', path: '/api/attention/settings', body: { quiet_hours: { to: '07:30' } } },
      { method: 'PUT', path: '/api/attention/settings', body: { quiet_hours: { on: false } } },
      { method: 'PUT', path: '/api/attention/settings', body: { mic: false } },
      { method: 'PUT', path: '/api/attention/settings', body: { meeting: false } },
    ]);
  });

  it('sends a limit in the minor unit', async () => {
    const { root, calls, panel } = setup();
    await panel.open();
    const limit = input(root, 'limit');
    limit.value = '1250.50';
    limit.dispatchEvent(new Event('change'));
    await flush();
    expect(writes(calls)).toEqual([{ method: 'PUT', path: '/api/attention/settings', body: { payment_limit: { amount_minor: 125050, currency: 'usd' } } }]);
  });

  it('shows a refusal’s why in plain words, announced, and puts the field back', async () => {
    const { root, panel } = setup((c) =>
      c.method === 'PUT' ? json({ error: 'bad_setting', why: 'meeting_lead_min must be a whole number from 2 to 30' }, 422) : undefined,
    );
    await panel.open();
    const lead = input(root, 'lead');
    lead.value = '1';
    lead.dispatchEvent(new Event('change'));
    await flush();
    const alert = root.querySelector('[role="alert"]') as HTMLElement;
    expect(alert.hidden).toBe(false);
    expect(alert.textContent).toBe('The heads-up time must be a whole number from 2 to 30.');
    expect(input(root, 'lead').value).toBe('5');
  });

  it('clears notes and dismisses a find by their routes, each with a JSON body', async () => {
    const { root, calls, panel } = setup();
    await panel.open();
    (root.querySelector('button[aria-label="Clear the meetings notes"]') as HTMLButtonElement).click();
    await flush();
    (root.querySelector('button[aria-label^="Dismiss Meeting soon"]') as HTMLButtonElement).click();
    await flush();
    expect(writes(calls)).toEqual([
      { method: 'DELETE', path: '/api/attention/notes/meetings', body: {} },
      { method: 'POST', path: `/api/attention/finds/${encodeURIComponent('meeting:e1:1')}/dismiss`, body: {} },
    ]);
    // The answer is the finds as they now stand.
    expect(root.textContent).not.toContain('Planning <b>with</b> Sam');
  });

  it('draws no meeting switch for a core that has none', async () => {
    const { meeting: _, ...older } = STATE.settings;
    const { root, panel } = setup((c) => (c.method === 'GET' && c.path === '/api/attention/settings' ? json({ ...STATE, settings: older }) : undefined));
    await panel.open();
    expect(input(root, 'meeting')).toBeNull();
    expect(input(root, 'mic')).not.toBeNull();
  });
});
