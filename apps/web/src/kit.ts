/**
 * The widget kit's style guide (`#kit`): every widget sample and every block sample from
 * kit/samples.json, drawn with the board's own cards (board.ts) in the board's own grid, so what
 * it shows is what she shows. A light/dark switch and a narrow/wide switch show the widgets in
 * both themes and at a phone's width; the rules she follows are beside them. It talks to nothing:
 * an ask tapped here only says what it would have asked.
 */
import { h } from './blocks';
import { type BoardView, createBoardView } from './board';
import SAMPLES from './kit/samples.json';
import { type Board, type Widget } from './view';

export interface Samples {
  readonly widgets: readonly Widget[];
  readonly blocks: readonly Widget[];
}

/** The rules she follows (the spec's "Tokens and rules"), in her own short words. */
export const RULES: readonly string[] = [
  'One job per widget. If it needs a paragraph to explain, it is two widgets.',
  'A number carries its unit and a comparison when one exists.',
  'Titles say what it shows (“Unpaid invoices”), never “Here’s your data”.',
  'Anything from a connector names its source.',
  'Empty is said plainly: “Nothing due this week”, never invented rows.',
  'Round to what matters.',
  'Prefer a catalogue widget; make a custom one only when none fits.',
  'Asks are for the obvious next question, not decoration; at most two.',
];

/** Whether the URL's fragment asks for the kit: `#kit`, alone or among `&`-joined others. */
export function isKit(hash: string): boolean {
  return /(?:^#|&)kit(?=&|$)/.test(hash);
}

/** A block sample's label: the block it shows (its first block's type), or its title. */
export function blockName(w: Widget): string {
  const first = Array.isArray(w['blocks']) ? (w['blocks'] as unknown[])[0] : undefined;
  const type = typeof first === 'object' && first !== null ? (first as Record<string, unknown>)['type'] : undefined;
  return typeof type === 'string' && type ? type : typeof w['title'] === 'string' ? w['title'] : w.type;
}

/** How long a "Would ask" note stays. */
export const NOTE_MS = 2400;

/** The theme the page is shown in. */
export type Theme = 'light' | 'dark';

export type KitWidth = 'narrow' | 'wide';

export interface KitOptions {
  /** The samples; the generated file by default. Tests pass their own. */
  readonly samples?: Samples;
  /** Show a theme: by default `data-theme` on the root, as the app sets it. main.ts also turns the galaxy. */
  readonly setTheme?: (theme: Theme) => void;
  /** Put the app's own theme back on leaving. By default the root's `data-theme` as it was. */
  readonly restoreTheme?: () => void;
  /** The clock; tests set it. */
  readonly now?: () => Date;
}

export interface Kit {
  /** The page, for the caller to put over the galaxy. */
  readonly el: HTMLElement;
  /** Stop every timer and put the app's theme back. */
  dispose(): void;
}

const root = (): HTMLElement => document.documentElement;
const shownTheme = (): Theme => (root().dataset['theme'] === 'light' ? 'light' : 'dark');

/** A two-way switch: a labelled group of two pressed-or-not buttons. */
function toggle<T extends string>(
  label: string,
  options: readonly { readonly value: T; readonly text: string }[],
  initial: T,
  onPick: (value: T) => void,
): { el: HTMLElement; set(value: T): void } {
  const el = h('div', 'kit-switch');
  el.setAttribute('role', 'group');
  el.setAttribute('aria-label', label);
  const buttons = options.map((o) => {
    const b = h('button', 'kit-switch-opt', o.text);
    b.type = 'button';
    b.dataset['value'] = o.value;
    b.addEventListener('click', () => {
      set(o.value);
      onPick(o.value);
    });
    el.append(b);
    return b;
  });
  function set(value: T): void {
    buttons.forEach((b, i) => {
      const on = options[i]?.value === value;
      b.setAttribute('aria-pressed', String(on));
      if (on) el.style.setProperty('--at', String(i));
    });
  }
  set(initial);
  return { el, set };
}

/** One section of the page: a heading, a line under it, and what it holds. */
function section(id: string, title: string, lede: string, ...content: HTMLElement[]): HTMLElement {
  const el = h('section', 'kit-section');
  el.setAttribute('aria-labelledby', id);
  const head = h('header', 'kit-section-head');
  const h2 = h('h2', 'kit-section-title', title);
  h2.id = id;
  head.append(h2, h('p', 'kit-section-lede', lede));
  el.append(head, ...content);
  return el;
}

/** Draw `widgets` with a board's own cards, each captioned in mono with `caption`. */
function grid(view: BoardView, widgets: readonly Widget[], caption: (w: Widget) => string): void {
  const board: Board = { title: 'Kit', open: true, widgets };
  view.draw(board);
  const cards = view.el.querySelectorAll<HTMLElement>('.bd-card');
  cards.forEach((card, i) => {
    const w = widgets[i];
    if (!w) return;
    const cap = h('p', 'kit-cap', caption(w));
    cap.setAttribute('aria-hidden', 'true');
    card.prepend(cap);
  });
}

export function createKit(opts: KitOptions = {}): Kit {
  const samples: Samples = opts.samples ?? (SAMPLES as unknown as Samples);
  const was = root().dataset['theme'];
  const setTheme = opts.setTheme ?? ((t: Theme): void => void (root().dataset['theme'] = t));
  const restoreTheme =
    opts.restoreTheme ??
    ((): void => {
      if (was === undefined) delete root().dataset['theme'];
      else root().dataset['theme'] = was;
    });

  const el = h('div', 'kit');
  el.setAttribute('role', 'main');
  el.setAttribute('aria-label', 'Widget kit');
  el.tabIndex = -1;
  const page = h('div', 'kit-page');

  // The header: what this is, and the two switches.
  const head = h('header', 'kit-head');
  const words = h('div', 'kit-head-words');
  words.append(
    h('p', 'kit-eyebrow', 'Style guide'),
    h('h1', 'kit-title', 'Widget kit'),
    h('p', 'kit-lede', 'Every widget and block she can put on her board, drawn exactly as the board draws them.'),
  );
  const controls = h('div', 'kit-controls');
  const theme = toggle<Theme>(
    'Theme',
    [
      { value: 'light', text: 'Light' },
      { value: 'dark', text: 'Dark' },
    ],
    shownTheme(),
    (t) => setTheme(t),
  );
  const width = toggle<KitWidth>(
    'Width',
    [
      { value: 'narrow', text: 'Narrow' },
      { value: 'wide', text: 'Wide' },
    ],
    'wide',
    (w) => el.classList.toggle('kit-narrow', w === 'narrow'),
  );
  // Leaving is a change of fragment: whoever opened the page closes it.
  const done = h('a', 'kit-done', 'Done');
  done.href = '#';
  controls.append(theme.el, width.el, done);
  head.append(words, controls);

  // Asks go nowhere here: a quiet note says what would have been asked.
  const note = h('p', 'kit-note');
  note.setAttribute('role', 'status');
  note.hidden = true;
  let noteTimer: ReturnType<typeof setTimeout> | null = null;
  const onAsk = (text: string): void => {
    note.textContent = `Would ask: ${text}`;
    note.hidden = false;
    if (noteTimer !== null) clearTimeout(noteTimer);
    noteTimer = setTimeout(() => {
      note.hidden = true;
      noteTimer = null;
    }, NOTE_MS);
  };
  const boardOpts = { onRemove: () => {}, onShowcase: () => {}, onAsk, ...(opts.now ? { now: opts.now } : {}) };
  const widgetView = createBoardView(boardOpts);
  const blockView = createBoardView(boardOpts);

  const rules = h('ol', 'kit-rules');
  for (const r of RULES) rules.append(h('li', 'kit-rule', r));
  const motto = h('p', 'kit-motto', 'She picks the content; the system picks the emphasis.');

  page.append(
    head,
    section('kit-widgets', 'Widgets', 'The catalogue: each type with sample content, at the size she would pick.', widgetView.el),
    section('kit-blocks', 'Blocks', 'The pieces every widget is made of, one to a card.', blockView.el),
    section('kit-rules', 'Rules', 'What she follows when she makes one.', rules, motto),
  );
  el.append(page, note);

  grid(widgetView, samples.widgets, (w) => `${w.type} · ${w.size}`);
  grid(blockView, samples.blocks, blockName);

  let gone = false;
  return {
    el,
    dispose() {
      if (gone) return;
      gone = true;
      widgetView.dispose();
      blockView.dispose();
      if (noteTimer !== null) clearTimeout(noteTimer);
      noteTimer = null;
      el.remove();
      restoreTheme();
    },
  };
}
