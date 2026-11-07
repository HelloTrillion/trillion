// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { deviceId, keptId, seenLine } from '../src/devices';
import { renderScheduled, type Scheduled } from '../src/notice';
import { INITIAL, reduce } from '../src/view';

function memory(): { getItem(k: string): string | null; setItem(k: string, v: string): void; data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
}

const alert = (over: Partial<Scheduled> = {}): Scheduled => ({
  kind: 'scheduled',
  id: 'n1',
  what: 'heads_up',
  title: 'Security alert',
  text: 'Firefox just signed in for the first time.',
  dueMs: 0,
  due: '9:55 am',
  late: false,
  conversationId: null,
  link: null,
  detail: 'New sign-in: Firefox',
  quiet: null,
  spoken: true,
  critical: true,
  ...over,
});

const words = (text: string): HTMLElement[] => {
  const p = document.createElement('p');
  p.textContent = text;
  return [p];
};

describe('this page as a device (SEC-11)', () => {
  it('makes its id once and keeps it', () => {
    const s = memory();
    const first = deviceId(s, (a) => a.fill(7));
    expect(first).toMatch(/^[\w-]{16,128}$/);
    expect(deviceId(s, (a) => a.fill(9))).toBe(first);
  });

  it('has none where storage is blocked, and makes a new one over a bad one', () => {
    expect(deviceId(null)).toBeNull();
    const broken = { getItem: () => { throw new Error('blocked'); }, setItem: () => undefined };
    expect(deviceId(broken)).toBeNull();
    const s = memory();
    s.setItem('trillion-device', 'bad id!');
    expect(deviceId(s, (a) => a.fill(1))).not.toBe('bad id!');
  });

  it('knows its own id as the core keeps it: the first 32 hex of its SHA-256', async () => {
    // sha256("abc")
    expect(await keptId('abc')).toBe('ba7816bf8f01cfea414140de5dae2223');
  });

  it('says when a device was first and last seen', () => {
    const now = new Date(Date.UTC(2026, 10, 7, 12));
    const d = { first_seen_ms: now.getTime() - 3 * 86_400_000, last_seen_ms: now.getTime() - 5 * 60_000 };
    expect(seenLine(d, now, false)).toMatch(/^First seen .+ · last seen 5 min ago$/);
    expect(seenLine(d, now, true)).toMatch(/in use now$/);
  });
});

describe('a security alert on the page', () => {
  it('is marked, with her line or with its title', () => {
    const spoken = renderScheduled(alert(), words);
    expect(spoken.querySelector('.notice-critical')?.textContent).toBe('Security');
    const shown = renderScheduled(alert({ text: null, spoken: false }), words);
    expect(shown.querySelector('.notice-critical')?.textContent).toBe('Security');
    expect(shown.textContent).toContain('Security alert');
    expect(renderScheduled(alert({ critical: false }), words).querySelector('.notice-critical')).toBeNull();
  });

  it('is critical only when the core says so', () => {
    const base = { type: 'notice', code: 'schedule', id: 'n1', kind: 'heads_up', title: 'Security alert', text: 'x' };
    const marked = reduce(INITIAL, { ...base, critical: true }).entries.at(-1);
    expect(marked).toMatchObject({ kind: 'scheduled', critical: true });
    const plain = reduce(INITIAL, { ...base, id: 'n2', critical: 'yes' }).entries.at(-1);
    expect(plain).not.toHaveProperty('critical');
  });
});
