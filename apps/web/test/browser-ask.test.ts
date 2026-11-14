// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { type AskNotice, OPEN_HER_BROWSER, answerAsk, askLine, settledWords } from '../src/notice';
import { INITIAL, type View, askAnswered, askSending, reduce } from '../src/view';

const feed = (msgs: Record<string, unknown>[], from: View = INITIAL): View => msgs.reduce((v, m) => reduce(v, m as { type: string }), from);

const TASK = 'abcdef0123456789';
const chip = (id = 'a1b2c3d4e5f6', kind = 'chip') => ({
  type: 'notice',
  code: 'browser',
  text: 'Her browsing helper wants to open example.org: it needs your OK.',
  ask: { task: TASK, id, kind },
});
const lineIn = (v: View, id = 'a1b2c3d4e5f6'): AskNotice => v.entries.find((e) => e.kind === 'notice' && e.ask?.id === id) as AskNotice;
const words = (node: HTMLElement): string[] => [...node.querySelectorAll('button')].map((b) => b.textContent ?? '');

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

afterEach(() => document.body.replaceChildren());

describe('a browsing helper’s ask in her conversation', () => {
  it('a chip shows Allow, Always allow and Not now under the host’s line', () => {
    const line = askLine(lineIn(feed([chip()])), () => undefined, () => undefined);
    expect(line.querySelector('.browser-ask-words')?.textContent).toBe('Her browsing helper wants to open example.org: it needs your OK.');
    expect(words(line)).toEqual(['Allow', 'Always allow', 'Not now']);
  });

  it('a sign-in shows Use it and Not now, never Always allow', () => {
    const v = feed([{ ...chip('0a0b0c0d0e0f', 'signin'), text: 'Her browsing helper can use your sign-in for library.org: it needs your OK.' }]);
    expect(words(askLine(lineIn(v, '0a0b0c0d0e0f'), () => undefined, () => undefined))).toEqual(['Use it', 'Not now']);
  });

  it('a step shows only View, which opens the window at its task', () => {
    const v = feed([{ ...chip('0f0f0f0f0f0f', 'step'), text: 'Her browsing helper needs you on example.org: open her browser to take over.' }]);
    const opened: string[] = [];
    const line = askLine(lineIn(v, '0f0f0f0f0f0f'), () => undefined, (t) => opened.push(t));
    expect(words(line)).toEqual(['View']);
    line.querySelector('button')!.click();
    expect(opened).toEqual([TASK]);
  });

  it('each button hands its own answer', () => {
    const said: string[] = [];
    const line = askLine(lineIn(feed([chip()])), (w) => said.push(w), () => undefined);
    for (const b of line.querySelectorAll('button')) b.click();
    expect(said).toEqual(['allowed', 'always', 'declined']);
  });

  it('a card’s line, or one with a bad id, carries nothing to answer', () => {
    const v = feed([
      { type: 'notice', code: 'browser', text: 'Before it sends the form to example.org, it needs your OK.' },
      { ...chip(), ask: { task: '../../x', id: 'a1', kind: 'chip' } },
      { ...chip(), ask: { task: TASK, id: 'a1', kind: 'card' } },
    ]);
    expect(v.entries.every((e) => e.kind === 'notice' && e.ask === undefined)).toBe(true);
  });

  it('the same ask told twice is one line', () => {
    expect(feed([chip(), chip()]).entries).toHaveLength(1);
  });

  it('posts the answer to the window’s own route', async () => {
    const calls: [string, string, unknown][] = [];
    const api = async (method: string, path: string, body?: unknown): Promise<Response> => {
      calls.push([method, path, body]);
      return json({ tasks: [] });
    };
    const ask = lineIn(feed([chip()])).ask;
    expect(await answerAsk(api, ask, 'always')).toBe('always');
    expect(calls).toEqual([['POST', `/api/browser/tasks/${TASK}/asks/a1b2c3d4e5f6`, { answer: 'always' }]]);
  });

  it('a stale answer is "stale", and one that didn’t go is null', async () => {
    const ask = lineIn(feed([chip()])).ask;
    expect(await answerAsk(async () => json({ error: 'That isn’t waiting any more.' }, 409), ask, 'allowed')).toBe('stale');
    expect(await answerAsk(async () => json({}, 500), ask, 'allowed')).toBeNull();
    expect(
      await answerAsk(
        async () => {
          throw new Error('down');
        },
        ask,
        'allowed',
      ),
    ).toBeNull();
  });

  it('while an answer is on its way, its buttons are off; once taken, they are gone', () => {
    let v = askSending(feed([chip()]), 'a1b2c3d4e5f6');
    const sending = askLine(lineIn(v), () => undefined, () => undefined);
    expect([...sending.querySelectorAll('button')].every((b) => b.disabled)).toBe(true);
    v = askAnswered(v, 'a1b2c3d4e5f6', 'always');
    const done = askLine(lineIn(v), () => undefined, () => undefined);
    expect(done.querySelectorAll('button')).toHaveLength(0);
    expect(done.querySelector('.browser-ask-settled')?.textContent).toBe(settledWords('always'));
  });

  it('an answer that didn’t go leaves it open, saying so', () => {
    const v = askAnswered(askSending(feed([chip()]), 'a1b2c3d4e5f6'), 'a1b2c3d4e5f6', null, 'That didn’t go through.');
    const line = askLine(lineIn(v), () => undefined, () => undefined);
    expect(words(line)).toEqual(['Allow', 'Always allow', 'Not now']);
    expect(line.querySelector('.browser-ask-note')?.textContent).toBe('That didn’t go through.');
  });

  it('answered in her browser’s window, or run out: settled here as the core says', () => {
    let v = feed([chip(), { type: 'browser.ask.resolved', ask_id: 'a1b2c3d4e5f6', answer: 'declined' }]);
    expect(lineIn(v).ask.status).toBe('settled');
    const line = askLine(lineIn(v), () => undefined, () => undefined);
    expect(line.querySelectorAll('button')).toHaveLength(0);
    expect(line.textContent).toContain('Not now.');
    // A late reply to an answer sent here doesn't change what the core said.
    v = askAnswered(v, 'a1b2c3d4e5f6', 'stale');
    expect(lineIn(v).ask.answer).toBe('declined');
    v = feed([chip(), { type: 'browser.ask.resolved', ask_id: 'a1b2c3d4e5f6', answer: 'expired' }]);
    expect(askLine(lineIn(v), () => undefined, () => undefined).textContent).toContain('No answer in time');
  });

  it('a stale button says it isn’t waiting any more', () => {
    const v = askAnswered(askSending(feed([chip()]), 'a1b2c3d4e5f6'), 'a1b2c3d4e5f6', 'stale');
    expect(askLine(lineIn(v), () => undefined, () => undefined).textContent).toContain('That isn’t waiting any more.');
  });

  it('View asks her browser’s window to open at the task', async () => {
    const { openHerBrowser } = await import('../src/notice');
    const got: unknown[] = [];
    const listen = (ev: Event): void => void got.push((ev as CustomEvent<unknown>).detail);
    addEventListener(OPEN_HER_BROWSER, listen);
    openHerBrowser(TASK);
    removeEventListener(OPEN_HER_BROWSER, listen);
    expect(got).toEqual([TASK]);
  });
});
