// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { HER, loadFeatures } from '../src/features';
import { drawMindSection, mindUrl } from '../src/features/mind';
import { type Host, type Surface, createPanel } from '../src/panel';

function host(): Host & { children: Node[]; closed: boolean } {
  const h = {
    open: false,
    children: [] as Node[],
    closed: false,
    showModal() {},
    close() {
      h.closed = true;
    },
    replaceChildren(...nodes: (Node | string)[]) {
      h.children = nodes.filter((n): n is Node => typeof n !== 'string');
    },
    setAttribute() {},
    addEventListener() {},
  };
  return h;
}

describe('Settings → Her mind', () => {
  it('opens /mind.html with the token in the fragment', () => {
    const h = host();
    const opened: string[] = [];
    drawMindSection(h, (url) => opened.push(url), () => 'tok-1');
    const form = h.children[0] as HTMLElement;
    const go = [...form.querySelectorAll('button')].find((b) => b.textContent === 'Open her mind');
    go!.click();
    expect(opened).toEqual(['/mind.html#token=tok-1']);
  });

  it('never puts anything that is not a token in the address', () => {
    expect(mindUrl(null)).toBe('/mind.html');
    expect(mindUrl('a&b=c')).toBe('/mind.html');
  });

  it('plugs into the Her group, after Memories', () => {
    document.body.replaceChildren();
    let shown: HTMLElement | null = null;
    const surface: Surface = { open: false, show: (c) => void (shown = c), close: () => undefined, onClose: () => undefined };
    const panel = createPanel(surface);
    panel.add({ id: 'skills', label: 'skills', group: HER, icon: 'model', open: () => undefined });
    const features = loadFeatures({ panel, api: async () => new Response('{}', { status: 404 }), send: () => false, refresh: () => undefined, openConversation: () => undefined });
    expect(features.names).toContain('mind');
    panel.host('skills').showModal();
    const labels = [...((shown as HTMLElement | null)?.querySelectorAll('.panel-link-words > span') ?? [])].map((s) => s.textContent);
    expect(labels.indexOf('Her mind')).toBe(labels.indexOf('Memories') + 1);
  });
});
