/**
 * A routine's ready line (AGT-3, SUR-9): "Your Monday revenue is ready", with an Open
 * button that goes to the conversation it built. One quiet line, never a bubble of hers.
 * The title is the routine's name in the host's words: only ever set as text.
 */

export function readyLine(title: string, conversationId: string | null, open: (id: string) => void): HTMLElement {
  const line = document.createElement('p');
  line.className = 'notice ready-line';
  const words = document.createElement('span');
  words.textContent = title;
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
