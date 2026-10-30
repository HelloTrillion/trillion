import { describe, expect, it } from 'vitest';
import { speechReach, systemVoices } from '../src/speech';

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

describe('speech providers out of reach', () => {
  it('counts a provider run by someone else as hosted, and one at an address as not', () => {
    expect(speechReach({ title: 'ElevenLabs', where: 'core', takes_url: false })).toEqual({ provider: 'ElevenLabs', hosted: true });
    expect(speechReach({ title: 'Another compatible service', where: 'core', takes_url: true }).hosted).toBe(false);
    expect(speechReach({ title: "This computer's voice", where: 'client', takes_url: false }).hosted).toBe(false);
  });
});
