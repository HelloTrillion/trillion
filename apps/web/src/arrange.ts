/**
 * Arranging a grid of tiles by hand: the pure parts (where a pointer drops, how a list moves)
 * and one small pointer-drag controller. Pointer events cover mouse, pen and touch alike; HTML5
 * drag and drop is not used, since it has no touch support and no placeholder of our own.
 */

export interface Point {
  readonly x: number;
  readonly y: number;
}

export interface Rect {
  readonly left: number;
  readonly top: number;
  readonly width: number;
  readonly height: number;
}

/**
 * The slot a pointer is over: the one containing it, else the nearest in the pointer's row (the
 * empty cells after the last tile belong to it), else the nearest by its centre. Slots are
 * measured once, when the tile is lifted, so a slide in progress never moves the target.
 */
export function slotAt(p: Point, slots: readonly Rect[]): number {
  let best = 0;
  let bestDist = Infinity;
  let inRow = -1;
  let rowDist = Infinity;
  for (let i = 0; i < slots.length; i++) {
    const r = slots[i]!;
    const across = p.y >= r.top && p.y < r.top + r.height;
    if (across && p.x >= r.left && p.x < r.left + r.width) return i;
    const dx = p.x - (r.left + r.width / 2);
    const dy = p.y - (r.top + r.height / 2);
    if (across && Math.abs(dx) < rowDist) {
      rowDist = Math.abs(dx);
      inRow = i;
    }
    const d = dx * dx + dy * dy;
    if (d < bestDist) {
      bestDist = d;
      best = i;
    }
  }
  return inRow >= 0 ? inRow : best;
}

/** The list with one item moved from `from` to `to`; out-of-range indexes are clamped. */
export function moved<T>(list: readonly T[], from: number, to: number): T[] {
  const out = [...list];
  if (from < 0 || from >= out.length) return out;
  const [item] = out.splice(from, 1);
  out.splice(Math.max(0, Math.min(to, out.length)), 0, item!);
  return out;
}

/** An arrow key's step for the keyboard grip: back for left and up, on for right and down. */
export function keyStep(key: string): -1 | 0 | 1 {
  if (key === 'ArrowLeft' || key === 'ArrowUp') return -1;
  if (key === 'ArrowRight' || key === 'ArrowDown') return 1;
  return 0;
}

const shift = (r: Rect, dx: number, dy: number): Rect => ({ left: r.left + dx, top: r.top + dy, width: r.width, height: r.height });

// ── The pointer drag ────────────────────────────────────────────────────

export interface DragHooks {
  /** What holds the tiles; the lifted tile is positioned inside it. */
  readonly container: HTMLElement;
  /** The tiles in order, the one being dragged included. */
  readonly items: () => readonly HTMLElement[];
  /** Wraps a change to the DOM so the tiles slide to their new places. */
  readonly flip: (mutate: () => void) => void;
  /** Whether a drag may start now. */
  readonly may?: () => boolean;
  /** A tile was lifted, from this index; `cancel` puts it back as Escape would. */
  readonly lifted: (from: number, cancel: () => void) => void;
  /** It was put down: at `to`, or back where it was if cancelled. */
  readonly ended: (from: number, to: number, cancelled: boolean) => void;
  /** Scrolls while the pointer is near the edge of what scrolls; by default the nearest scrolling ancestor. */
  readonly scroller?: () => HTMLElement | null;
}

/** How far the pointer moves before a press becomes a drag, so a click stays a click. */
export const DRAG_THRESHOLD = 4;
/** How near the scroller's edge the pointer is when the page starts to scroll. */
const EDGE = 56;

function scrollParent(el: HTMLElement): HTMLElement | null {
  for (let p = el.parentElement; p; p = p.parentElement) {
    const y = getComputedStyle(p).overflowY;
    if ((y === 'auto' || y === 'scroll') && p.scrollHeight > p.clientHeight) return p;
  }
  return null;
}

/**
 * Lets `item` be dragged by `handle` within its container. The lifted tile follows the pointer;
 * a placeholder of its size moves through the others. Escape puts it back.
 */
export function attachDrag(handle: HTMLElement, item: HTMLElement, hooks: DragHooks): void {
  handle.addEventListener('pointerdown', (down: PointerEvent) => {
    if (down.button !== 0 || down.isPrimary === false) return;
    if (hooks.may && !hooks.may()) return;
    // Touch would otherwise scroll the page instead of moving the tile.
    down.preventDefault();
    const id = down.pointerId;
    try {
      handle.setPointerCapture?.(id);
    } catch {
      // A pointer that is already gone: the window listeners below still see it end.
    }
    const start = { x: down.clientX, y: down.clientY };
    let last = start;
    let live: {
      from: number;
      to: number;
      slots: Rect[];
      others: HTMLElement[];
      hold: HTMLElement;
      grab: Point;
      box: Rect;
      at: Rect;
      scroller: HTMLElement | null;
      frame: number;
    } | null = null;

    const lift = (): void => {
      const items = [...hooks.items()];
      const from = items.indexOf(item);
      if (from < 0) return;
      // Measured before anything moves: the slots stay where the tiles are now.
      const slots = items.map((el) => el.getBoundingClientRect() as Rect);
      const box = hooks.container.getBoundingClientRect() as Rect;
      const own = slots[from]!;
      const hold = document.createElement('li');
      hold.className = 'home-placeholder';
      hold.setAttribute('aria-hidden', 'true');
      hold.style.height = `${own.height}px`;
      item.before(hold);
      // Its entrance animation would hold its transform; the pointer has it now.
      item.classList.remove('home-in');
      item.classList.add('home-lifted');
      item.style.width = `${own.width}px`;
      item.style.height = `${own.height}px`;
      live = {
        from,
        to: from,
        slots,
        others: items.filter((el) => el !== item),
        hold,
        grab: { x: start.x - own.left, y: start.y - own.top },
        box,
        at: box,
        scroller: hooks.scroller ? hooks.scroller() : scrollParent(hooks.container),
        frame: 0,
      };
      follow();
      hooks.lifted(from, () => finish(true));
    };

    // The window changed size: the grid reflowed, so the slots are where the tiles stand now.
    const remeasure = (): void => {
      if (!live) return;
      const { hold, others } = live;
      const now = [...hooks.container.children].filter((c): c is HTMLElement => c === hold || others.includes(c as HTMLElement));
      live.slots = now.map((c) => c.getBoundingClientRect() as Rect);
      live.box = hooks.container.getBoundingClientRect() as Rect;
      const own = hold.getBoundingClientRect();
      item.style.width = `${own.width}px`;
      item.style.height = `${own.height}px`;
      follow();
    };

    const follow = (): void => {
      if (!live) return;
      // The container moves when the page scrolls; the slots move with it.
      live.at = hooks.container.getBoundingClientRect() as Rect;
      const x = last.x - live.grab.x - live.at.left;
      const y = last.y - live.grab.y - live.at.top;
      item.style.transform = `translate3d(${x}px, ${y}px, 0) scale(1.025)`;
      const dx = live.at.left - live.box.left;
      const dy = live.at.top - live.box.top;
      const to = slotAt(last, live.slots.map((r) => shift(r, dx, dy)));
      if (to !== live.to) {
        live.to = to;
        const { others, hold } = live;
        hooks.flip(() => {
          const next = others[to];
          if (next) next.before(hold);
          else others[others.length - 1]?.after(hold);
        });
      }
    };

    // Near the top or bottom of what scrolls, it scrolls, so a tile can go anywhere on a phone.
    const edgeScroll = (): void => {
      if (!live) return;
      live.frame = 0;
      const sc = live.scroller;
      if (!sc) return;
      const r = sc.getBoundingClientRect();
      const step = last.y < r.top + EDGE ? -Math.ceil((r.top + EDGE - last.y) / 6) : last.y > r.bottom - EDGE ? Math.ceil((last.y - (r.bottom - EDGE)) / 6) : 0;
      if (!step) return;
      const before = sc.scrollTop;
      sc.scrollTop += step;
      if (sc.scrollTop !== before) follow();
      live.frame = requestAnimationFrame(edgeScroll);
    };

    const move = (e: PointerEvent): void => {
      if (e.pointerId !== id) return;
      last = { x: e.clientX, y: e.clientY };
      if (!live) {
        if (Math.hypot(last.x - start.x, last.y - start.y) < DRAG_THRESHOLD) return;
        lift();
        if (!live) return;
      }
      e.preventDefault();
      follow();
      const l = live as { frame: number } | null;
      if (l && !l.frame && typeof requestAnimationFrame === 'function') l.frame = requestAnimationFrame(edgeScroll);
    };

    const finish = (cancelled: boolean): void => {
      removeEventListener('pointermove', move);
      removeEventListener('pointerup', up);
      removeEventListener('pointercancel', cancel);
      removeEventListener('keydown', key, true);
      removeEventListener('resize', remeasure);
      try {
        handle.releasePointerCapture?.(id);
      } catch {
        // Already released.
      }
      const l = live;
      live = null;
      if (!l) return;
      if (l.frame) cancelAnimationFrame(l.frame);
      const to = cancelled ? l.from : l.to;
      hooks.flip(() => {
        // Back to its place, or into the placeholder's.
        const next = l.others[to];
        if (next) next.before(l.hold);
        else l.others[l.others.length - 1]?.after(l.hold);
        l.hold.replaceWith(item);
        item.classList.remove('home-lifted');
        item.style.removeProperty('width');
        item.style.removeProperty('height');
        item.style.removeProperty('transform');
      });
      hooks.ended(l.from, to, cancelled);
    };

    const up = (e: PointerEvent): void => {
      if (e.pointerId === id) finish(false);
    };
    const cancel = (e: PointerEvent): void => {
      if (e.pointerId === id) finish(true);
    };
    // Escape puts it back, and is spent here: the workspace behind it stays open.
    const key = (e: KeyboardEvent): void => {
      if (e.key !== 'Escape' || !live) return;
      e.preventDefault();
      e.stopPropagation();
      finish(true);
    };

    addEventListener('pointermove', move);
    addEventListener('pointerup', up);
    addEventListener('pointercancel', cancel);
    addEventListener('keydown', key, true);
    addEventListener('resize', remeasure);
  });
}
