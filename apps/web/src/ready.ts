/**
 * A routine's ready line (AGT-3, SUR-9): "Your Monday revenue is ready", with an Open
 * button that goes to the conversation it built. One quiet line, never a bubble of hers.
 * The title is the routine's name in the host's words: only ever set as text.
 */

import { type From, fromTag } from './notice';

export function readyLine(
  title: string,
  conversationId: string | null,
  open: (id: string) => void,
  from?: From | null,
  show?: (helperId: string) => void,
): HTMLElement {
  const line = document.createElement('p');
  line.className = 'notice ready-line';
  const words = document.createElement('span');
  if (from && show) {
    // Its helper's name first, as the Helpers panel says it, and a tap there opens it.
    // "Your Morning brief is ready" after "Morning brief" says only "ready".
    const plain = title.replace(/\.$/, '');
    words.append(fromTag(from, show), `: ${plain === `Your ${from.name} is ready` ? 'ready' : plain}`);
  } else words.textContent = title;
  line.append(words);
  // Without a conversation to go to, the line alone still says it is ready.
  if (conversationId) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'ready-open';
    button.textContent = 'Open';
    button.setAttribute('aria-label', `Open: ${title}`);
    button.addEventListener('click', () => open(conversationId));
    line.append(button);
  }
  return line;
}
