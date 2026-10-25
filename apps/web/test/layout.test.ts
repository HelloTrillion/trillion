import { defaultRect } from '@hellotrillion/galaxy';
import { describe, expect, it } from 'vitest';
import { DOCK, PHONE_MAX, SHEET, SIDEBAR, clampWidth, dragTop, isPhone, orbRect, settle, sheetTop, stepRest, tapped } from '../src/layout';

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

describe('sheetTop', () => {
  const h = 844;
  const ask = 70;

  it('leaves the docked orb in sight when full', () => {
    expect(sheetTop('full', h, ask)).toBe(SHEET.full);
    expect(SHEET.full).toBeGreaterThanOrEqual(DOCK.phoneInset + DOCK.phoneSize);
  });

  it('takes the lower half when half', () => {
    expect(sheetTop('half', h, ask)).toBe(422);
    expect(sheetTop('half', 845, ask)).toBe(423);
  });

  it('shows only the ask bar when collapsed', () => {
    expect(sheetTop('collapsed', h, ask)).toBe(h - ask);
  });
});

describe('settle', () => {
  const h = 844;
  const ask = 70;

  it('stays at a rest it was let go on', () => {
    expect(settle(SHEET.full, h, ask)).toBe('full');
    expect(settle(422, h, ask)).toBe('half');
    expect(settle(h - ask, h, ask)).toBe('collapsed');
  });

  it('goes to the nearest rest between two', () => {
    expect(settle(200, h, ask)).toBe('full');
    expect(settle(300, h, ask)).toBe('half');
    expect(settle(560, h, ask)).toBe('half');
    expect(settle(700, h, ask)).toBe('collapsed');
  });

  it('settles past the ends on the end rests', () => {
    expect(settle(0, h, ask)).toBe('full');
    expect(settle(h, h, ask)).toBe('collapsed');
  });
});

describe('dragTop', () => {
  const h = 844;
  const ask = 70;

  it('keeps the grab point under the finger', () => {
    expect(dragTop(400, 10, h, ask)).toBe(390);
  });

  it('stops at full and at collapsed', () => {
    expect(dragTop(20, 10, h, ask)).toBe(SHEET.full);
    expect(dragTop(840, 10, h, ask)).toBe(h - ask);
  });
});

describe('stepRest', () => {
  it('moves one rest up or down with the arrows', () => {
    expect(stepRest('collapsed', 'ArrowUp')).toBe('half');
    expect(stepRest('half', 'ArrowUp')).toBe('full');
    expect(stepRest('full', 'ArrowDown')).toBe('half');
    expect(stepRest('half', 'ArrowDown')).toBe('collapsed');
  });

  it('stops at the ends', () => {
    expect(stepRest('full', 'ArrowUp')).toBe('full');
    expect(stepRest('collapsed', 'ArrowDown')).toBe('collapsed');
  });

  it('goes to the ends for Home and End', () => {
    expect(stepRest('half', 'Home')).toBe('collapsed');
    expect(stepRest('half', 'End')).toBe('full');
  });

  it('ignores other keys', () => {
    expect(stepRest('half', 'Enter')).toBeNull();
  });
});

describe('tapped', () => {
  it('steps up a rest, and from full back down to collapsed', () => {
    expect(tapped('collapsed')).toBe('half');
    expect(tapped('half')).toBe('full');
    expect(tapped('full')).toBe('collapsed');
  });
});
