/**
 * A card's full detail (SPN-2a): everything the action sends or writes, exactly as it will go,
 * under the one-line summary. Every recipient, the subject and the whole text, line breaks kept.
 * It scrolls inside the card and is never cut: a tap or a yes approves what this shows. The host
 * composed it from the frozen arguments; it is set with textContent, never parsed as HTML.
 */
import './card-detail.css';

/** The detail as a block for a card, or null when there is none. `cls` is the card's own look. */
export function cardDetail(text: string, cls = 'card-detail'): HTMLElement | null {
  if (!text) return null;
  const box = document.createElement('div');
  box.className = cls;
  box.setAttribute('role', 'region');
  box.setAttribute('aria-label', 'Everything it will send or write');
  // Reachable by keyboard, so a long one can be scrolled without a pointer.
  box.tabIndex = 0;
  const pre = document.createElement('pre');
  pre.textContent = text;
  box.append(pre);
  return box;
}
