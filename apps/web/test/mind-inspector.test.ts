// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import type { MindNode } from '@hellotrillion/galaxy';
import { createInspector } from '../src/mind-inspector';

const mem = (id: string, label: string): MindNode => ({ id, type: 'memory', region: 'memory', label, size: 1, freshness: 1, extra: { kind: 'fact' } });

describe('the inspector', () => {
  it('shows a memory as text, lists similar ones, and hops to one on the page', async () => {
    const root = document.createElement('aside');
    const nodes = new Map([['mem:a', mem('mem:a', 'a')], ['mem:b', mem('mem:b', 'b')]]);
    const hops: string[] = [];
    const insp = createInspector(root, {
      load: async () => ({ text: '<img src=x onerror=alert(1)>', kind: 'user', source: 'user_statement', created_ms: 1, similar: [{ id: 'mem:mem_b', label: 'b', score: 0.71 }, { id: 'mem:gone', label: 'not drawn', score: 0.65 }] }),
      node: (id) => nodes.get(id),
      hop: (id) => hops.push(id),
      close: () => undefined,
    });
    expect(root.hidden).toBe(true);
    nodes.set('mem:mem_b', mem('mem:mem_b', 'b'));
    await insp.open('mem:a');
    expect(root.hidden).toBe(false);
    expect(root.querySelector('img')).toBeNull();
    expect(root.querySelector('.mind-body')?.textContent).toBe('<img src=x onerror=alert(1)>');
    // The Memories screen's words, not the raw values.
    expect(root.querySelector('.mind-badge')?.textContent).toBe('About you');
    expect(root.querySelector('.mind-facts')?.textContent).toContain('You said this');
    const buttons = [...root.querySelectorAll<HTMLButtonElement>('.mind-hop')];
    expect(buttons.map((b) => b.textContent)).toEqual(['b  ·  0.71']);
    expect(root.querySelector('.mind-item')?.textContent).toBe('not drawn  ·  0.65');
    buttons[0]!.click();
    expect(hops).toEqual(['mem:mem_b']);
  });

  it('drops an answer for a node no longer shown', async () => {
    const root = document.createElement('aside');
    let release: (v: Record<string, unknown>) => void = () => undefined;
    const insp = createInspector(root, {
      load: (id) => (id === 'mem:a' ? new Promise((r) => (release = r)) : Promise.resolve({ text: 'second' })),
      node: (id) => mem(id, id),
      hop: () => undefined,
      close: () => undefined,
    });
    const first = insp.open('mem:a');
    await insp.open('mem:b');
    release({ text: 'first' });
    await first;
    expect(root.querySelector('.mind-body')?.textContent).toBe('second');
  });

  it('says so when there is no detail (404), and when it could not be read (503)', async () => {
    const root = document.createElement('aside');
    const insp = createInspector(root, { load: async () => null, node: (id) => mem(id, id), hop: () => undefined, close: () => undefined });
    await insp.open('mem:a');
    expect(root.textContent).toContain('No detail for this one.');
    const failing = createInspector(root, { load: async () => Promise.reject(new Error('detail 503')), node: (id) => mem(id, id), hop: () => undefined, close: () => undefined });
    await failing.open('mem:a');
    expect(root.textContent).toContain("Couldn't load this one right now.");
  });

  it('shows a knowledge file from its text, and a connector by its label', async () => {
    const root = document.createElement('aside');
    const node = (id: string): MindNode =>
      id.startsWith('know:')
        ? { id, type: 'knowledge', region: 'knowledge', label: 'SOUL.md', size: 1, freshness: 0, extra: { kind: 'persona' } }
        : { id, type: 'connector', region: 'rim', label: 'google', size: 1, freshness: 1, extra: { status: 'ok' } };
    const insp = createInspector(root, {
      load: async (id) => (id.startsWith('know:') ? { id, type: 'knowledge', path: 'SOUL.md', kind: 'persona', modified_ms: 1, chars: 12, text: '# Who she is' } : { id, type: 'connector', label: 'Google', status: 'ok', tools: ['gmail_search'] }),
      node,
      hop: () => undefined,
      close: () => undefined,
    });
    await insp.open('know:SOUL.md');
    expect(root.querySelector('.mind-preview')?.textContent).toBe('# Who she is');
    await insp.open('conn:google');
    expect(root.querySelector('.mind-inspector-body h2')?.textContent).toBe('Google');
  });
});
