import { describe, expect, it } from 'vitest';
import { choiceLabel, megabytes } from '../src/listening';

describe('mic settings', () => {
  it('says how large a download is before it is agreed to', () => {
    expect(megabytes(147_769_510)).toBe('148 MB');
    expect(megabytes(486_212_372)).toBe('486 MB');
    expect(megabytes(12)).toBe('1 MB');
  });

  it('labels a choice by whether it is ready', () => {
    expect(choiceLabel({ id: 'base.en', name: 'English, fast', download: null })).toBe('English, fast');
    expect(choiceLabel({ id: 'small', name: 'Many languages', download: 486_212_372 })).toBe(
      'Many languages (486 MB download)',
    );
  });
});
