import { describe, expect, it } from 'vitest';
import { ROAST_TOOL, isRoastTurn, plain, weekRange, wrap } from '../src/roast';
import { INITIAL, type View, reduce, toolLabel } from '../src/view';

const feed = (msgs: Record<string, unknown>[], from: View = INITIAL): View =>
  msgs.reduce((v, m) => reduce(v, m as { type: string }), from);

const asked = [
  { type: 'session', id: 'c1', agent_name: 'Nova', model: null, title: null },
  { type: 'transcript.final', text: 'roast my week' },
  { type: 'state', orb: 'processing' },
];
const read = [
  { type: 'tool.start', call_id: 't1', name: ROAST_TOOL },
  { type: 'tool.end', call_id: 't1', ok: true, summary: 'succeeded' },
];

const reply = (v: View) => v.entries.filter((e) => e.kind === 'assistant').at(-1);

describe('roast my week', () => {
  it('marks the reply to a roast, and makes it shareable only once her turn is over', () => {
    let v = feed([...asked, ...read, { type: 'reply.delta', text: 'You talked to me at 2:14 am. ' }]);
    expect(reply(v)).toMatchObject({ roast: 'writing' });
    v = feed([{ type: 'reply.delta', text: 'Bold.' }], v);
    expect(reply(v)).toMatchObject({ text: 'You talked to me at 2:14 am. Bold.', roast: 'writing' });
    v = feed([{ type: 'turn.complete' }], v);
    expect(reply(v)).toMatchObject({ roast: 'done' });
  });

  it('keeps a roast cut short shareable, as far as it got', () => {
    const v = feed([...asked, ...read, { type: 'reply.delta', text: 'Thirty messages.' }, { type: 'interrupted' }]);
    expect(reply(v)).toMatchObject({ roast: 'done', interrupted: true });
  });

  it('leaves any other reply alone, and a failed read is no roast', () => {
    const plainReply = feed([...asked, { type: 'reply.delta', text: 'Hi.' }, { type: 'turn.complete' }]);
    expect(reply(plainReply)).not.toHaveProperty('roast');
    const failed = feed([
      ...asked,
      { type: 'tool.start', call_id: 't1', name: ROAST_TOOL },
      { type: 'tool.end', call_id: 't1', ok: false, summary: 'failed' },
      { type: 'reply.delta', text: "I can't read your week." },
    ]);
    expect(reply(failed)).not.toHaveProperty('roast');
  });

  it('looks back only as far as the user last spoke', () => {
    const earlier = feed([...asked, ...read, { type: 'reply.delta', text: 'Roasted.' }, { type: 'turn.complete' }]);
    const next = feed([{ type: 'transcript.final', text: 'thanks' }, { type: 'reply.delta', text: 'Any time.' }], earlier);
    expect(isRoastTurn(earlier.entries.slice(0, -1))).toBe(true);
    expect(reply(next)).not.toHaveProperty('roast');
  });

  it('names the chip for what she is doing', () => {
    expect(toolLabel(ROAST_TOOL, null, true)).toBe('Reading your week …');
    expect(toolLabel(ROAST_TOOL, 'succeeded', true)).toBe('Read your week');
  });

  it('shows her words on the card without markdown marks', () => {
    expect(plain('**Thirty** messages.\n\n- You cut me off *nine* times.\n1. Bold.')).toEqual([
      'Thirty messages.',
      'You cut me off nine times.',
      'Bold.',
    ]);
  });

  it('wraps each paragraph to the width it is given', () => {
    const measure = (s: string) => s.length;
    expect(wrap(['one two three four', 'five'], 9, measure)).toEqual([['one two', 'three', 'four'], ['five']]);
    // A word longer than the line still gets a line of its own.
    expect(wrap(['extraordinarily so'], 5, measure)).toEqual([['extraordinarily', 'so']]);
  });

  it('dates the card by the last seven days', () => {
    expect(weekRange(new Date(2026, 10, 6, 18), 'en-US')).toBe('Oct 31 – Nov 6');
  });
});
