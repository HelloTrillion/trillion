import { describe, expect, it } from 'vitest';
import { OPTIONS, errorText, modelReach, preferred } from '../src/settings';

describe('model settings', () => {
  it('explains a rejected key in plain words', () => {
    expect(errorText('unauthorized')).toMatch(/rejected that key/);
    expect(errorText('something-new')).toMatch(/did not work/);
  });

  it('words every error a provider can send back', () => {
    for (const code of ['bad_request', 'provider_error', 'bad_stream']) {
      expect(errorText(code)).not.toMatch(/did not work/);
    }
  });

  it('says a hosted provider is out of reach in terms of being online, a local one in terms of the address', () => {
    expect(errorText('unreachable')).toMatch(/Is it running, and is the address right\?/);
    expect(errorText('unreachable', { provider: 'ElevenLabs', hosted: true })).toBe(
      'Could not reach ElevenLabs. Check this computer is online.',
    );
    expect(errorText('unreachable', { hosted: true })).toBe('Could not reach the provider. Check this computer is online.');
    expect(errorText('unreachable', { provider: 'Ollama', hosted: false })).toMatch(/address right/);
    // Other errors read the same either way.
    expect(errorText('unauthorized', { provider: 'OpenAI', hosted: true })).toBe(errorText('unauthorized'));
  });

  it('counts the model providers with no address as hosted', () => {
    const by = (kind: string) => modelReach(OPTIONS.find((o) => o.kind === kind)!);
    expect(by('anthropic')).toEqual({ provider: 'Anthropic', hosted: true });
    expect(by('openai')).toEqual({ provider: 'OpenAI', hosted: true });
    expect(by('local').hosted).toBe(false);
    expect(by('custom').hosted).toBe(false);
  });

  it('keeps the saved model selected, else the recommended one, else the first', () => {
    const list = [{ id: 'a', label: 'a' }, { id: 'claude-opus-5-5', label: 'Opus' }];
    expect(preferred(list, 'a')).toBe('a');
    expect(preferred(list, 'gone')).toBe('claude-opus-5-5');
    expect(preferred([{ id: 'x', label: 'x' }], null)).toBe('x');
    expect(preferred([], null)).toBe('');
  });

  it('asks for a key only where one is used, and only links to the provider that issues it', () => {
    const local = OPTIONS.find((o) => o.kind === 'local');
    expect(local).toMatchObject({ takesKey: false, needsKey: false, takesAddress: true });
    for (const o of OPTIONS) {
      if (o.needsKey) expect(o.takesKey).toBe(true);
      if (o.keyLink) expect(o.keyLink).toMatch(/^https:\/\/(console\.anthropic\.com|platform\.openai\.com)\//);
    }
  });
});
