// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createdOf, helloOnce } from '../src/helpers';
import { fromTag, renderScheduled } from '../src/notice';
import { areaWords, chatHead, offerLine, type Offer } from '../src/offer';
import { readyLine } from '../src/ready';
import { INITIAL, type View, offerChosen, reduce } from '../src/view';

const feed = (msgs: Record<string, unknown>[], from: View = INITIAL): View => msgs.reduce((v, m) => reduce(v, m as { type: string }), from);

const OFFER = { type: 'helper.offer', area: 'email', name: 'Inbox', job: 'Keeps your inbox tidy' };
const offerIn = (v: View): Offer => v.entries.find((e) => e.kind === 'offer') as Offer;

afterEach(() => {
  document.body.replaceChildren();
});

describe('the offer line', () => {
  it('joins the conversation, or waits for a reply under way to end', () => {
    const v = feed([OFFER]);
    expect(offerIn(v)).toEqual({ kind: 'offer', area: 'email', name: 'Inbox', job: 'Keeps your inbox tidy', status: 'open', note: '' });
    const during = feed([{ type: 'reply.delta', text: 'Done.' }, OFFER]);
    expect(during.entries.some((e) => e.kind === 'offer')).toBe(false);
    expect(during.held).toHaveLength(1);
    const after = reduce(during, { type: 'turn.complete' });
    expect(after.entries.some((e) => e.kind === 'offer')).toBe(true);
  });

  it('ignores an offer with no name or job', () => {
    expect(feed([{ type: 'helper.offer', area: 'email', name: '', job: 'x' }]).entries).toEqual([]);
  });

  it('reads "Want a helper for {area}? {name}: {job}" with Create and No thanks, which send the choice', () => {
    const chose: string[] = [];
    const line = offerLine(offerIn(feed([OFFER])), (c) => chose.push(c));
    document.body.append(line);
    expect(line.querySelector('.offer-words')?.textContent).toBe('Want a helper for email? Inbox: Keeps your inbox tidy');
    const buttons = [...line.querySelectorAll('button')];
    expect(buttons.map((b) => b.textContent)).toEqual(['Create', 'No thanks']);
    buttons[0]!.click();
    buttons[1]!.click();
    expect(chose).toEqual(['create', 'decline']);
  });

  it('says an area as the user reads it', () => {
    expect(areaWords('email')).toBe('email');
    expect(areaWords('slack')).toBe('Slack');
    expect(areaWords('mcp:notion')).toBe('notion');
  });

  it('becomes "Made {name}." once Create has made it', () => {
    let v = offerChosen(feed([OFFER]), 'email', 'create');
    expect(offerIn(v).status).toBe('creating');
    expect(offerLine(offerIn(v), () => undefined).textContent).toBe('Making Inbox.');
    v = reduce(v, { type: 'helper.created', helper_id: 'a'.repeat(32), conversation_id: 'c-new' });
    expect(offerIn(v).status).toBe('made');
    const line = offerLine(offerIn(v), () => undefined);
    expect(line.textContent).toBe('Made Inbox.');
    expect(line.querySelectorAll('button')).toHaveLength(0);
  });

  it('becomes "Okay." on No thanks', () => {
    const v = offerChosen(feed([OFFER]), 'email', 'decline');
    expect(offerLine(offerIn(v), () => undefined).textContent).toBe('Okay.');
  });

  it('says in a few plain words why it could not be made', () => {
    const words = (reason: string): string => {
      const v = reduce(offerChosen(feed([OFFER]), 'email', 'create'), { type: 'helper.offer.failed', reason });
      return offerLine(offerIn(v), () => undefined).textContent ?? '';
    };
    expect(words('full')).toBe('You have 20 helpers, the most there can be. Delete one to make room.');
    expect(words('taken')).toBe('There is already a helper called Inbox.');
    expect(words('stale')).toBe('That offer has run out. Ask her again if you still want one.');
    expect(words('invalid')).toBe("That name or job didn't work. Ask her to make it.");
    expect(words('error')).toBe("That didn't work. Try again in a moment.");
    for (const w of ['full', 'taken', 'stale', 'invalid', 'error', 'odd']) expect(words(w)).not.toMatch(/[–—]/);
  });

  it('a made helper with no offer open changes no line', () => {
    const v = feed([OFFER, { type: 'helper.created', helper_id: 'x', conversation_id: 'c' }]);
    expect(offerIn(v).status).toBe('open');
  });
});

describe('helper.created', () => {
  it('names the panel’s helper and the chat to open', () => {
    expect(createdOf({ type: 'helper.created', helper_id: 'a'.repeat(32), conversation_id: 'c-new' })).toEqual({ helperId: `own:${'a'.repeat(32)}`, conversationId: 'c-new' });
    expect(createdOf({ type: 'helper.created', helper_id: `own:${'b'.repeat(32)}`, conversation_id: 'c' })?.helperId).toBe(`own:${'b'.repeat(32)}`);
    expect(createdOf({ type: 'helper.created', helper_id: 'x' })).toBeNull();
    expect(createdOf({ type: 'notice' })).toBeNull();
  });
});

describe('the first hello', () => {
  it('is asked once per page load per helper', () => {
    const send = vi.fn(() => true);
    const hello = helloOnce(send);
    hello('own:a');
    hello('own:a');
    hello('own:b');
    expect(send.mock.calls).toEqual([[{ type: 'helper.hello' }], [{ type: 'helper.hello' }]]);
  });

  it('tries again when the socket was not open', () => {
    const send = vi.fn().mockReturnValueOnce(false).mockReturnValue(true);
    const hello = helloOnce(send);
    hello('own:a');
    hello('own:a');
    hello('own:a');
    expect(send).toHaveBeenCalledTimes(2);
  });
});

describe('pictures in the chat', () => {
  it('shows a create card’s picture, from its look', () => {
    const v = feed([{ type: 'confirm.request', pending_id: 'p1', tool: 'helper_create', summary: 'New helper: Invoices. Chases late payments.', expires_at: 1, look: { icon: 'receipt', color: 'green' } }]);
    expect(v.entries[0]).toMatchObject({ kind: 'card', look: { icon: 'receipt', color: 'green' } });
    const plain = feed([{ type: 'confirm.request', pending_id: 'p2', summary: 'Send it', expires_at: 1 }]);
    expect((plain.entries[0] as { look?: unknown }).look ?? null).toBeNull();
  });

  it('puts a helper’s picture on its lines in her conversation', () => {
    const v = feed([
      { type: 'notice', code: 'schedule', id: 'n1', kind: 'ready', title: 'Invoices: 3 are overdue.', conversation_id: 'c-inv', helper_id: 'own:x', helper_name: 'Invoices', helper_icon: 'receipt', helper_color: 'green' },
    ]);
    const e = v.entries[0] as Extract<View['entries'][number], { kind: 'scheduled' }>;
    expect(e.helper).toEqual({ id: 'own:x', name: 'Invoices', look: { icon: 'receipt', color: 'green' } });
    const line = readyLine(e.title, e.conversationId, () => undefined, e.helper, () => undefined);
    const pic = line.querySelector('.notice-from .look') as HTMLElement;
    expect(pic.dataset['size']).toBe('18');
    expect(pic.getAttribute('aria-hidden')).toBe('true');
    expect(line.querySelector('.notice-from')?.getAttribute('aria-label')).toBe('Invoices, in Helpers');
    // A routine's line has no picture.
    expect(fromTag({ id: 'routine:a', name: 'Morning brief' }, () => undefined).querySelector('.look')).toBeNull();
    const asked = renderScheduled({ ...e, what: 'ask', text: 'Done' }, (t) => [Object.assign(document.createElement('p'), { textContent: t })], undefined, () => undefined);
    expect(asked.querySelector('.notice-from .look')).not.toBeNull();
  });

  it('heads a helper’s own chat with its picture and name', () => {
    const head = chatHead('Invoices', { icon: 'receipt', color: 'green' }, 'Keeps track of who owes you');
    const pic = head.querySelector('.look') as HTMLElement;
    expect(pic.dataset['size']).toBe('44');
    expect(pic.getAttribute('aria-label')).toBe('Invoices picture: receipt, green');
    expect(head.querySelector('.helper-head-name')?.textContent).toBe('Invoices');
    expect(head.textContent).toContain('Keeps track of who owes you');
  });
});
