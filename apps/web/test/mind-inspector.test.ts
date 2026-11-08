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
      load: async () => ({ text: '<img src=x onerror=alert(1)>', kind: 'fact', source: 'user_statement', created_ms: 1, similar: [{ id: 'mem_b', label: 'b', score: 0.71 }, { id: 'gone', label: 'not drawn', score: 0.65 }] }),
      node: (id) => nodes.get(id),
      hop: (id) => hops.push(id),
      close: () => undefined,
    });
    expect(root.hidden).toBe(true);
    // Similar ids come with or without the prefix; this one names mem:mem_b, not drawn here.
    nodes.set('mem:mem_b', mem('mem:mem_b', 'b'));
    await insp.open('mem:a');
    expect(root.hidden).toBe(false);
    expect(root.querySelector('img')).toBeNull();
    expect(root.querySelector('.mind-body')?.textContent).toBe('<img src=x onerror=alert(1)>');
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

  it('says so when there is no detail', async () => {
    const root = document.createElement('aside');
    const insp = createInspector(root, { load: async () => null, node: (id) => mem(id, id), hop: () => undefined, close: () => undefined });
    await insp.open('mem:a');
    expect(root.textContent).toContain('No detail for this one.');
  });
});
