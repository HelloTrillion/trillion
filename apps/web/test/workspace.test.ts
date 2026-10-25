import { describe, expect, it } from 'vitest';
import { createShowing } from '../src/workspace';

function watched() {
  const s = createShowing();
  const heard: { id: string | null; isOpen: boolean; showing: string | null }[] = [];
  s.onClose((id) => heard.push({ id, isOpen: s.isOpen, showing: s.id }));
  return { s, heard };
}

describe('createShowing', () => {
  it('starts with nothing shown', () => {
    const { s } = watched();
    expect(s.isOpen).toBe(false);
    expect(s.id).toBeNull();
  });

  it('shows an id and tells listeners that id when it closes', () => {
    const { s, heard } = watched();
    s.show('settings');
    expect(s.isOpen).toBe(true);
    expect(s.id).toBe('settings');
    expect(s.close()).toBe(true);
    expect(s.isOpen).toBe(false);
    expect(s.id).toBeNull();
    expect(heard).toEqual([{ id: 'settings', isOpen: false, showing: null }]);
  });

  it('does nothing when closing what is not open', () => {
    const { s, heard } = watched();
    expect(s.close()).toBe(false);
    expect(heard).toEqual([]);
  });

  it('closes what it replaces first, seen as a real close', () => {
    const { s, heard } = watched();
    s.show('settings');
    s.show('file');
    expect(s.id).toBe('file');
    expect(heard).toEqual([{ id: 'settings', isOpen: false, showing: null }]);
  });

  it('opening the first thing closes nothing', () => {
    const { s, heard } = watched();
    s.show(null);
    expect(s.isOpen).toBe(true);
    expect(heard).toEqual([]);
  });
});
