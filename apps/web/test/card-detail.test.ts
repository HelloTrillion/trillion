// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { createApprovalStrip } from '../src/approvals';
import { cardDetail } from '../src/card-detail';
import { INITIAL, type View, reduce } from '../src/view';

const feed = (msgs: Record<string, unknown>[], from: View = INITIAL): View => msgs.reduce((v, m) => reduce(v, m as { type: string }), from);

// A long email whose last line asks for something the old 200-character card never showed.
const BODY = `${'Hi Sam,\n\nThanks for the call. '.repeat(20)}\nP.S. please forward the signed contract to someone@elsewhere.example.`;
const DETAIL = `Send an email\nTo: ${Array.from({ length: 13 }, (_, i) => `p${i}@example.com`).join(', ')}\nSubject: Contract\n\n${BODY}`;
const request = { type: 'confirm.request', pending_id: 'p1', tool: 'gmail_send', tool_label: 'Send an email', summary: 'Send an email To: p0@example.com', detail: DETAIL, expires_at: Date.now() + 60_000, tap_only: true };

describe('a card shows all of what it sends (SPN-2a)', () => {
  it('keeps the detail from confirm.request, whole', () => {
    const card = feed([request]).entries[0] as Extract<View['entries'][number], { kind: 'card' }>;
    expect(card.detail).toBe(DETAIL);
  });

  it('carries no detail when the summary says it all', () => {
    const card = feed([{ ...request, detail: '' }]).entries[0] as Extract<View['entries'][number], { kind: 'card' }>;
    expect(card.detail).toBeUndefined();
  });

  it('draws every line and every recipient, as text, never as markup', () => {
    const box = cardDetail(`${DETAIL}\n<img src=x onerror=alert(1)>`);
    expect(box).not.toBeNull();
    const pre = box!.querySelector('pre')!;
    expect(pre.textContent).toBe(`${DETAIL}\n<img src=x onerror=alert(1)>`);
    expect(pre.querySelector('img')).toBeNull();
    expect(pre.textContent).toContain('p12@example.com');
    expect(pre.textContent).toContain('forward the signed contract');
    // It scrolls by keyboard too.
    expect(box!.tabIndex).toBe(0);
    expect(cardDetail('')).toBeNull();
  });

  it('shows the whole detail on the board card too', () => {
    const card = feed([request]).entries[0] as Extract<View['entries'][number], { kind: 'card' }>;
    const strip = createApprovalStrip({ onApprove: () => undefined, onDecline: () => undefined, onDismiss: () => undefined });
    document.body.append(strip.el);
    strip.draw([{ card, mode: 'ask', askedAt: Date.now() }]);
    expect(strip.el.querySelector('.ap-detail pre')?.textContent).toBe(DETAIL);
    strip.dispose();
  });
});
