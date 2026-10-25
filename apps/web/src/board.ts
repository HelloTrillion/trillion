/**
 * Her board, drawn in the workspace: one glass card per widget, laid out in a grid that follows
 * the workspace's width. Each card is its title, its blocks (blocks.ts), and a quiet foot: where
 * it came from and how long ago, and up to two asks she offers as the obvious next question.
 * Everything she put there may quote a page she read (SPN-5), so every piece of text is set with
 * textContent and nothing is ever parsed as HTML.
 */
import { type DrawnBlocks, drawBlocks, h, records, str } from './blocks';
import { type SourceTile, mark } from './connected';
import { icon } from './icons';
import { type Board, type Widget, type WidgetSize } from './view';
import { age } from './when';

// ── Pure helpers ────────────────────────────────────────────────────────

/** How many columns a widget takes on a wide workspace (of 6) and a middling one (of 2). */
export function spans(size: WidgetSize): { readonly wide: number; readonly mid: number } {
  switch (size) {
    case 'small':
      return { wide: 2, mid: 1 };
    case 'wide':
      return { wide: 4, mid: 2 };
    case 'full':
      return { wide: 6, mid: 2 };
    default:
      return { wide: 3, mid: 1 };
  }
}

/** The least height a chart is drawn at in a card of each size, in CSS px; it grows with its card. */
export function chartHeight(size: WidgetSize): number {
  return size === 'small' ? 150 : size === 'medium' ? 200 : 230;
}

export type RowFit = 'grow' | 'fit';

export interface RowPlan {
  /** Columns each widget takes, in order. Every row adds up to the full width. */
  readonly spans: readonly number[];
  /** Per row: one with a chart shares the spare height; the rest keep their natural height. */
  readonly rows: readonly RowFit[];
}

/** Whether a widget's blocks hold a line, bar or area chart (one that fills spare height), at the top or in a column. */
export function hasChart(blocks: unknown): boolean {
  return records(blocks).some(
    (b) =>
      (b['type'] === 'chart' && b['kind'] !== 'donut') ||
      (b['type'] === 'columns' && Array.isArray(b['columns']) && (b['columns'] as unknown[]).some((c) => hasChart(c))),
  );
}

/**
 * Widgets laid out in rows of `columns`, in her order. A widget that does not fit what is left
 * of a row starts the next one, and the last widget of a row widens to fill it, so a row never
 * has a hole and a widget alone takes the whole width.
 */
export function planRows(
  widgets: readonly { readonly size: WidgetSize; readonly type: string; readonly blocks?: unknown }[],
  columns: number,
): RowPlan {
  const cols = Math.max(1, Math.floor(columns));
  const out: number[] = [];
  const rows: RowFit[] = [];
  let used = 0;
  let grow = false;
  const end = (): void => {
    if (used === 0) return;
    out[out.length - 1]! += cols - used;
    rows.push(grow ? 'grow' : 'fit');
    used = 0;
    grow = false;
  };
  for (const w of widgets) {
    const sp = spans(w.size);
    const want = Math.min(cols, cols >= 6 ? sp.wide : cols >= 2 ? sp.mid : 1);
    if (used + want > cols) end();
    out.push(want);
    used += want;
    grow ||= w.type === 'chart' || hasChart(w.blocks);
    if (used === cols) end();
  }
  end();
  return { spans: out, rows };
}

/** A plan's rows as `grid-template-rows`. */
export function rowTemplate(rows: readonly RowFit[]): string {
  return rows.map((r) => (r === 'grow' ? 'minmax(min-content, 1fr)' : 'auto')).join(' ') || 'auto';
}

/** What the Workspace button does: the empty state for no widgets, and tells her only when it reopens one. */
export function buttonAction(board: Board | null): 'empty' | 'reopen' | 'show' {
  if (!board || board.widgets.length === 0) return 'empty';
  return board.open ? 'show' : 'reopen';
}

/** The workspace's title for a board: hers, or a plain word when she gave none. */
export function boardTitle(board: Board | null): string {
  return board?.title.trim() || 'Board';
}

/** A card's foot: where it came from and how long ago ("Calendar · 2 min ago"); empty with no source. */
export function sourceWords(source: unknown, now: Date): string {
  if (typeof source !== 'object' || source === null || Array.isArray(source)) return '';
  const r = source as Record<string, unknown>;
  const name = str(r['name']).trim();
  if (!name) return '';
  const when = age(str(r['at']), now);
  return when ? `${name} · ${when}` : name;
}

/** The connected service a card's source names (its catalogue title, ignoring case), if any. */
export function sourceTile(source: unknown, connected: readonly SourceTile[]): SourceTile | undefined {
  if (typeof source !== 'object' || source === null || Array.isArray(source)) return undefined;
  const name = str((source as Record<string, unknown>)['name']).trim().toLocaleLowerCase();
  if (!name) return undefined;
  return connected.find((t) => t.title.trim().toLocaleLowerCase() === name);
}

/** At most two asks, each her words as given; anything that is not words is left out. */
export function asksOf(asks: unknown): string[] {
  if (!Array.isArray(asks)) return [];
  return (asks as unknown[])
    .filter((a): a is string => typeof a === 'string' && a.trim() !== '')
    .map((a) => a.trim())
    .slice(0, 2);
}

/** How often the times on the board are brought up to date. */
export const TICK_MS = 60_000;

/**
 * A card kept from the last draw is live again: a removal that never came back (refused, or lost
 * with the connection) leaves it as it was, not dimmed with a dead ×.
 */
export function revive(card: Pick<HTMLElement, 'removeAttribute' | 'querySelector'>): void {
  card.removeAttribute('data-leaving');
  const x = card.querySelector<HTMLButtonElement>('.bd-x');
  if (x) x.disabled = false;
}

// ── Drawing ─────────────────────────────────────────────────────────────

export interface BoardOptions {
  /** The user took a widget off. */
  readonly onRemove: (id: string) => void;
  /** The empty state's link to the sample preview. */
  readonly onShowcase: () => void;
  /** The strip of approvals (approvals.ts), pinned above her widgets when there are any. */
  readonly approvals?: HTMLElement;
  /** The user tapped one of a card's asks: send exactly its words as their turn. Without it, no asks are drawn. */
  readonly onAsk?: (text: string) => void;
  /** The catalogue services connected now: a card whose source names one shows its tile. */
  readonly connected?: () => readonly SourceTile[];
  /** The clock; tests set it. */
  readonly now?: () => Date;
}

interface Card {
  readonly el: HTMLElement;
  tick(now: Date): void;
  dispose(): void;
}

const NO_BLOCKS: Omit<DrawnBlocks, 'el'> = { tick: () => {}, dispose: () => {} };

function card(w: Widget, now: Date, opts: BoardOptions, tile: SourceTile | undefined): Card {
  // The type is an attribute, not a class: a class named for it would also be its parts'.
  const el = h('section', 'bd-card');
  el.dataset['type'] = w.type;
  el.dataset['size'] = w.size;
  const name = str(w['title']);
  const head = h('header', 'bd-card-head');
  if (name) {
    head.append(h('h3', 'bd-card-title', name));
    el.setAttribute('aria-label', name);
  } else {
    // No title: the head is only the ×, floated into the corner, so the blocks start at the top.
    el.dataset['untitled'] = '';
  }
  const x = h('button', 'bd-x');
  x.type = 'button';
  x.setAttribute('aria-label', name ? `Take “${name}” off the board` : 'Take this off the board');
  x.title = 'Take off the board';
  x.append(icon('close', 14));
  x.addEventListener('click', () => {
    el.dataset['leaving'] = '';
    x.disabled = true;
    opts.onRemove(w.id);
  });
  head.append(x);
  // A stretched column, so the blocks inside measure the card's own width.
  const body = h('div', 'bd-card-body');
  el.append(head, body);
  let blocks: Omit<DrawnBlocks, 'el'> = NO_BLOCKS;
  try {
    // A widget from before blocks (an old page cache, a server error) has none: its body is empty.
    const raw = w['blocks'];
    const drawn = drawBlocks(Array.isArray(raw) ? (raw as unknown[]) : [], { now, chartHeight: chartHeight(w.size), title: name });
    body.append(drawn.el);
    blocks = drawn;
  } catch (err) {
    // A widget the page cannot draw must not take the rest of the board with it.
    console.warn(`A ${w.type} widget could not be drawn`, err);
    body.replaceChildren(h('p', 'bd-sub', 'This could not be shown.'));
  }

  // The foot: where it came from on the left, her asks on the right; it wraps on a narrow card.
  const source = sourceWords(w['source'], now);
  const asks = opts.onAsk ? asksOf(w['asks']) : [];
  let from: HTMLElement | null = null;
  if (source || asks.length) {
    const foot = h('footer', 'bd-card-foot');
    if (source) {
      const line = h('p', 'bd-source');
      // The connector's own tile before its name: where the numbers came from, at a glance.
      if (tile) line.append(mark(tile.color, tile.glyph, 'xs', tile.id));
      from = h('span', 'bd-source-words', source);
      line.append(from);
      foot.append(line);
    }
    if (asks.length) {
      const list = h('ul', 'bd-asks');
      list.setAttribute('aria-label', 'Ask about this');
      for (const text of asks) {
        const li = h('li');
        const ask = h('button', 'bd-ask', text);
        ask.type = 'button';
        const arrow = h('span', 'bd-ask-arrow', '↗');
        arrow.setAttribute('aria-hidden', 'true');
        ask.append(arrow);
        ask.addEventListener('click', () => opts.onAsk?.(text));
        li.append(ask);
        list.append(li);
      }
      foot.append(list);
    }
    el.append(foot);
  }
  return {
    el,
    tick(at) {
      blocks.tick(at);
      if (from) {
        const words = sourceWords(w['source'], at);
        if (from.textContent !== words) from.textContent = words;
      }
    },
    dispose: () => blocks.dispose(),
  };
}

/** What the approvals strip holds: any card at all, and whether one is waiting for the user. */
export interface Pinned {
  readonly any: boolean;
  readonly waiting: boolean;
  /** Her name, for the label over her widgets while an approval waits. */
  readonly from: string;
  /** An approval waits anywhere, in the conversation too: the cards' asks are hidden. */
  readonly asksOff?: boolean;
}

const NOTHING_PINNED: Pinned = { any: false, waiting: false, from: '' };

/** Each card's span and each row's height, for a wide (6) and a middling (2) workspace, and one column. */
function layoutRows(widgets: readonly Widget[], cards: readonly HTMLElement[], grid: HTMLElement): void {
  const wide = planRows(widgets, 6);
  const mid = planRows(widgets, 2);
  cards.forEach((c, i) => {
    c.style.setProperty('--wide', String(wide.spans[i] ?? 6));
    c.style.setProperty('--mid', String(mid.spans[i] ?? 2));
  });
  grid.style.setProperty('--rows-wide', rowTemplate(wide.rows));
  grid.style.setProperty('--rows-mid', rowTemplate(mid.rows));
  grid.style.setProperty('--rows-one', rowTemplate(planRows(widgets, 1).rows));
}

export interface BoardView {
  readonly el: HTMLElement;
  /**
   * Draw `board` (or the empty state), keeping what has not changed and the scroll position.
   * With approvals pinned, they go above her widgets and the board is not empty without them;
   * while one waits, the cards' asks are hidden.
   */
  draw(board: Board | null, pinned?: Pinned): void;
  /** The board is no longer shown: stop watching card sizes and the clock. The next draw starts afresh. */
  dispose(): void;
}

export function createBoardView(opts: BoardOptions): BoardView {
  const clock = opts.now ?? ((): Date => new Date());
  const el = h('div', 'bd');
  el.tabIndex = -1;
  const grid = h('div', 'bd-grid');
  // Over her widgets while an approval waits: what follows is her context, not what is approved.
  const from = h('h3', 'bd-from');
  let emptyEl: HTMLElement | null = null;
  // By id: what it was drawn from, so an unchanged widget keeps its element (and its hover).
  let drawn = new Map<string, { key: string; card: Card }>();
  // One clock for the whole board: every card's times and ages move on together.
  let timer: ReturnType<typeof setInterval> | null = null;

  function tick(): void {
    const at = clock();
    for (const d of drawn.values()) d.card.tick(at);
  }

  function empty(): HTMLElement {
    const box = h('div', 'bd-empty bd-new');
    const glow = h('div', 'bd-empty-glow');
    glow.setAttribute('aria-hidden', 'true');
    const link = h('button', 'bd-link', 'See a sample board');
    link.type = 'button';
    link.addEventListener('click', () => opts.onShowcase());
    box.append(
      glow,
      h('h3', 'bd-empty-title', 'Ask her to show you something'),
      h('p', 'bd-empty-text', 'Numbers, plans and side-by-side choices appear here when she puts them on the board.'),
      link,
    );
    return box;
  }

  function dispose(): void {
    if (timer !== null) clearInterval(timer);
    timer = null;
    for (const d of drawn.values()) d.card.dispose();
    drawn = new Map();
    grid.replaceChildren();
    emptyEl = null;
  }

  return {
    el,
    dispose,
    draw(board, pinned = NOTHING_PINNED) {
      // Opening afresh: every card arrives. Already open: only new ones do.
      const fresh = !el.isConnected;
      if (fresh) dispose();
      timer ??= setInterval(tick, TICK_MS);
      const now = clock();
      const scroller = el.closest('.workspace-body');
      const top = scroller?.scrollTop ?? 0;
      const active = document.activeElement;
      const focused = active instanceof HTMLElement && el.contains(active) ? active : null;
      const widgets = board?.widgets ?? [];
      const next = new Map<string, { key: string; card: Card }>();
      const cards: HTMLElement[] = [];
      let arriving = 0;
      const connected = opts.connected?.() ?? [];
      for (const w of widgets) {
        const tile = sourceTile(w['source'], connected);
        // A tile that came or went redraws its card, like any other change.
        const key = JSON.stringify(w) + (tile ? `|${tile.id}` : '');
        const old = drawn.get(w.id);
        if (old && old.key === key) {
          old.card.el.classList.remove('bd-new');
          revive(old.card.el);
          next.set(w.id, old);
          cards.push(old.card.el);
          continue;
        }
        const c = card(w, now, opts, tile);
        if (!old) {
          c.el.classList.add('bd-new');
          c.el.style.setProperty('--i', String(arriving++));
        }
        next.set(w.id, { key, card: c });
        cards.push(c.el);
      }
      for (const [id, old] of drawn) if (next.get(id) !== old) old.card.dispose();
      drawn = next;
      // A tap on a widget can't approve: while an approval waits, there is nothing to tap.
      el.classList.toggle('bd-asks-off', pinned.waiting || pinned.asksOff === true);
      layoutRows(widgets, cards, grid);
      if (cards.length) {
        // In place: what went is taken out, and only new, changed or moved cards are put in, so an
        // unchanged card is never detached (its focus and its scroll survive).
        const keep = new Set(cards);
        for (const child of [...grid.children]) if (!keep.has(child as HTMLElement)) child.remove();
        cards.forEach((c, i) => {
          const at = grid.children[i] ?? null;
          if (at !== c) grid.insertBefore(c, at);
        });
      } else {
        grid.replaceChildren();
      }
      const parts: HTMLElement[] = [];
      if (pinned.any && opts.approvals) parts.push(opts.approvals);
      if (cards.length) {
        if (pinned.waiting) {
          from.textContent = `From ${pinned.from}`;
          parts.push(from);
        }
        parts.push(grid);
      } else if (!pinned.any) {
        emptyEl ??= empty();
        parts.push(emptyEl);
      }
      // Shown again later, the empty state arrives afresh.
      if (emptyEl && !parts.includes(emptyEl)) emptyEl = null;
      // In place, so the strip and the grid stay attached (and keep focus) while what is beside them changes.
      const keepParts = new Set(parts);
      for (const child of [...el.children]) if (!keepParts.has(child as HTMLElement)) child.remove();
      parts.forEach((p, i) => {
        const at = el.children[i] ?? null;
        if (at !== p) el.insertBefore(p, at);
      });
      if (scroller) scroller.scrollTop = top;
      const askHidden = el.classList.contains('bd-asks-off') && focused?.closest('.bd-asks');
      if (focused?.isConnected && !askHidden) {
        // A moved card lost focus on the way: it goes back where it was, or to the board if it can't take it.
        if (document.activeElement !== focused) {
          focused.focus({ preventScroll: true });
          if (document.activeElement !== focused) el.focus({ preventScroll: true });
        }
      } else if (focused) {
        // The card that had focus went (its × was pressed), or the ask that had it is hidden: focus stays on the board.
        el.focus({ preventScroll: true });
      }
    },
  };
}
