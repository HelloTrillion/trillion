/**
 * Her board, drawn in the workspace: one glass card per widget from the kit, laid out in a grid
 * that follows the workspace's width. Everything she put there may quote a page she read
 * (SPN-5), so every piece of text is set with textContent and nothing is ever parsed as HTML.
 */
import {
  type Dispose,
  cartesian,
  chartSpecOf,
  donut,
  drawNote,
  drawTable,
  drawTimeline,
  h,
  nothing,
  records,
  str,
} from './blocks';
import { icon } from './icons';
import { type Board, type Widget, type WidgetSize } from './view';

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

/**
 * Widgets laid out in rows of `columns`, in her order. A widget that does not fit what is left
 * of a row starts the next one, and the last widget of a row widens to fill it, so a row never
 * has a hole and a widget alone takes the whole width.
 */
export function planRows(widgets: readonly { readonly size: WidgetSize; readonly type: string }[], columns: number): RowPlan {
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
    grow ||= w.type === 'chart';
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

// Pure helpers that moved into blocks.ts with the drawing they serve, still reachable from here.
export {
  type NoteBlock,
  type PlacedEvent,
  type Run,
  type TimelineLayout,
  chartSpecOf,
  chartSummary,
  layoutTimeline,
  minutesOf,
  nearestIndex,
  parseNote,
  pointWords,
  runs,
} from './blocks';

export type Tone = 'up' | 'down' | 'neutral';

/** A stat's tone: the one she gave, else read from the sign of its delta. */
export function toneOf(tone: unknown, delta: string): Tone {
  if (tone === 'up' || tone === 'down' || tone === 'neutral') return tone;
  const d = delta.trim();
  if (d.startsWith('+') || d.startsWith('▲')) return 'up';
  if (d.startsWith('-') || d.startsWith('−') || d.startsWith('▼')) return 'down';
  return 'neutral';
}



// Stat ──

function stat(w: Widget, body: HTMLElement): Dispose {
  const value = h('p', 'bd-stat-value', str(w['value']));
  body.append(value);
  const delta = str(w['delta']);
  const hint = str(w['hint']);
  if (delta || hint) {
    const foot = h('p', 'bd-stat-foot');
    if (delta) {
      const chip = h('span', 'bd-delta', delta);
      chip.dataset['tone'] = toneOf(w['tone'], delta);
      foot.append(chip);
    }
    if (hint) foot.append(h('span', 'bd-hint', hint));
    body.append(foot);
  }
  return nothing;
}

// Chart ──


function chart(w: Widget, body: HTMLElement): Dispose {
  const spec = chartSpecOf(w);
  const title = str(w['title']);
  return spec.kind === 'donut' ? donut(spec, title, body) : cartesian(spec, title, chartHeight(w.size), body);
}


// List ──

function list(w: Widget, body: HTMLElement): Dispose {
  const ul = h('ul', 'bd-list');
  for (const it of records(w['items'])) {
    const li = h('li', 'bd-item');
    const done = it['done'] === true;
    if (done) li.dataset['done'] = '';
    const mark = h('span', 'bd-mark');
    mark.setAttribute('aria-hidden', 'true');
    if (done) mark.append(icon('check', 12));
    const text = h('div', 'bd-item-text');
    const t = h('strong', '', str(it['title']));
    text.append(t);
    if (done) t.prepend(h('span', 'visually-hidden', 'Done: '));
    const sub = str(it['subtitle']);
    if (sub) text.append(h('span', 'bd-sub', sub));
    li.append(mark, text);
    const meta = str(it['meta']);
    if (meta) li.append(h('span', 'bd-meta', meta));
    ul.append(li);
  }
  body.append(ul);
  return nothing;
}

// Table ──

function table(w: Widget, body: HTMLElement): Dispose {
  body.append(drawTable(w['columns'], w['rows']));
  return nothing;
}

// Note ──

function note(w: Widget, body: HTMLElement): Dispose {
  body.append(drawNote(str(w['text'])));
  return nothing;
}

// Timeline ──

function timeline(w: Widget, body: HTMLElement): Dispose {
  drawTimeline(w['events'], body);
  return nothing;
}

// Compare ──

function compare(w: Widget, body: HTMLElement): Dispose {
  const pick = str(w['pick']);
  const grid = h('div', 'bd-options');
  for (const o of records(w['options'])) {
    const name = str(o['name']);
    const card = h('article', 'bd-option');
    const top = h('div', 'bd-option-top');
    top.append(h('h4', 'bd-option-name', name));
    if (pick && name === pick) {
      card.dataset['pick'] = '';
      top.append(h('span', 'bd-pick', 'Her pick'));
    }
    card.append(top);
    const metrics = records(o['metrics']);
    if (metrics.length) {
      const dl = h('dl', 'bd-bars');
      for (const m of metrics) {
        const score = typeof m['score'] === 'number' && Number.isFinite(m['score']) ? Math.min(10, Math.max(0, m['score'])) : 0;
        const dd = h('dd');
        dd.title = `${score} of 10`;
        const fill = h('i');
        fill.style.setProperty('--v', String(score / 10));
        dd.append(fill, h('span', 'visually-hidden', `${score} of 10`));
        dl.append(h('dt', '', str(m['label'])), dd);
      }
      card.append(dl);
    }
    const tags = Array.isArray(o['tags']) ? (o['tags'] as unknown[]).map(str).filter(Boolean) : [];
    if (tags.length) {
      const row = h('div', 'bd-tags');
      for (const t of tags) row.append(h('span', 'bd-tag', t));
      card.append(row);
    }
    const n = str(o['note']);
    if (n) card.append(h('p', 'bd-sub', n));
    grid.append(card);
  }
  body.append(grid);
  const reason = str(w['reason']);
  if (reason) body.append(h('p', 'bd-reason', reason));
  return nothing;
}

const DRAW: Readonly<Record<string, (w: Widget, body: HTMLElement) => Dispose>> = { stat, chart, list, table, note, timeline, compare };

/** The name a card goes by: its title, or for a stat its label. */
function nameOf(w: Widget): string {
  return str(w['title']) || (w.type === 'stat' ? str(w['label']) : '');
}

function card(w: Widget, onRemove: (id: string) => void): { el: HTMLElement; dispose: Dispose } {
  // The type is an attribute, not a class: a class named for it (bd-list, bd-table) would also be its parts'.
  const el = h('section', 'bd-card');
  el.dataset['type'] = w.type;
  el.dataset['size'] = w.size;
  const name = nameOf(w);
  const head = h('header', 'bd-card-head');
  if (name) {
    head.append(h('h3', 'bd-card-title', name));
    el.setAttribute('aria-label', name);
  }
  const x = h('button', 'bd-x');
  x.type = 'button';
  x.setAttribute('aria-label', name ? `Take “${name}” off the board` : 'Take this off the board');
  x.title = 'Take off the board';
  x.append(icon('close', 14));
  x.addEventListener('click', () => {
    el.dataset['leaving'] = '';
    x.disabled = true;
    onRemove(w.id);
  });
  head.append(x);
  const body = h('div', 'bd-card-body');
  el.append(head, body);
  let dispose = nothing;
  try {
    dispose = (DRAW[w.type] ?? (() => nothing))(w, body);
  } catch {
    // A widget the page cannot draw must not take the rest of the board with it.
    body.replaceChildren(h('p', 'bd-sub', 'This could not be shown.'));
  }
  return { el, dispose };
}

export interface BoardOptions {
  /** The user took a widget off. */
  readonly onRemove: (id: string) => void;
  /** The empty state's link to the sample preview. */
  readonly onShowcase: () => void;
  /** The strip of approvals (approvals.ts), pinned above her widgets when there are any. */
  readonly approvals?: HTMLElement;
}

/** What the approvals strip holds: any card at all, and whether one is waiting for the user. */
export interface Pinned {
  readonly any: boolean;
  readonly waiting: boolean;
  /** Her name, for the label over her widgets while an approval waits. */
  readonly from: string;
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
   * With approvals pinned, they go above her widgets and the board is not empty without them.
   */
  draw(board: Board | null, pinned?: Pinned): void;
  /** The board is no longer shown: stop watching card sizes. The next draw starts afresh. */
  dispose(): void;
}

/**
 * A card kept from the last draw is live again: a removal that never came back (refused, or lost
 * with the connection) leaves it as it was, not dimmed with a dead ×.
 */
export function revive(card: Pick<HTMLElement, 'removeAttribute' | 'querySelector'>): void {
  card.removeAttribute('data-leaving');
  const x = card.querySelector<HTMLButtonElement>('.bd-x');
  if (x) x.disabled = false;
}

export function createBoardView(opts: BoardOptions): BoardView {
  const el = h('div', 'bd');
  el.tabIndex = -1;
  const grid = h('div', 'bd-grid');
  // Over her widgets while an approval waits: what follows is her context, not what is approved.
  const from = h('h3', 'bd-from');
  let emptyEl: HTMLElement | null = null;
  // By id: what it was drawn from, so an unchanged widget keeps its element (and its hover).
  let drawn = new Map<string, { key: string; el: HTMLElement; dispose: Dispose }>();

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
    for (const d of drawn.values()) d.dispose();
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
      const scroller = el.closest('.workspace-body');
      const top = scroller?.scrollTop ?? 0;
      const active = document.activeElement;
      const focused = active instanceof HTMLElement && el.contains(active) ? active : null;
      const widgets = board?.widgets ?? [];
      const next = new Map<string, { key: string; el: HTMLElement; dispose: Dispose }>();
      const cards: HTMLElement[] = [];
      let arriving = 0;
      for (const w of widgets) {
        const key = JSON.stringify(w);
        const old = drawn.get(w.id);
        if (old && old.key === key) {
          old.el.classList.remove('bd-new');
          revive(old.el);
          next.set(w.id, old);
          cards.push(old.el);
          continue;
        }
        const c = card(w, opts.onRemove);
        if (!old) {
          c.el.classList.add('bd-new');
          c.el.style.setProperty('--i', String(arriving++));
        }
        next.set(w.id, { key, ...c });
        cards.push(c.el);
      }
      for (const [id, old] of drawn) if (next.get(id) !== old) old.dispose();
      drawn = next;
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
      if (focused?.isConnected) {
        // A moved card lost focus on the way: it goes back where it was.
        if (document.activeElement !== focused) focused.focus({ preventScroll: true });
      } else if (focused) {
        // The card that had focus went (its × was pressed): focus stays on the board.
        el.focus({ preventScroll: true });
      }
    },
  };
}
