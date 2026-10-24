import { describe, expect, it } from 'vitest';
import { OPTIONS, errorText, preferred } from '../src/settings';

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
