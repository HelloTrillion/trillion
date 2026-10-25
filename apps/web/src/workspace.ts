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
}

export interface Workspace {
  /** Show `content` under `title`, in place of whatever was open. */
  open(title: string, content: HTMLElement): void;
  close(): void;
  readonly isOpen: boolean;
  /** Re-place the orb: the window, the sidebar or the workspace changed. */
  layout(animate?: boolean): void;
  onClose(listener: () => void): void;
}

export function createWorkspace(d: WorkspaceDeps): Workspace {
  const listeners: (() => void)[] = [];
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
    if (d.region.hidden) return;
    d.region.hidden = true;
    d.dock.hidden = true;
    d.body.replaceChildren();
    layout();
    for (const l of listeners) l();
    back?.focus();
    back = null;
  }

  d.close.addEventListener('click', close);
  d.dock.addEventListener('click', close);
  // A window being resized is followed exactly; gliding would lag behind the edge.
  addEventListener('resize', () => layout(false));

  return {
    open(title, content) {
      if (d.region.hidden) back = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      d.title.textContent = title;
      d.body.replaceChildren(content);
      d.region.hidden = false;
      d.dock.hidden = false;
      layout();
    },
    close,
    get isOpen() {
      return !d.region.hidden;
    },
    layout,
    onClose(listener) {
      listeners.push(listener);
    },
  };
}
