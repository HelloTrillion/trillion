/**
 * Captions under the orb while the sidebar is hidden: the transcript is out of sight,
 * so what she says, or what she hears, shows here for a moment. Screen readers get the
 * same words from the #announce live region, so this stays aria-hidden.
 */
import { orbRect, type Frame } from './layout';
import type { View } from './view';

/** The caption lines for the current view: what she is saying, or what she hears. */
export function captionOf(view: Pick<View, 'entries' | 'hearing' | 'replyOpen' | 'orb'>): string {
  if (view.hearing) return view.hearing;
  const last = view.entries[view.entries.length - 1];
  if (last?.kind === 'assistant' && (view.replyOpen || view.orb === 'speaking')) return last.text;
  return '';
}

/** The tail of a long line, so two lines show what is being said now. */
export const tail = (text: string, max = 160): string =>
  text.length <= max ? text : `…${text.slice(text.length - max + 1).replace(/^\S*\s/, '')}`;

export const CAPTION_MS = 4000;

/**
 * Where the captions sit: centred on the orb's resting place, between its glow and its outer
 * rings. The ring extent is nearly the stage's height, so its edge is no place to measure from.
 */
export function captionSpot(f: Frame): { x: number; top: number; width: number } {
  const orb = orbRect({ ...f, workspace: false });
  const left = f.phone ? 0 : f.sidebar;
  const width = Math.max(120, Math.min(640, f.w - left - 48));
  return { x: orb.x, top: Math.min(orb.y + orb.size * 0.4, f.h - 72), width };
}

export interface Captions {
  update(text: string, sidebarHidden: boolean): void;
  place(frame: Frame): void;
}

export function createCaptions(el: HTMLElement): Captions {
  let last = '';
  let timer: ReturnType<typeof setTimeout> | undefined;
  return {
    update(text, sidebarHidden) {
      if (!sidebarHidden || !text) {
        clearTimeout(timer);
        last = '';
        el.hidden = true;
        return;
      }
      // Only a change shows it again and restarts the 4 s; the same text is left to fade.
      if (text === last) return;
      last = text;
      el.textContent = tail(text);
      el.hidden = false;
      clearTimeout(timer);
      timer = setTimeout(() => {
        el.hidden = true;
      }, CAPTION_MS);
    },
    place(frame) {
      const s = captionSpot(frame);
      el.style.setProperty('--cap-x', `${s.x}px`);
      el.style.setProperty('--cap-top', `${s.top}px`);
      el.style.setProperty('--cap-w', `${s.width}px`);
    },
  };
}
