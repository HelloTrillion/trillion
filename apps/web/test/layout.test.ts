import { defaultRect } from '@hellotrillion/galaxy';
import { describe, expect, it } from 'vitest';
import { DOCK, PHONE_MAX, SIDEBAR, clampWidth, isPhone, orbRect } from '../src/layout';

describe('orbRect', () => {
  it('centres the orb right of the sidebar, sized as it is today', () => {
    const r = orbRect({ w: 1400, h: 900, sidebar: 360, workspace: false, phone: false });
    expect(r).toEqual({ x: 360 + 1040 / 2, y: 450, size: defaultRect(1040, 900).size });
  });

  it('uses the whole window when the sidebar is hidden', () => {
    expect(orbRect({ w: 1400, h: 900, sidebar: 0, workspace: false, phone: false })).toEqual(defaultRect(1400, 900));
  });

  it('docks bottom-right while the workspace is open', () => {
    expect(orbRect({ w: 1400, h: 900, sidebar: 360, workspace: true, phone: false }))
      .toEqual({ x: 1400 - DOCK.inset - DOCK.size / 2, y: 900 - DOCK.inset - DOCK.size / 2, size: DOCK.size });
  });

  it('on a phone, docks top centre and otherwise uses the full window', () => {
    expect(orbRect({ w: 390, h: 844, sidebar: 0, workspace: true, phone: true }))
      .toEqual({ x: 195, y: DOCK.phoneInset + DOCK.phoneSize / 2, size: DOCK.phoneSize });
    expect(orbRect({ w: 390, h: 844, sidebar: 0, workspace: false, phone: true })).toEqual(defaultRect(390, 844));
  });

  it('ignores the sidebar width on a phone', () => {
    expect(orbRect({ w: 390, h: 844, sidebar: 360, workspace: false, phone: true })).toEqual(defaultRect(390, 844));
  });

  it('knows a phone', () => {
    expect(isPhone(PHONE_MAX - 1)).toBe(true);
    expect(isPhone(PHONE_MAX)).toBe(false);
  });
});

describe('clampWidth', () => {
  it('keeps the sidebar between its limits and whole', () => {
    expect(clampWidth(100)).toBe(SIDEBAR.min);
    expect(clampWidth(900)).toBe(SIDEBAR.max);
    expect(clampWidth(400.6)).toBe(401);
  });
});
