// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { readyLine } from '../src/ready';
import { createThreads, type ThreadsDeps } from '../src/threads';

const NOW = new Date('2026-10-26T09:05:00').getTime();
const flush = (): Promise<void> => new Promise((r) => setTimeout(r, 0));

afterEach(() => {
  document.body.replaceChildren();
});

describe('the ready line', () => {
  it('says it is ready, and Open goes to the conversation the routine built', () => {
    const opened: string[] = [];
    const line = readyLine('Your Monday revenue is ready', 'r1', (id) => opened.push(id));
    expect(line.textContent).toContain('Your Monday revenue is ready');
    const open = line.querySelector('button')!;
    expect(open.textContent).toBe('Open');
    expect(open.getAttribute('aria-label')).toBe('Open: Your Monday revenue is ready');
    open.click();
    expect(opened).toEqual(['r1']);
  });

  it('sets its title only as text', () => {
    const line = readyLine('<img src=x onerror=alert(1)>', 'r1', () => undefined);
    expect(line.querySelector('img')).toBeNull();
    expect(line.textContent).toContain('<img src=x onerror=alert(1)>');
  });

  it('is only the words when there is nowhere to go', () => {
    expect(readyLine('Your day is ready', null, () => undefined).querySelector('button')).toBeNull();
  });
});

const ROWS = {
  conversations: [
    { id: 'd1', title: 'Your day · Mon 26 Oct', last_message_at: NOW - 1000, kind: 'day', day: '2026-10-26' },
    { id: 'r1', title: 'Monday revenue', last_message_at: NOW - 2000, kind: 'routine', day: null },
  ],
};

function setup(over: Partial<ThreadsDeps> = {}) {
  const root = document.createElement('div');
  document.body.append(root);
  const list = createThreads({
    root,
    fetchPage: () => Promise.resolve(ROWS),
    rename: () => Promise.resolve(true),
    remove: () => Promise.resolve(true),
    restore: () => Promise.resolve(true),
    current: () => 'c1',
    pick: () => undefined,
    deletedCurrent: () => undefined,
    now: () => NOW,
    openDay: () => Promise.resolve('opened'),
    dayId: () => 'd1',
    ...over,
  });
  const mark = (id: string): Element | null => root.querySelector(`li[data-id="${id}"] .thread-unread`);
  const dayMark = (): HTMLElement => root.querySelector<HTMLElement>('.thread-day .thread-unread')!;
  return { root, list, mark, dayMark };
}

describe('the unread mark', () => {
  it("marks a routine's conversation until it is opened", async () => {
    const { root, list, mark } = setup();
    list.markUnread('r1');
    list.open();
    await flush();
    expect(mark('r1')).not.toBeNull();
    expect(mark('d1')).toBeNull();
    expect(list.isUnread('r1')).toBe(true);
    // Read out with the row's name.
    const row = root.querySelector('li[data-id="r1"]')!;
    const labels = row.getAttribute('aria-labelledby')!.split(' ').map((id) => document.getElementById(id)?.getAttribute('aria-label') ?? document.getElementById(id)?.textContent);
    expect(labels).toContain('unread');
    list.markRead('r1');
    expect(mark('r1')).toBeNull();
    expect(list.isUnread('r1')).toBe(false);
  });

  it("marks the Your day row for the day's conversation, and clears it", async () => {
    const { list, dayMark } = setup();
    expect(dayMark().hidden).toBe(true);
    list.markUnread('d1');
    expect(dayMark().hidden).toBe(false);
    list.markRead('d1');
    expect(dayMark().hidden).toBe(true);
  });
});
