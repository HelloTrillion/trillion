import { describe, expect, it } from 'vitest';
import { announcement, statusText } from '../src/announce';
import { INITIAL, type View } from '../src/view';

const ready: View = { ...INITIAL, link: 'ready' };

describe('statusText', () => {
  it('says what she is doing once connected', () => {
    expect(statusText(ready)).toBe('Ready');
    expect(statusText({ ...ready, orb: 'processing' })).toBe('Thinking');
    expect(statusText({ ...ready, orb: 'speaking', noticeSpeaking: true })).toBe('Speaking');
  });

  it('says connecting or offline otherwise', () => {
    expect(statusText(INITIAL)).toBe('Connecting');
    expect(statusText({ ...INITIAL, link: 'offline' })).toBe('Offline');
  });
});

describe('announcement', () => {
  it('says nothing when nothing changed', () => {
    expect(announcement(ready, { ...ready })).toBeNull();
  });

  it('announces a change of status', () => {
    expect(announcement(ready, { ...ready, orb: 'listening' })).toBe('Listening');
  });

  it('announces her reply once it is finished, not while it streams', () => {
    const streaming: View = { ...ready, replyOpen: true, entries: [{ kind: 'assistant', text: 'Hel', interrupted: false }] };
    const more: View = { ...streaming, entries: [{ kind: 'assistant', text: 'Hello, Sam', interrupted: false }] };
    expect(announcement(streaming, more)).toBeNull();
    expect(announcement(more, { ...more, replyOpen: false })).toBe('Hello, Sam');
  });

  it('announces the words she heard when a voice turn completes', () => {
    const heard: View = { ...ready, entries: [{ kind: 'user', text: 'what time is it' }], pending: null };
    expect(announcement(ready, heard)).toBe('You said: what time is it');
  });

  it('does not echo a typed message', () => {
    const typed: View = { ...ready, entries: [{ kind: 'user', text: 'hi' }], pending: 'hi' };
    expect(announcement(ready, typed)).toBeNull();
  });

  it('does not read out a loaded history', () => {
    const history: View = {
      ...ready,
      entries: [
        { kind: 'user', text: 'one' },
        { kind: 'assistant', text: 'two', interrupted: false },
      ],
    };
    expect(announcement(ready, history)).toBeNull();
  });

  it('puts several pieces of news in one line', () => {
    const open: View = { ...ready, orb: 'speaking', busy: true, replyOpen: true, entries: [{ kind: 'assistant', text: 'Done', interrupted: false }] };
    expect(announcement(open, { ...open, orb: 'idle', busy: false, replyOpen: false })).toBe('Done. Ready');
  });
});
