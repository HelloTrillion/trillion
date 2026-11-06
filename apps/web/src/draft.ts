/**
 * Her draft of a new helper (the your helpers spec, Creating one): one card, pinned above
 * the message box while it waits, so talking on never scrolls it away. When she drafts
 * again, the same card shows the new draft, and what changed glows for a moment.
 *
 * Create and Not now are the card's Approve and Decline: they send the same `confirm`
 * and `cancel` as every card, so nothing here is a new way to approve. Every word is set
 * as text, never parsed as markup.
 */

import { type Look, picture } from './look';
import type { DraftConnection, Entry, HelperDraft } from './view';

export type DraftCard = Extract<Entry, { kind: 'card' }> & { readonly draft: HelperDraft };

/** How long a fresh draft's buttons wait before they take a click. */
export const ARRIVE_MS = 600;
/** How long what changed stays marked. */
export const CHANGED_MS = 1600;
/** Notes longer than this are clamped, with More. */
export const NOTES_CLAMP = 160;

const FALLBACK: Look = { icon: 'spark', color: 'blue' };

export interface DraftPinOptions {
  readonly onCreate: (pendingId: string) => void;
  readonly onNotNow: (pendingId: string) => void;
  /** Open Settings, Connected tools, at this catalog entry. */
  readonly onConnect: (catalogId: string) => void;
}

export interface DraftPin {
  readonly el: HTMLElement;
  /** Show this draft, or none. The same card is kept and filled again. */
  draw(card: DraftCard | null): void;
  /** Bring it into view and focus it, if it is showing. */
  focus(): boolean;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** What differs between two drafts, by part of the card. */
export function changedParts(before: HelperDraft | null, after: HelperDraft): Set<string> {
  const out = new Set<string>();
  if (!before) return out;
  if (before.name !== after.name) out.add('name');
  if (before.job !== after.job) out.add('job');
  if (before.notes !== after.notes) out.add('notes');
  const ids = (cs: readonly DraftConnection[]): string => cs.map((c) => `${c.id}:${c.connected}`).join(',');
  if (ids(before.connectors) !== ids(after.connectors)) out.add('connectors');
  if (before.asks.join('\n') !== after.asks.join('\n')) out.add('asks');
  return out;
}

/** A suggested connection: one to connect is a button that opens it in Settings. */
export function connectionChip(c: DraftConnection, onConnect: (id: string) => void): HTMLElement {
  if (c.connected) {
    const chip = el('span', 'draft-chip connected', c.title);
    chip.title = `${c.title} is connected`;
    return chip;
  }
  const chip = el('button', 'draft-chip', `Connect ${c.title}`);
  chip.type = 'button';
  chip.addEventListener('click', () => onConnect(c.id));
  return chip;
}

export function createDraftPin(o: DraftPinOptions): DraftPin {
  const root = el('section', 'draft-pin');
  root.setAttribute('aria-label', 'New helper, waiting for your OK');
  root.tabIndex = -1;
  root.hidden = true;
  // Said once per new draft: a screen reader hears that it came, or changed.
  const live = el('p', 'visually-hidden');
  live.setAttribute('aria-live', 'polite');
  const body = el('div', 'draft-body');
  root.append(live, body);

  let shown: DraftCard | null = null;
  let expanded = false;
  let marks: ReturnType<typeof setTimeout> | null = null;

  function fill(card: DraftCard, changed: Set<string>): void {
    const d = card.draft;
    const part = (node: HTMLElement, key: string): HTMLElement => {
      node.dataset['part'] = key;
      if (changed.has(key)) node.dataset['changed'] = '';
      return node;
    };

    const head = el('header', 'draft-head');
    const words = el('div', 'draft-words');
    words.append(el('p', 'draft-eyebrow', 'New helper · draft'), part(el('h3', 'draft-name', d.name), 'name'));
    head.append(picture(card.look ?? FALLBACK, 44, d.name), words);
    const parts: HTMLElement[] = [head, part(el('p', 'draft-job', d.job), 'job')];

    if (d.notes) {
      const notes = part(el('div', 'draft-notes'), 'notes');
      const text = el('p', 'draft-notes-text', d.notes);
      notes.append(text);
      if (d.notes.length > NOTES_CLAMP) {
        text.toggleAttribute('data-clamped', !expanded);
        const more = el('button', 'draft-more', expanded ? 'Less' : 'More');
        more.type = 'button';
        more.setAttribute('aria-expanded', String(expanded));
        more.addEventListener('click', () => {
          expanded = !expanded;
          text.toggleAttribute('data-clamped', !expanded);
          more.textContent = expanded ? 'Less' : 'More';
          more.setAttribute('aria-expanded', String(expanded));
        });
        notes.append(more);
      }
      parts.push(notes);
    }
    if (d.connectors.length) {
      const row = part(el('div', 'draft-chips'), 'connectors');
      row.setAttribute('aria-label', 'Connections it would use');
      for (const c of d.connectors) row.append(connectionChip(c, o.onConnect));
      parts.push(row);
    }
    if (d.asks.length) {
      const asks = part(el('div', 'draft-asks'), 'asks');
      const list = el('ul', 'draft-asks-list');
      for (const a of d.asks) list.append(el('li', '', a));
      asks.append(el('p', 'draft-asks-head', 'It will ask you first'), list);
      parts.push(asks);
    }

    const row = el('div', 'draft-actions');
    const create = el('button', 'approve', 'Create');
    const notNow = el('button', 'quiet', 'Not now');
    create.type = notNow.type = 'button';
    // Not at once: a draft that changes under the pointer is never approved by accident.
    create.disabled = notNow.disabled = true;
    setTimeout(() => {
      if (shown?.pendingId === card.pendingId) create.disabled = notNow.disabled = false;
    }, ARRIVE_MS);
    const press = (send: (id: string) => void) => (): void => {
      create.disabled = notNow.disabled = true;
      send(card.pendingId);
    };
    create.addEventListener('click', press(o.onCreate));
    notNow.addEventListener('click', press(o.onNotNow));
    row.append(create, notNow, el('span', 'draft-hint', 'Or tell her what to change'));
    parts.push(row);
    body.replaceChildren(...parts);
  }

  return {
    el: root,
    draw(card) {
      if (card === null) {
        shown = null;
        expanded = false;
        root.hidden = true;
        body.replaceChildren();
        return;
      }
      if (shown?.pendingId === card.pendingId) return;
      const changed = changedParts(shown?.draft ?? null, card.draft);
      const replacing = shown !== null;
      shown = card;
      fill(card, changed);
      root.hidden = false;
      live.textContent = replacing ? `Draft updated: ${card.draft.name}` : `New helper draft: ${card.draft.name}. Create or Not now.`;
      if (marks !== null) clearTimeout(marks);
      marks = changed.size
        ? setTimeout(() => {
            for (const n of root.querySelectorAll('[data-changed]')) n.removeAttribute('data-changed');
          }, CHANGED_MS)
        : null;
    },
    focus() {
      if (root.hidden) return false;
      root.scrollIntoView({ block: 'nearest' });
      root.focus({ preventScroll: true });
      return true;
    },
  };
}
