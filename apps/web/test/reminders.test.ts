import { describe, expect, it } from 'vitest';
import {
  STOPPED,
  checkedText,
  dueWords,
  kindText,
  nextText,
  remindersSummary,
  repeatText,
  resultLine,
  resultText,
  serverNow,
  sinceText,
  stale,
  troubled,
  type RemindersState,
  type Upcoming,
} from '../src/reminders';

const state = (over: Partial<RemindersState> = {}): RemindersState => ({
  active: [],
  recent: [],
  checked_ms: 1_000_000,
  tick_s: 15,
  now_ms: 1_012_000,
  ...over,
});

const one: Upcoming = {
  id: 'a1b2c3d4',
  kind: 'ask',
  title: 'Email',
  next_ms: 1,
  next: 'Mon 26 Oct, 9:00 am',
  when: 'Every weekday at 9:00 am',
  repeat: 'every weekday',
  last: null,
};

const due = Date.UTC(2026, 9, 24, 20, 0); // Sat 24 Oct, 4:00 pm in New York

describe('reminders', () => {
  it('says how long ago the scheduler checked', () => {
    expect(checkedText(state())).toBe('Checked 12 s ago');
    expect(checkedText(state({ checked_ms: null }))).toBe('The scheduler has not checked yet.');
  });

  it('turns into a warning once three checks and a margin have gone by', () => {
    expect(stale(state({ now_ms: 1_000_000 + 55_000 }))).toBe(false);
    expect(stale(state({ now_ms: 1_000_000 + 55_001 }))).toBe(true);
    expect(stale(state({ checked_ms: null }))).toBe(true);
    expect(checkedText(state({ now_ms: 1_000_000 + 600_000 }))).toBe(`Last checked 10 min ago. ${STOPPED}`);
    expect(sinceText(state({ now_ms: 1_000_000 + 3 * 3_600_000 }))).toBe('Last checked 3 h ago.');
  });

  it('keeps the server clock moving between reads, so a page left open goes stale', () => {
    const s = state();
    expect(serverNow(s, 0)).toBe(s);
    expect(stale(serverNow(s, 60_000))).toBe(true);
    expect(checkedText(serverNow(s, 3_000))).toBe('Checked 15 s ago');
  });

  it('sums them up for the sidebar', () => {
    expect(remindersSummary(null)).toBe('');
    expect(remindersSummary(state())).toBe('None set');
    expect(remindersSummary(state({ active: [one] }))).toBe('1 coming up');
    expect(remindersSummary(state({ active: [one], checked_ms: null }))).toBe('Not checking');
  });

  it('names each result and kind in words', () => {
    expect(resultText('late')).toBe('Delivered late');
    expect(resultText('blocked')).toBe('Waiting for a model');
    expect(resultText('failed')).toBe("Didn't finish");
    expect(kindText('ask')).toBe('Timed ask');
    expect(kindText('reminder')).toBe('Reminder');
    expect(troubled('missed') && troubled('failed') && !troubled('delivered') && !troubled('late')).toBe(true);
  });

  it('says how it repeats and when it comes next', () => {
    expect(repeatText('every weekday')).toBe('Repeats every weekday');
    expect(repeatText('every Mon and Thu')).toBe('Repeats every Mon and Thu');
    expect(repeatText('once')).toBe('Once');
    expect(nextText(one)).toBe('Next: Mon 26 Oct, 9:00 am');
    expect(nextText({ next: '' })).toBe('Due now');
  });

  it('gives each recent result the due time the core gave, and a reason only when it went wrong', () => {
    const r = { title: 'Email', due: 'Sat 24 Oct, 4:00 pm', due_ms: 0, detail: 'no model' };
    expect(resultLine({ ...r, status: 'blocked' })).toBe('Email: Waiting for a model (due Sat 24 Oct, 4:00 pm)');
    expect(resultLine({ ...r, status: 'failed', detail: 'no reply' })).toBe("Email: Didn't finish: no reply (due Sat 24 Oct, 4:00 pm)");
    expect(resultLine({ ...r, status: 'late', detail: 'retried' })).toBe('Email: Delivered late (due Sat 24 Oct, 4:00 pm)');
    // Only when the core could not say: this browser's clock.
    expect(dueWords(due, 'America/New_York')).toBe('Sat 24 Oct, 4:00 pm');
    expect(resultLine({ ...r, due: '', due_ms: due, status: 'missed', detail: '' }, 'America/New_York')).toBe('Email: Missed (due Sat 24 Oct, 4:00 pm)');
  });
});
