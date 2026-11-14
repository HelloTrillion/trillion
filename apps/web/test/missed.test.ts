// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { type MissedItem, createMissed, expiredText, parseItems } from '../src/features/missed';

const NOW = new Date(2026, 10, 14, 10, 0).getTime();

function item(over: Partial<MissedItem> = {}): MissedItem {
  return {
    pending_id: 'p1',
    tool_label: 'Send an email',
    summary: 'Send "Lunch?" to sam@example.com',
    expired_at: new Date(2026, 10, 14, 9, 55).getTime(),
    shown: true,
    conversation_id: 'c1',
    why_not: null,
    ...over,
  };
}

function setup(here = 'c1') {
  const sent: Record<string, unknown>[] = [];
  const opened: string[] = [];
  let connected = true;
  const missed = createMissed({
    send: (body) => {
      if (connected) sent.push(body);
      return connected;
    },
    open: (id) => void opened.push(id),
    now: () => NOW,
  });
  document.body.replaceChildren(missed.element);
  missed.handle({ type: 'session', id: here });
  return {
    missed,
    sent,
    opened,
    offline: () => {
      connected = false;
    },
    rows: () => [...missed.element.querySelectorAll<HTMLLIElement>('.missed-row')],
    button: (id: string, words: string) =>
      [...missed.element.querySelectorAll<HTMLButtonElement>(`.missed-row[data-id="${id}"] button`)].find((b) => b.textContent === words) ?? null,
  };
}

describe('missed approvals under Needs you', () => {
  it('lists each one on a line: its summary and when it expired, with Ask again and Dismiss', () => {
    const t = setup();
    expect(t.missed.element.hidden).toBe(true);
    t.missed.handle({ type: 'unseen', items: [item(), item({ pending_id: 'p2', summary: '<b>two</b>', shown: false })] });
    expect(t.missed.element.hidden).toBe(false);
    expect(t.missed.element.querySelector('h2')?.textContent).toBe('Needs you');
    expect(t.missed.ids).toEqual(['p1', 'p2']);
    const [one, two] = t.rows();
    expect(one?.querySelector('.missed-what')?.textContent).toBe('Send "Lunch?" to sam@example.com');
    expect(one?.querySelector('.missed-when')?.textContent).toBe('Expired 9:55');
    // Text, never markup.
    expect(two?.querySelector('.missed-what')?.textContent).toBe('<b>two</b>');
    expect(two?.querySelector('b')).toBeNull();
    expect(two?.querySelector('.missed-when')?.textContent).toBe('Expired 9:55, never shown');
    expect(t.button('p1', 'Ask again')).not.toBeNull();
    expect(t.button('p1', 'Dismiss')).not.toBeNull();
    // Nothing is live: the lines are never announced or spoken.
    expect(t.missed.element.getAttribute('aria-live')).toBeNull();
  });

  it('asks again in the conversation it is in', () => {
    const t = setup('c1');
    t.missed.handle({ type: 'unseen', items: [item()] });
    t.button('p1', 'Ask again')?.click();
    expect(t.sent).toEqual([{ type: 'unseen.ask', pending_id: 'p1' }]);
    expect(t.button('p1', 'Ask again')?.disabled).toBe(true);
    t.missed.handle({ type: 'unseen.done', pending_id: 'p1', outcome: 'asked', text: null });
    expect(t.rows()).toEqual([]);
    expect(t.missed.element.hidden).toBe(true);
    // Never again, even if a later hello still lists it.
    t.missed.handle({ type: 'unseen', items: [item()] });
    expect(t.rows()).toEqual([]);
  });

  it('opens the card’s own conversation first, then asks there', () => {
    const t = setup('elsewhere');
    t.missed.handle({ type: 'unseen', items: [item()] });
    t.button('p1', 'Ask again')?.click();
    expect(t.opened).toEqual(['c1']);
    expect(t.sent).toEqual([]);
    // That conversation's hello brings the list again: now it is asked.
    t.missed.handle({ type: 'session', id: 'c1' });
    t.missed.handle({ type: 'unseen', items: [item()] });
    expect(t.sent).toEqual([{ type: 'unseen.ask', pending_id: 'p1' }]);
  });

  it('keeps one it could not ask again, saying why', () => {
    const t = setup();
    t.missed.handle({ type: 'unseen', items: [item()] });
    t.button('p1', 'Ask again')?.click();
    t.missed.handle({ type: 'unseen.done', pending_id: 'p1', outcome: 'refused', text: 'It can’t be done as it was.' });
    expect(t.missed.ids).toEqual(['p1']);
    expect(t.rows()[0]?.querySelector('.missed-note')?.textContent).toBe('It can’t be done as it was.');
    expect(t.button('p1', 'Ask again')?.disabled).toBe(false);
  });

  it('dismisses, for good', () => {
    const t = setup();
    t.missed.handle({ type: 'unseen', items: [item(), item({ pending_id: 'p2' })] });
    t.button('p2', 'Dismiss')?.click();
    expect(t.sent).toEqual([{ type: 'unseen.dismiss', pending_id: 'p2' }]);
    t.missed.handle({ type: 'unseen.done', pending_id: 'p2', outcome: 'dismissed', text: null });
    expect(t.missed.ids).toEqual(['p1']);
    t.missed.handle({ type: 'unseen', items: [item(), item({ pending_id: 'p2' })] });
    expect(t.missed.ids).toEqual(['p1']);
  });

  it('offers only Dismiss for one that can’t be asked again, and says why', () => {
    const t = setup();
    const why = "Her browser's task has ended, so this can't be asked again.";
    t.missed.handle({ type: 'unseen', items: [item({ conversation_id: null, why_not: why })] });
    expect(t.button('p1', 'Ask again')).toBeNull();
    expect(t.button('p1', 'Dismiss')).not.toBeNull();
    expect(t.rows()[0]?.querySelector('.missed-note')?.textContent).toBe(why);
  });

  it('says so when the page is not connected', () => {
    const t = setup();
    t.missed.handle({ type: 'unseen', items: [item()] });
    t.offline();
    t.button('p1', 'Dismiss')?.click();
    expect(t.rows()[0]?.querySelector('.missed-note')?.textContent).toBe('Not connected. Try again in a moment.');
    expect(t.button('p1', 'Dismiss')?.disabled).toBe(false);
  });

  it('keeps only well-formed items', () => {
    expect(parseItems(null)).toEqual([]);
    expect(parseItems([item(), { pending_id: '' }, { ...item(), expired_at: 'soon' }, 7])).toEqual([item()]);
    expect(expiredText({ expired_at: new Date(2026, 10, 12, 7, 0).getTime(), shown: true }, NOW)).toBe('Expired Thu 7:00');
  });
});
