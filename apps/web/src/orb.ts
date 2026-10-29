/**
 * The floating orb: the desktop app's small always-on-top window, shown while she is
 * talked to from another app (D5). It draws what the main page reports through the
 * shell (`orb` events): the orb's state and one caption line. Clicking it brings the
 * main window forward.
 */
import '@hellotrillion/galaxy/tokens.css';
import { tail } from './captions';
import { onOrb, showMain } from './desktop';
import { savedTheme, shownTheme } from './prefs';

export function createOrb(root: HTMLElement): () => void {
  root.dataset['state'] = 'idle';
  const ball = document.createElement('span');
  ball.className = 'float-orb';
  ball.setAttribute('aria-hidden', 'true');
  const caption = document.createElement('span');
  caption.className = 'float-caption';
  caption.hidden = true;
  root.replaceChildren(ball, caption);
  root.addEventListener('click', () => showMain());
  return onOrb(({ state, caption: text }) => {
    root.dataset['state'] = state;
    root.setAttribute('aria-label', text ? `Trillion: ${text}` : 'Open Trillion');
    caption.textContent = tail(text, 90);
    caption.hidden = !text;
  });
}

const root = typeof document !== 'undefined' ? document.getElementById('float') : null;
if (root) {
  // The main page's theme, kept in this origin's storage.
  document.documentElement.dataset['theme'] = shownTheme(savedTheme(), matchMedia('(prefers-color-scheme: light)').matches);
  createOrb(root);
}
