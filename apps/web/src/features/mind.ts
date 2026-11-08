/** Settings → Her mind (GAL-10): opens the Living Mind, a page of its own, in a new tab. */

import { HER, type Feature } from '../features';
import { addIcon } from '../icons';
import type { Host } from '../panel';
import './mind.css';

// A web of nodes round a centre: her mind.
addIcon('mind', ['M12 9.5a2.5 2.5 0 1 0 0 5a2.5 2.5 0 0 0 0-5z', 'M5 5.5h.01', 'M19 6.5h.01', 'M5.5 18.5h.01', 'M18.5 18h.01', 'M6 6.5l4.2 4', 'M18 7.5l-4.1 3', 'M6.5 17.5l3.8-3.6', 'M17.5 17l-3.6-3']);

const TOKEN_KEY = 'trillion-token';

/** The page's address, carrying the token in the fragment as the main page gets it. Never sent anywhere. */
export function mindUrl(token: string | null): string {
  return token && /^[\w-]+$/.test(token) ? `/mind.html#token=${token}` : '/mind.html';
}

function storedToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** The section: what the page is, and the button that opens it. */
export function drawMindSection(host: Host, open: (url: string) => void, token: () => string | null = storedToken): void {
  const form = el('form', 'settings');
  form.addEventListener('submit', (ev) => ev.preventDefault());
  const title = el('h2', '', 'Her mind');
  title.id = 'mind-section-title';
  host.setAttribute('aria-labelledby', title.id);
  const intro = el(
    'p',
    'settings-note',
    'A 3D map of what makes her who she is: what she remembers, her recent chats, her helpers, what she reads every turn, and the tools she can reach. It lights up as she works. It opens in a new tab.',
  );
  const go = el('button', 'approve mind-open', 'Open her mind');
  go.type = 'button';
  go.addEventListener('click', () => open(mindUrl(token())));
  const actions = el('div', 'settings-actions');
  const close = el('button', 'settings-close', 'Close');
  close.type = 'button';
  close.addEventListener('click', () => host.close());
  actions.append(go, close);
  form.append(title, intro, actions);
  host.replaceChildren(form);
}

export const feature: Feature = {
  setup({ panel }) {
    const host = panel.host('mind');
    panel.add({
      id: 'mind',
      label: 'Her mind',
      group: HER,
      icon: 'mind',
      after: 'memories',
      keywords: 'living mind map brain 3d memories helpers tools graph visual network',
      open: () => {
        drawMindSection(host, (url) => void window.open(url, '_blank', 'noopener'));
        host.showModal();
      },
    });
  },
};
