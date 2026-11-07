/** Settings → Celebrations, and the Supernova itself (GAL-9): the one place the page
 * asks the galaxy for one, from the attention engine's notice. */

import { YOU, type Feature } from '../features';
import { addIcon } from '../icons';
import { Celebrations, createCelebrationsSettings, savedCelebrations } from '../supernova';

// A star bursting: four rays and four short ones.
addIcon('celebrations', ['M12 3v5', 'M12 16v5', 'M3 12h5', 'M16 12h5', 'M6.5 6.5l2.5 2.5', 'M15 15l2.5 2.5', 'M17.5 6.5L15 9', 'M9 15l-2.5 2.5']);

export const feature: Feature = {
  setup({ panel, refresh, on, galaxy, replying }) {
    const settings = createCelebrationsSettings(panel.host('celebrations'), refresh);
    panel.add({
      id: 'celebrations',
      label: 'Celebrations',
      group: YOU,
      icon: 'celebrations',
      after: 'appearance',
      keywords: 'celebrate celebration supernova burst confetti win milestone animation',
      summary: () => settings.summary(),
      open: () => settings.open(),
    });
    if (!galaxy) return;
    const live = replying ?? (() => false);
    const celebrations = new Celebrations((tier) => galaxy.supernova(tier), savedCelebrations);
    // Messages reach features before the page has taken them in: look once it has, so
    // `live` is the page's state after this message (a reply that just ended is over).
    on((msg) => setTimeout(() => celebrations.take(msg, live()), 0));
  },
};
