/** Settings → Memories (MEM-6), with the notes-from-chats switch at its top (MEM-4). */

import { createNotesSwitch } from '../browser';
import { HER, type Feature } from '../features';
import { addIcon } from '../icons';
import { createMemories } from '../memories';
import './memories.css';

// A bookmark: what she kept from your conversations.
addIcon('memories', ['M6.5 3.5h11v17l-5.5-4l-5.5 4z', 'M9.5 8h5']);

export const feature: Feature = {
  setup({ panel, api, send, refresh, on }) {
    const notes = createNotesSwitch(api, refresh);
    const memories = createMemories(panel.host('memories'), api, send, refresh, notes);
    on((msg) => memories.handle(msg));
    panel.add({
      id: 'memories',
      label: 'Memories',
      group: HER,
      icon: 'memories',
      after: 'skills',
      keywords: 'memory remember remembered forget facts about me edit correct notes chats learn privacy',
      summary: () => memories.summary(),
      open: () => memories.open(),
    });
  },
};
