import { describe, expect, it } from 'vitest';
import { systemVoices } from '../src/speech';

const v = (name: string, lang: string): SpeechSynthesisVoice =>
  ({ name, lang, voiceURI: `uri:${name}`, default: false, localService: true }) as SpeechSynthesisVoice;

describe('system voices', () => {
  it("puts the user's own language first, then sorts by name", () => {
    const list = systemVoices([v('Zoe', 'en-GB'), v('Anna', 'de-DE'), v('Alex', 'en-US')], 'en-US');
    expect(list.map((c) => c.name)).toEqual(['Alex (en-US)', 'Zoe (en-GB)', 'Anna (de-DE)']);
    expect(list[0]?.id).toBe('uri:Alex');
  });

  it('is empty when the browser has none', () => {
    expect(systemVoices([], 'en')).toEqual([]);
  });
});
