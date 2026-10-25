import { describe, expect, it } from 'vitest';
import { crumb } from '../src/panel';

describe('crumb', () => {
  it('names the section after Settings, for the workspace bar', () => {
    expect(crumb('Voice')).toBe('Settings / Voice');
  });

  it('is just Settings before a section is known', () => {
    expect(crumb('')).toBe('Settings');
  });
});
