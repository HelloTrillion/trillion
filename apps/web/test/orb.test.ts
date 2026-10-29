// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createOrb } from '../src/orb';

type Handler = (e: { payload: unknown }) => void;

afterEach(() => {
  delete (window as unknown as { __TAURI__?: unknown }).__TAURI__;
});

describe('the floating orb page', () => {
  it('draws the state and caption from orb events, and opens the main window when clicked', async () => {
    let handler: Handler = () => {};
    const invoke = vi.fn(async () => undefined);
    (window as unknown as { __TAURI__: unknown }).__TAURI__ = {
      core: { invoke },
      event: {
        listen: async (_: string, h: Handler) => {
          handler = h;
          return () => {};
        },
      },
    };
    const root = document.createElement('button');
    createOrb(root);
    await new Promise((r) => setTimeout(r, 0));
    expect(root.dataset['state']).toBe('idle');

    handler({ payload: { state: 'listening', caption: 'what is on today' } });
    expect(root.dataset['state']).toBe('listening');
    expect(root.querySelector('.float-caption')?.textContent).toBe('what is on today');

    handler({ payload: { state: 'speaking', caption: 'x'.repeat(300) } });
    expect(root.dataset['state']).toBe('speaking');
    expect((root.querySelector('.float-caption')?.textContent ?? '').length).toBeLessThan(200);

    handler({ payload: { state: 'thinking', caption: '' } });
    expect(root.querySelector<HTMLElement>('.float-caption')?.hidden).toBe(true);

    root.click();
    expect(invoke).toHaveBeenCalledWith('show_main', undefined);
  });
});
