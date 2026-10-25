/**
 * The workspace (SUR-1): one thing at a time opens over the stage, and the orb docks
 * small while it is open. It stays the orb: every state and reaction, just smaller.
 */
import { type StageRect } from '@hellotrillion/galaxy';
import { isPhone, orbRect } from './layout';

export interface WorkspaceDeps {
  readonly region: HTMLElement;
  readonly title: HTMLElement;
  readonly body: HTMLElement;
  readonly close: HTMLButtonElement;
  readonly dock: HTMLButtonElement;
  /** The sidebar's current width in CSS px, 0 when hidden. */
  readonly sidebarWidth: () => number;
  readonly place: (rect: StageRect, animate: boolean) => void;
  /** Where focus goes on close when where it came from can no longer take it. */
  readonly fallback: () => HTMLElement | null;
}

export interface Workspace {
  /**
   * Show `content` under `title`, in place of whatever was open; `id` names what it is.
   * Opening what is already shown only changes the title.
   */
  open(title: string, content: HTMLElement, id?: string): void;
  close(): void;
  readonly isOpen: boolean;
  /** The id of what is shown, or null. */
  readonly showing: string | null;
  /** Re-place the orb: the window, the sidebar or the workspace changed. */
  layout(animate?: boolean): void;
  /** Called with the id of what closed, on close or when something else replaces it. */
  onClose(listener: (id: string | null) => void): void;
}

/** What is shown and who hears it close, without the DOM. */
export interface Showing {
  readonly isOpen: boolean;
  readonly id: string | null;
  /** Show `id`. Whatever was shown closes first, exactly as `close` would. */
  show(id: string | null): void;
  /** Forget what is shown, then tell the listeners its id. False when nothing was shown. */
  close(): boolean;
  onClose(listener: (id: string | null) => void): void;
}

export function createShowing(): Showing {
  const listeners: ((id: string | null) => void)[] = [];
  let isOpen = false;
  let id: string | null = null;
  function close(): boolean {
    if (!isOpen) return false;
    const was = id;
    isOpen = false;
    id = null;
    for (const l of listeners) l(was);
    return true;
  }
  return {
    get isOpen() {
      return isOpen;
    },
    get id() {
      return id;
    },
    show(next) {
      close();
      isOpen = true;
      id = next;
    },
    close,
    onClose(listener) {
      listeners.push(listener);
    },
  };
}

export function createWorkspace(d: WorkspaceDeps): Workspace {
  const state = createShowing();
  // Where focus was before it opened, to hand back on close.
  let back: HTMLElement | null = null;

  function layout(animate = true): void {
    const w = innerWidth;
    d.place(
      orbRect({ w, h: innerHeight, sidebar: d.sidebarWidth(), workspace: !d.region.hidden, phone: isPhone(w) }),
      animate,
    );
  }

  function close(): void {
    if (!state.isOpen) return;
    d.region.hidden = true;
    d.dock.hidden = true;
    d.body.replaceChildren();
    layout();
    state.close();
    // Focus came from somewhere now gone or inert (a hidden sidebar): go to the fallback.
    const usable = back !== null && back.isConnected && back.closest('[inert], [hidden]') === null;
    (usable ? back : back && d.fallback())?.focus();
    back = null;
  }

  d.close.addEventListener('click', close);
  d.dock.addEventListener('click', close);
  // A window being resized is followed exactly; gliding would lag behind the edge.
  addEventListener('resize', () => layout(false));

  return {
    open(title, content, id) {
      d.title.textContent = title;
      // Already shown: moving it would lose focus and scroll.
      if (state.isOpen && d.body.firstElementChild === content) return;
      if (!state.isOpen) back = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      // Something else was open: it is taken away and closes first, as on a real close.
      else d.body.replaceChildren();
      state.show(id ?? null);
      d.body.replaceChildren(content);
      d.region.hidden = false;
      d.dock.hidden = false;
      layout();
    },
    close,
    get isOpen() {
      return state.isOpen;
    },
    get showing() {
      return state.id;
    },
    layout,
    onClose: (listener) => state.onClose(listener),
  };
}
