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
  const lines = document.createElement('span');
  lines.className = 'float-lines';
  const words = document.createElement('span');
  lines.append(words);
  caption.append(lines);
  root.replaceChildren(ball, caption);
  root.addEventListener('click', () => showMain());
  return onOrb(({ state, caption: text }) => {
    root.dataset['state'] = state;
    root.setAttribute('aria-label', text ? `Trillion: ${text}` : 'Open Trillion');
    // Bounded here too; the CSS keeps the last two lines in sight.
    words.textContent = tail(text, 90);
    caption.hidden = !text;
  });
}

const root = typeof document !== 'undefined' ? document.getElementById('float') : null;
if (root) {
  // The main page's theme, kept in this origin's storage; it follows a change there, or the computer's.
  const light = matchMedia('(prefers-color-scheme: light)');
  const theme = (): void => {
    document.documentElement.dataset['theme'] = shownTheme(savedTheme(), light.matches);
  };
  theme();
  light.addEventListener('change', theme);
  addEventListener('storage', theme);
  createOrb(root);
}
