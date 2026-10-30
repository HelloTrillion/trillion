import { describe, expect, it } from 'vitest';
import { choiceLabel, listeningReach, megabytes } from '../src/listening';

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

describe('hearing providers out of reach', () => {
  it('counts a keyed service as hosted and the one on this computer as not', () => {
    expect(listeningReach({ title: 'Deepgram', needs_key: true })).toEqual({ provider: 'Deepgram', hosted: true });
    expect(listeningReach({ title: 'On this computer (Whisper)', needs_key: false }).hosted).toBe(false);
  });
});
