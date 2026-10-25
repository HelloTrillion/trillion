import { describe, expect, it } from 'vitest';
import { captionOf, captionSpot, tail } from '../src/captions';
import { orbRect } from '../src/layout';
import { INITIAL, type View } from '../src/view';

const reply = (text: string): View['entries'] => [{ kind: 'assistant', text, interrupted: false }];

describe('captionOf', () => {
  it('shows what she hears first', () => {
    const v = { ...INITIAL, hearing: 'turn it', entries: reply('Hi'), replyOpen: true };
    expect(captionOf(v)).toBe('turn it');
  });
  it('shows her reply while it is open', () => {
    expect(captionOf({ ...INITIAL, entries: reply('Hello there'), replyOpen: true })).toBe('Hello there');
  });
  it('shows her reply while she speaks it', () => {
    expect(captionOf({ ...INITIAL, entries: reply('Hello'), orb: 'speaking' })).toBe('Hello');
  });
  it('shows nothing for a finished reply with the orb idle', () => {
    expect(captionOf({ ...INITIAL, entries: reply('Done'), orb: 'idle' })).toBe('');
  });
});

describe('tail', () => {
  it('keeps short text', () => {
    expect(tail('short')).toBe('short');
  });
  it('trims long text at a word', () => {
    const t = tail('alpha beta gamma delta', 14);
    expect(t.startsWith('…')).toBe(true);
    expect(t).toBe('…gamma delta');
  });
});

describe('captionSpot', () => {
  const f = { w: 1200, h: 800, sidebar: 360, workspace: false, phone: false };
  it('sits under the orb, centred on its x, inside the stage', () => {
    const s = captionSpot(f);
    const orb = orbRect(f);
    expect(s.x).toBe(orb.x);
    expect(s.top).toBeCloseTo(orb.y + orb.size * 0.4);
    expect(s.top).toBeGreaterThan(orb.y + orb.size * 0.19);
    expect(s.top).toBeLessThan(f.h);
    expect(s.width).toBeLessThanOrEqual(f.w - f.sidebar);
  });
  it('stays clear of the window bottom', () => {
    expect(captionSpot({ ...f, h: 300 }).top).toBeLessThanOrEqual(300 - 72);
  });
  it('follows the orb when the sidebar hides', () => {
    expect(captionSpot({ ...f, sidebar: 0 }).x).toBe(600);
  });
});
