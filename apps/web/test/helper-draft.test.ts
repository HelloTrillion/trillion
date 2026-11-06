// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ARRIVE_MS, CHANGED_MS, type DraftCard, changedParts, createDraftPin } from '../src/draft';
import { chatHead } from '../src/offer';
import { INITIAL, type View, draftIn, pinnedDraft, reduce } from '../src/view';

const feed = (msgs: Record<string, unknown>[], from: View = INITIAL): View => msgs.reduce((v, m) => reduce(v, m as { type: string }), from);

const DRAFT = {
  name: 'Socials',
  job: 'I help you grow your channels.',
  notes: 'Posts go out on weekdays.',
  connectors: [
    { id: 'canva', title: 'Canva', connected: false },
    { id: 'zapier', title: 'Zapier', connected: true },
  ],
  asks: ['Which channels matter most?'],
};

const card = (id: string, draft: Record<string, unknown> = DRAFT): Record<string, unknown> => ({
  type: 'confirm.request',
  pending_id: id,
  tool: 'helper_create',
  tool_label: 'Make a helper',
  summary: `New helper: ${String(draft['name'])}.`,
  expires_at: Date.now() + 600_000,
  reason: 'irreversible',
  look: { icon: 'spark', color: 'teal' },
  draft,
});

const pinned = (v: View): DraftCard => pinnedDraft(v.entries) as DraftCard;

afterEach(() => {
  document.body.replaceChildren();
  vi.useRealTimers();
});

describe('the draft in the view', () => {
  it('keeps the draft on its card, leaving out what is not one', () => {
    const v = feed([card('p1')]);
    expect(pinned(v).draft).toEqual(DRAFT);
    expect(draftIn({ name: 'A', job: 'B', connectors: [{ id: '' }, 'x', { id: 'c', connected: 'yes' }], asks: ['', 1, 'Q?'] })).toEqual({
      name: 'A',
      job: 'B',
      notes: '',
      connectors: [{ id: 'c', title: 'c', connected: false }],
      asks: ['Q?'],
    });
    expect(draftIn({ name: '', job: 'B' })).toBeNull();
    expect(draftIn(null)).toBeNull();
  });

  it('replaces the draft still waiting where it was: never two', () => {
    const v = feed([card('p1'), { type: 'reply.delta', text: 'Anything to add?' }, { type: 'turn.complete' }, card('p2', { ...DRAFT, name: 'Social Media' })]);
    const cards = v.entries.filter((e) => e.kind === 'card');
    expect(cards).toHaveLength(1);
    expect(v.entries[0]).toMatchObject({ kind: 'card', pendingId: 'p2' });
    expect(pinned(v).draft.name).toBe('Social Media');
  });

  it('pins only a waiting draft', () => {
    const v = feed([card('p1'), { type: 'confirm.resolved', pending_id: 'p1', status: 'succeeded' }]);
    expect(pinnedDraft(v.entries)).toBeNull();
    // Any other card is never pinned.
    const other = feed([{ ...card('p3'), tool: 'send_email', draft: undefined }]);
    expect(pinnedDraft(other.entries)).toBeNull();
  });
});

describe('the pinned draft', () => {
  const setup = () => {
    const sent: string[] = [];
    const pin = createDraftPin({
      onCreate: (id) => sent.push(`create:${id}`),
      onNotNow: (id) => sent.push(`no:${id}`),
      onConnect: (id) => sent.push(`connect:${id}`),
    });
    document.body.append(pin.el);
    return { pin, sent };
  };

  it('shows the orb, name, job, notes, connections and first questions', () => {
    const { pin } = setup();
    pin.draw(pinned(feed([card('p1')])));
    const el = pin.el;
    expect(el.hidden).toBe(false);
    expect(el.querySelector('.look')?.getAttribute('aria-label')).toBe('Socials, teal');
    expect(el.querySelector('.draft-name')?.textContent).toBe('Socials');
    expect(el.querySelector('.draft-job')?.textContent).toBe('I help you grow your channels.');
    expect(el.querySelector('.draft-notes-text')?.textContent).toBe('Posts go out on weekdays.');
    // Its notes and questions fold away, so Create stays in sight.
    expect((el.querySelector('.draft-details') as HTMLElement).hidden).toBe(true);
    const chips = [...el.querySelectorAll('.draft-chip')];
    expect(chips.map((c) => c.textContent)).toEqual(['Connect Canva', 'Zapier']);
    expect([...el.querySelectorAll('.draft-asks-list li')].map((l) => l.textContent)).toEqual(['Which channels matter most?']);
  });

  it('folds its notes and questions behind Details, beside Create', () => {
    const { pin } = setup();
    pin.draw(pinned(feed([card('p1')])));
    const details = pin.el.querySelector('.draft-details') as HTMLElement;
    const more = pin.el.querySelector('.draft-actions .draft-more') as HTMLButtonElement;
    expect(more.textContent).toBe('Details');
    more.click();
    expect(details.hidden).toBe(false);
    expect(more.textContent).toBe('Hide details');
    expect(more.getAttribute('aria-expanded')).toBe('true');
    // With nothing to fold, no Details.
    pin.draw(pinned(feed([card('p2', { ...DRAFT, notes: '', asks: [] })])));
    expect(pin.el.querySelector('.draft-more')).toBeNull();
  });

  it('Create and Not now send its approve and decline, once they are ready', () => {
    vi.useFakeTimers();
    const { pin, sent } = setup();
    pin.draw(pinned(feed([card('p1')])));
    const [create, notNow] = [...pin.el.querySelectorAll('.draft-actions button')] as HTMLButtonElement[];
    expect([create!.textContent, notNow!.textContent]).toEqual(['Create', 'Not now']);
    expect(create!.disabled).toBe(true);
    vi.advanceTimersByTime(ARRIVE_MS);
    create!.click();
    expect(sent).toEqual(['create:p1']);
    // Not twice.
    expect(create!.disabled && notNow!.disabled).toBe(true);
    pin.draw(pinned(feed([card('p2')])));
    vi.advanceTimersByTime(ARRIVE_MS);
    (pin.el.querySelectorAll('.draft-actions button')[1] as HTMLButtonElement).click();
    expect(sent).toEqual(['create:p1', 'no:p2']);
  });

  it('a connection to make opens Settings at it', () => {
    const { pin, sent } = setup();
    pin.draw(pinned(feed([card('p1')])));
    (pin.el.querySelector('button.draft-chip') as HTMLButtonElement).click();
    expect(sent).toEqual(['connect:canva']);
  });

  it('updates in place on a new draft, marking what changed for a moment', () => {
    vi.useFakeTimers();
    const { pin } = setup();
    const v1 = feed([card('p1')]);
    pin.draw(pinned(v1));
    const root = pin.el;
    const v2 = reduce(v1, card('p2', { ...DRAFT, notes: 'Posts at nine.', asks: [] }) as { type: string });
    pin.draw(pinned(v2));
    expect(pin.el).toBe(root);
    expect(root.querySelector('.draft-notes-text')?.textContent).toBe('Posts at nine.');
    expect(root.querySelector('.draft-asks')).toBeNull();
    const changed = [...root.querySelectorAll('[data-part][data-changed]')].map((n) => (n as HTMLElement).dataset['part']);
    expect(changed).toEqual(['notes']);
    // Folded away, the change shows on Details.
    expect(root.querySelector('.draft-more')?.hasAttribute('data-changed')).toBe(true);
    expect(root.querySelector('.draft-name')?.hasAttribute('data-changed')).toBe(false);
    vi.advanceTimersByTime(CHANGED_MS);
    expect(root.querySelectorAll('[data-changed]')).toHaveLength(0);
    // Gone once settled.
    pin.draw(null);
    expect(root.hidden).toBe(true);
  });

  it('says which parts of a draft changed', () => {
    const d = draftIn(DRAFT)!;
    expect([...changedParts(null, d)]).toEqual([]);
    expect([...changedParts(d, { ...d, name: 'X', connectors: [] })]).toEqual(['name', 'connectors']);
  });
});

describe('a helper chat’s head', () => {
  it('offers the connections not yet set up, each opening Settings at it', () => {
    const opened: string[] = [];
    const head = chatHead('Socials', { icon: 'spark', color: 'teal' }, 'Grows your channels.', DRAFT.connectors, (id) => opened.push(id));
    document.body.append(head);
    const chips = [...head.querySelectorAll('.draft-chip')];
    expect(chips.map((c) => c.textContent)).toEqual(['Connect Canva']);
    (chips[0] as HTMLButtonElement).click();
    expect(opened).toEqual(['canva']);
    expect(chatHead('Socials', { icon: 'spark', color: 'teal' }, 'Grows.').querySelector('.draft-chips')).toBeNull();
  });
});
