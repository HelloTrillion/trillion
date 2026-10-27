// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { type DayResult, createThreads, shortDate, type ThreadsDeps } from '../src/threads';

const NOW = new Date('2026-10-26T09:05:00').getTime();
const flush = (): Promise<void> => new Promise((r) => setTimeout(r, 0));

const ROWS = {
  conversations: [
    { id: 'd1', title: 'Your day · Mon 26 Oct', last_message_at: NOW - 1000, kind: 'day', day: '2026-10-26' },
    { id: 'c1', title: 'Trip', last_message_at: NOW - 2000, kind: 'chat', day: null },
  ],
};

function setup(over: Partial<ThreadsDeps> = {}) {
  const root = document.createElement('div');
  document.body.append(root);
  const s = { opened: 0, current: 'c1' as string | null, dayId: null as string | null, result: 'opened' as DayResult };
  const list = createThreads({
    root,
    fetchPage: () => Promise.resolve(ROWS),
    rename: () => Promise.resolve(true),
    remove: () => Promise.resolve(true),
    restore: () => Promise.resolve(true),
    current: () => s.current,
    pick: () => undefined,
    deletedCurrent: () => undefined,
    now: () => NOW,
    openDay: () => {
      s.opened++;
      return Promise.resolve(s.result);
    },
    dayId: () => s.dayId,
    ...over,
  });
  const row = (): HTMLButtonElement => root.querySelector<HTMLButtonElement>('.thread-day')!;
  return { root, s, list, row };
}

afterEach(() => {
  document.body.replaceChildren();
});

describe('the Your day row', () => {
  it('sits above the search and the threads, with today and a sun', async () => {
    const { root, list, row } = setup();
    list.open();
    await flush();
    expect(root.firstElementChild).toBe(row());
    expect(row().querySelector('.thread-name')?.textContent).toBe('Your day');
    expect(row().querySelector('.thread-time')?.textContent).toBe(shortDate(NOW));
    expect(row().querySelector('svg.icon')).not.toBeNull();
    expect(row().closest('[role="listbox"]')).toBeNull();
  });

  it('opens the day and closes the list', async () => {
    const { s, list, row } = setup();
    list.open();
    await flush();
    row().click();
    await flush();
    expect(s.opened).toBe(1);
    expect(list.isOpen).toBe(false);
  });

  it('stays open and says so when the day could not be opened', async () => {
    const { root, s, list, row } = setup();
    s.result = 'failed';
    list.open();
    await flush();
    row().click();
    await flush();
    expect(list.isOpen).toBe(true);
    expect(row().disabled).toBe(false);
    await new Promise((r) => setTimeout(r, 80));
    expect(root.querySelector('[role="status"]')?.textContent).toBe("Couldn't open your day");
  });

  it('says nothing when a newer open took over', async () => {
    const { root, s, list, row } = setup();
    s.result = 'superseded';
    list.open();
    await flush();
    row().click();
    await flush();
    await new Promise((r) => setTimeout(r, 80));
    expect(row().disabled).toBe(false);
    expect(root.querySelector('[role="status"]')?.textContent).not.toBe("Couldn't open your day");
  });

  it("is marked current when today's day is open, known from the rows or the page", async () => {
    const a = setup();
    a.list.open();
    await flush();
    expect(a.row().getAttribute('aria-current')).toBeNull();
    a.list.close();
    a.s.current = 'd1';
    a.list.open();
    await flush();
    expect(a.row().getAttribute('aria-current')).toBe('true');

    const b = setup({ fetchPage: () => Promise.resolve({ conversations: [] }) });
    b.s.current = 'd9';
    b.s.dayId = 'd9';
    b.list.open();
    await flush();
    expect(b.row().getAttribute('aria-current')).toBe('true');
  });

  it('marks day conversations in the list with a small sun', async () => {
    const { root, list } = setup();
    list.open();
    await flush();
    const icons = (id: string) => root.querySelector(`li[data-id="${id}"]`)?.querySelectorAll('svg.icon').length;
    expect(icons('d1')).toBe(1);
    expect(icons('c1')).toBe(0);
  });
});
