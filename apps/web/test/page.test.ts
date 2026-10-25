import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

// The page's own markup, read as text: what a screen reader depends on is fixed here, so an edit to
// index.html cannot quietly lose it.
const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
const tags = [...html.matchAll(/<(\w+)\b([^>]*)>/g)].map((m) => ({ name: m[1]!, attrs: m[2]! }));
const ids = new Set(tags.flatMap((t) => /\bid="([^"]+)"/.exec(t.attrs)?.[1] ?? []));

describe('index.html', () => {
  it('names every icon-only button', () => {
    const bare = tags.filter((t) => t.name === 'button' && /\bicon-btn\b/.test(t.attrs) && !/\baria-label="[^"]+"/.test(t.attrs));
    expect(bare).toEqual([]);
  });

  it('points every aria reference and label at an id that exists', () => {
    const missing: string[] = [];
    for (const t of tags) {
      for (const m of t.attrs.matchAll(/\b(aria-controls|aria-labelledby|aria-describedby|for)="([^"]+)"/g)) {
        for (const ref of m[2]!.split(/\s+/)) if (!ids.has(ref)) missing.push(`${m[1]}=${ref}`);
      }
    }
    expect(missing).toEqual([]);
  });

  it('keeps a status region for her state in the sidebar and another outside it', () => {
    const aside = html.slice(html.indexOf('<aside'), html.indexOf('</aside>'));
    expect(aside).toMatch(/id="status"[^>]*role="status"/);
    const outside = html.replace(aside, '');
    expect(outside).toMatch(/id="announce"[^>]*role="status"/);
  });

  it('puts the sidebar before the workspace, so the tab order reads head, title, messages, ask bar, workspace', () => {
    const at = (s: string): number => html.indexOf(s);
    const order = ['id="hide-sidebar"', 'id="thread-switch"', 'id="new"', 'id="transcript"', 'id="input"', 'id="workspace"'];
    expect(order.map(at)).toEqual([...order.map(at)].sort((a, b) => a - b));
    expect(order.every((s) => at(s) > 0)).toBe(true);
  });

  it('labels the workspace by its title and hides the canvas from readers', () => {
    expect(html).toMatch(/id="workspace"[^>]*aria-labelledby="workspace-title"/);
    expect(html).toMatch(/id="galaxy" aria-hidden="true"/);
  });
});
