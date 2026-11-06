// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';

import { sameByName } from '../src/mic';
import { createMicrophones } from '../src/microphone';
import { saveMic, savedMic, savedMicName } from '../src/prefs';

const input = (deviceId: string, label: string) => ({ kind: 'audioinput', deviceId, label, groupId: '' });

function devices(list: ReturnType<typeof input>[]): void {
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { enumerateDevices: vi.fn(async () => list) },
  });
}

afterEach(() => {
  localStorage.clear();
});

describe('the chosen microphone, when its id changes between launches', () => {
  it('is remembered by name as well as id, and both go when the choice is cleared', () => {
    saveMic('id-1', 'Scarlett Solo 4th Gen');
    expect(savedMic()).toBe('id-1');
    expect(savedMicName()).toBe('Scarlett Solo 4th Gen');
    // Found again under a new id: the name is kept.
    saveMic('id-2');
    expect(savedMic()).toBe('id-2');
    expect(savedMicName()).toBe('Scarlett Solo 4th Gen');
    saveMic(null);
    expect(savedMic()).toBeNull();
    expect(savedMicName()).toBeNull();
  });

  it('is found by name under its new id', async () => {
    devices([input('default', 'Default'), input('teams', 'Microsoft Teams Audio'), input('new', 'Scarlett Solo 4th Gen')]);
    expect(await sameByName('Scarlett Solo 4th Gen', 'old')).toBe('new');
  });

  it('is not found without a name, when absent, or when the id has not changed', async () => {
    devices([input('new', 'Scarlett Solo 4th Gen')]);
    expect(await sameByName(null, 'old')).toBeNull();
    expect(await sameByName('Studio Display Microphone', 'old')).toBeNull();
    expect(await sameByName('Scarlett Solo 4th Gen', 'new')).toBeNull();
  });

  it('is shown chosen in Settings, and its new id is saved', async () => {
    devices([input('teams', 'Microsoft Teams Audio'), input('new', 'Scarlett Solo 4th Gen')]);
    const choose = vi.fn();
    const mics = createMicrophones(
      () => 'old',
      choose,
      undefined,
      () => 'Scarlett Solo 4th Gen',
    );
    await mics.refresh();
    const pick = mics.element.querySelector('select') as HTMLSelectElement;
    expect(pick.value).toBe('new');
    expect(choose).toHaveBeenCalledWith('new', 'Scarlett Solo 4th Gen');
  });

  it('saves the name with a choice made in Settings', async () => {
    devices([input('a', 'Studio Display Microphone'), input('b', 'Scarlett Solo 4th Gen')]);
    const choose = vi.fn();
    const mics = createMicrophones(() => null, choose);
    await mics.refresh();
    const pick = mics.element.querySelector('select') as HTMLSelectElement;
    pick.value = 'b';
    pick.dispatchEvent(new Event('change'));
    expect(choose).toHaveBeenCalledWith('b', 'Scarlett Solo 4th Gen');
  });

  it('learns the name of a choice saved without one, once Settings can see names', async () => {
    devices([input('teams', 'Microsoft Teams Audio'), input('sd', 'Studio Display Microphone')]);
    const choose = vi.fn();
    const mics = createMicrophones(() => 'sd', choose, undefined, () => null);
    await mics.refresh();
    expect(choose).toHaveBeenCalledWith('sd', 'Studio Display Microphone');
  });
});
