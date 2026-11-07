/** Settings → Devices (SEC-11): each device that signed in, with Forget. */

import { createDevicesSettings } from '../devices';
import { COMPUTER, type Feature } from '../features';
import { addIcon } from '../icons';
import './devices.css';

// A screen on a stand: the devices that signed in.
addIcon('devices', ['M3.5 5.5h17v11h-17z', 'M9 20h6', 'M12 16.5V20']);

export const feature: Feature = {
  setup({ panel, api }) {
    const devices = createDevicesSettings(panel.host('devices'), api);
    panel.add({
      id: 'devices',
      label: 'Devices',
      group: COMPUTER,
      icon: 'devices',
      after: 'folders',
      keywords: 'security sign in browser device forget new login',
      summary: () => devices.summary(),
      open: () => devices.open(),
    });
  },
};
