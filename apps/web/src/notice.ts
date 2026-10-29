/**
 * A notice through the door, as the conversation shows it (the reminders and speaking
 * up specs): her line, or its title when she has none; a Join button for a link; the
 * host's short detail; and "Held: <reason>" when she kept her voice.
 *
 * Titles, details and reasons can carry someone else's words (an invite title, an
 * email subject): every one is set as text, never as markup.
 */

import { inApp, openExternal } from './desktop';
import { dueLabel, quietLine, type Entry } from './view';

export type Scheduled = Extract<Entry, { kind: 'scheduled' }>;

/** How a link leaves the page: the user's browser from the app, a new tab from a tab. */
export interface Opener {
  inApp(): boolean;
  external(url: string): void;
  tab(url: string): void;
}

export const OPENER: Opener = {
  inApp,
  external: (url) => void openExternal(url),
  // The opened page gets no handle on this one, and no address it came from.
  tab: (url) => void window.open(url, '_blank', 'noopener,noreferrer'),
};

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** The link's host, beside its button: where a click goes is seen before it is made. */
export function linkHost(link: string): string {
  try {
    return new URL(link).host;
  } catch {
    return '';
  }
}

export function heldText(reason: string): string {
  return `Held: ${reason}`;
}

/** What goes under the line: Join, the detail, and why she held it. None when it simply spoke. */
export function extras(e: Pick<Scheduled, 'link' | 'detail' | 'quiet'>, open: Opener = OPENER): HTMLElement[] {
  const out: HTMLElement[] = [];
  const host = e.link ? linkHost(e.link) : '';
  if (e.link && host) {
    const link = e.link;
    const row = el('div', 'notice-join');
    const join = el('button', 'notice-join-button', 'Join');
    join.type = 'button';
    join.setAttribute('aria-label', `Join at ${host}`);
    join.addEventListener('click', () => (open.inApp() ? open.external(link) : open.tab(link)));
    row.append(join, el('span', 'notice-host', host));
    out.push(row);
  }
  if (e.detail) out.push(el('small', 'notice-detail', e.detail));
  // Not spoken and no reason means it was past its moment: nothing more to say.
  if (e.quiet) out.push(el('small', 'notice-held', heldText(e.quiet)));
  return out;
}

/** A reminder, timed ask or heads-up (a routine's ready line is drawn by `readyLine`). */
export function renderScheduled(e: Scheduled, words: (text: string) => HTMLElement[], open: Opener = OPENER): HTMLElement {
  const more = extras(e, open);
  if (e.text === null) {
    // No words of hers: missed or failed says so, anything else is shown by its title.
    const line = el('p', 'notice scheduled-quiet', e.what === 'missed' || e.what === 'failed' ? quietLine(e) : e.title);
    if (!more.length) return line;
    const box = el('div', 'notice-block');
    box.append(line, ...more);
    return box;
  }
  const box = el('div', 'msg assistant');
  box.append(...words(e.text));
  // Late: when it was due, so "now" in her words is not taken for now.
  if (e.late) box.append(el('span', 'mark', dueLabel(e.due)));
  box.append(...more);
  return box;
}
