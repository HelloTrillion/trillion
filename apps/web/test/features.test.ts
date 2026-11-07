// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { HER, YOU, type Feature, type Message, loadFeatures } from '../src/features';
import { addIcon, icon } from '../src/icons';
import { type Panel, type SectionSpec, type Surface, createPanel } from '../src/panel';

let shown: HTMLElement | null = null;

function surface(): Surface {
  const listeners: (() => void)[] = [];
  return {
    open: false,
    show: (content) => {
      shown = content;
    },
    close: () => undefined,
    onClose: (l) => void listeners.push(l),
  };
}

const section = (id: string, group: string, after?: string): SectionSpec => ({
  id,
  label: id,
  group,
  icon: 'model',
  open: () => undefined,
  ...(after ? { after } : {}),
});

/** The rail's labels, group by group, as the user sees them once Settings opens. */
function rail(panel: Panel): string[] {
  panel.host('one').showModal();
  return [...(shown?.querySelectorAll('.panel-link-words > span') ?? [])].map((n) => n.textContent ?? '');
}

describe('features plug in', () => {
  it('sets up each feature in file-name order and hands every message to those that listen', () => {
    const order: string[] = [];
    const heard: string[] = [];
    const make = (name: string, listens: boolean): { feature: Feature } => ({
      feature: {
        setup(ctx) {
          order.push(name);
          if (listens) ctx.on((m: Message) => heard.push(`${name}:${m.type}`));
        },
      },
    });
    const ctx = {
      panel: {} as Panel,
      api: async () => new Response('{}'),
      send: () => true,
      refresh: () => undefined,
      openConversation: () => undefined,
    };
    const features = loadFeatures(ctx, {
      './features/b.ts': make('b', true),
      './features/a.ts': make('a', true),
      './features/c.ts': make('c', false),
      './features/helper.ts': {},
    });
    expect(order).toEqual(['a', 'b', 'c']);
    expect(features.names).toEqual(['a', 'b', 'c']);
    features.dispatch({ type: 'hello' });
    expect(heard).toEqual(['a:hello', 'b:hello']);
  });

  it('loads the real features from features/ and each places its section where it says', () => {
    document.body.replaceChildren();
    const panel = createPanel(surface());
    for (const s of [section('model', HER), section('skills', HER), section('voice', HER), section('reminders', YOU), section('usage', YOU)]) panel.add(s);
    const features = loadFeatures({
      panel,
      api: async () => new Response('{}', { status: 404 }),
      send: () => false,
      refresh: () => undefined,
      openConversation: () => undefined,
    });
    expect(features.names).toEqual(expect.arrayContaining(['confirmations', 'devices', 'memories']));
    const labels = rail(panel);
    expect(labels.indexOf('Memories')).toBe(labels.indexOf('skills') + 1);
    expect(labels.indexOf('Confirmations')).toBe(labels.indexOf('reminders') + 1);
  });
});

describe('a section placed after another', () => {
  it('goes straight after its anchor, after any placed there before it, and at the end without one', () => {
    document.body.replaceChildren();
    const panel = createPanel(surface());
    panel.add(section('one', HER));
    panel.add(section('two', HER));
    panel.add(section('first-after-one', HER, 'one'));
    panel.add(section('second-after-one', HER, 'one'));
    panel.add(section('lost', HER, 'nowhere'));
    expect(rail(panel)).toEqual(['one', 'first-after-one', 'second-after-one', 'two', 'lost']);
  });
});

describe('a feature brings its own icon', () => {
  it('draws it once added, and a name is taken only once', () => {
    addIcon('test-only-shape', ['M1 1h2']);
    expect(icon('test-only-shape').querySelectorAll('path')).toHaveLength(1);
    expect(() => addIcon('test-only-shape', ['M2 2h2'])).toThrow(/already exists/);
    expect(() => addIcon('model', ['M2 2h2'])).toThrow(/already exists/);
  });
});
