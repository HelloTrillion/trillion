// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { MEMORY_NOTES_PATH, createNotesSwitch } from '../src/browser';

function mount<T extends { element: HTMLElement }>(s: T): T & { h: HTMLElement } {
  const h = document.createElement('div');
  h.append(s.element);
  document.body.append(h);
  return Object.assign(s, { h });
}

describe('Settings → Memories: notes from chats', () => {
  it('shows the saved choice and saves the switch', async () => {
    const calls: [string, string, unknown][] = [];
    let stored = false;
    const api = async (method: string, path: string, body?: unknown) => {
      calls.push([method, path, body]);
      if (method === 'PUT') stored = (body as { notes: boolean }).notes;
      return new Response(JSON.stringify({ notes: stored, count: 0 }), { status: 200 });
    };
    let told = 0;
    const s = mount(createNotesSwitch(api, () => void told++));
    const h = s.h;
    await vi.waitFor(() => expect(told).toBe(1));
    expect(s.summary()).toBe('Only when asked');
    await s.open();
    const box = h.querySelector<HTMLInputElement>('input[role="switch"]')!;
    expect(h.textContent).toContain('Keep notes from our chats');
    expect(h.textContent).toContain('Never a permission or a rule');
    expect(box.checked).toBe(false);
    box.checked = true;
    box.dispatchEvent(new Event('change'));
    await vi.waitFor(() => expect(box.disabled).toBe(false));
    expect(calls.at(-1)).toEqual(['PUT', MEMORY_NOTES_PATH, { notes: true }]);
    expect(box.checked).toBe(true);
    expect(s.summary()).toBe('Notes from chats');
  });

  it('is on when the core has nothing to say, and puts the switch back when saving fails', async () => {
    const api = async (method: string) => new Response('{}', { status: method === 'GET' ? 404 : 500 });
    const s = mount(createNotesSwitch(api, () => {}));
    const h = s.h;
    await s.open();
    const box = h.querySelector<HTMLInputElement>('input[role="switch"]')!;
    expect(box.checked).toBe(true);
    box.checked = false;
    box.dispatchEvent(new Event('change'));
    await vi.waitFor(() => expect(box.disabled).toBe(false));
    expect(box.checked).toBe(true);
    expect(h.querySelector('.settings-status')?.textContent).toContain('could not be saved');
  });
});
