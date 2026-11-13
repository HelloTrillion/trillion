/** Settings → Confirmations (SUR-7): the decision record, read-only. */

import { createConfirmations } from '../confirmations';
import { YOU, type Feature } from '../features';
import { addIcon } from '../icons';
import { asksLoader } from '../seamless';
import './confirmations.css';

// A ticked list: what asked for your yes, and what came of it.
addIcon('confirmations', ['M4.5 7l1.5 1.5L9 5.5', 'M4.5 13l1.5 1.5L9 11.5', 'M12 7h7.5M12 13h7.5M4.5 18.5h15']);

export const feature: Feature = {
  setup({ panel, api, refresh, openConversation }) {
    const confirmations = createConfirmations(panel.host('confirmations'), api, refresh, openConversation, asksLoader(api));
    panel.add({
      id: 'confirmations',
      label: 'Confirmations',
      group: YOU,
      icon: 'confirmations',
      after: 'reminders',
      keywords: 'approvals approved approve decisions record history audit cards yes declined waiting unknown did that reach me',
      summary: () => confirmations.summary(),
      open: () => confirmations.open(),
    });
  },
};
