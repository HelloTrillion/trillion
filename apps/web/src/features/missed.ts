/**
 * Missed approvals under Needs you (SPN-17, SPN-2b, SUR-7): each card that expired before
 * the user answered it, from the last 7 days, sent by the core on the page's hello. One line
 * each: the card's summary and when it expired, with Ask again and Dismiss. Once either is
 * done it never shows again.
 *
 * These lines are the app's. Nothing here is spoken, announced or saved as hers, and nothing
 * reaches her: Ask again is the user's own tap, which the core turns into a fresh card with
 * fresh checks in the conversation the card was raised in (so the page opens that one first).
 * A card that can't be asked again (her browser's, whose task ended) offers only Dismiss and
 * says why. Every piece of text is set with textContent, never parsed as markup.
 */

import type { Feature, Message } from '../features';
import { timeText } from '../helpers';
import './missed.css';

export interface MissedItem {
  readonly pending_id: string;
  readonly tool_label: string;
  readonly summary: string;
  readonly expired_at: number;
  readonly shown: boolean;
  /** Where Ask again raises the fresh card; null when it can't be asked again. */
  readonly conversation_id: string | null;
  /** Why it can't be asked again, in plain words; null when it can. */
  readonly why_not: string | null;
}

export interface MissedDeps {
  /** Sends one message to the session; false when it is not connected. */
  readonly send: (body: Record<string, unknown>) => boolean;
  /** Open a conversation: Ask again raises its card where the card was asked. */
  readonly open: (id: string) => void;
  readonly now: () => number;
}

export interface Missed {
  readonly element: HTMLElement;
  /** A message from the core: `session`, `unseen` and `unseen.done` are taken in. */
  handle(msg: Message): void;
  /** The ids listed now, in order. */
  readonly ids: readonly string[];
}

const NOT_CONNECTED = 'Not connected. Try again in a moment.';

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text = ''): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text) node.textContent = text;
  return node;
}

/** The items in an `unseen` message, keeping only well-formed ones. */
export function parseItems(raw: unknown): MissedItem[] {
  if (!Array.isArray(raw)) return [];
  const str = (v: unknown): v is string => typeof v === 'string';
  return raw.filter(
    (i): i is MissedItem =>
      typeof i === 'object' &&
      i !== null &&
      str(i.pending_id) &&
      i.pending_id !== '' &&
      str(i.summary) &&
      str(i.tool_label) &&
      typeof i.expired_at === 'number' &&
      (i.conversation_id === null || str(i.conversation_id)) &&
      (i.why_not === null || str(i.why_not)),
  );
}

/** When it expired, as the line says it: "Expired 9:55", "Expired Thu 7:00". */
export function expiredText(item: Pick<MissedItem, 'expired_at' | 'shown'>, now: number): string {
  const at = `Expired ${timeText(item.expired_at, now)}`;
  return item.shown ? at : `${at}, never shown`;
}

interface Row {
  readonly li: HTMLLIElement;
  readonly note: HTMLElement;
  readonly buttons: HTMLButtonElement[];
}

export function createMissed(d: MissedDeps): Missed {
  const element = el('section', 'missed');
  const title = el('h2', 'missed-title', 'Needs you');
  title.id = 'missed-title';
  element.setAttribute('aria-labelledby', title.id);
  const list = el('ul', 'missed-list');
  element.append(title, list);
  element.hidden = true;

  let items: MissedItem[] = [];
  const rows = new Map<string, Row>();
  // Asked again or dismissed here: never listed again, whatever a later hello sends.
  const gone = new Set<string>();
  // The conversation this page has open, and an Ask again waiting for its conversation.
  let here: string | null = null;
  let queued: string | null = null;

  function sendFor(item: MissedItem, type: 'unseen.ask' | 'unseen.dismiss'): void {
    const row = rows.get(item.pending_id);
    if (!row) return;
    row.note.textContent = '';
    if (!d.send({ type, pending_id: item.pending_id })) {
      row.note.textContent = NOT_CONNECTED;
      return;
    }
    for (const b of row.buttons) b.disabled = true;
  }

  function askAgain(item: MissedItem): void {
    const where = item.conversation_id;
    if (where === null) return;
    if (where !== here) {
      // Its card is raised where it was asked: that conversation opens, then it is asked.
      queued = item.pending_id;
      d.open(where);
      return;
    }
    sendFor(item, 'unseen.ask');
  }

  function makeRow(item: MissedItem): Row {
    const li = el('li', 'missed-row');
    li.dataset['id'] = item.pending_id;
    const line = el('p', 'missed-line');
    line.append(el('span', 'missed-what', item.summary), el('span', 'missed-when', expiredText(item, d.now())));
    const note = el('p', 'missed-note');
    if (item.why_not) note.textContent = item.why_not;
    const actions = el('div', 'missed-actions');
    const buttons: HTMLButtonElement[] = [];
    if (item.conversation_id !== null && item.why_not === null) {
      const ask = el('button', 'missed-ask', 'Ask again');
      ask.type = 'button';
      ask.addEventListener('click', () => askAgain(item));
      buttons.push(ask);
    }
    const dismiss = el('button', 'quiet missed-dismiss', 'Dismiss');
    dismiss.type = 'button';
    dismiss.addEventListener('click', () => sendFor(item, 'unseen.dismiss'));
    buttons.push(dismiss);
    actions.append(...buttons);
    li.append(line, note, actions);
    return { li, note, buttons };
  }

  function draw(): void {
    for (const [id, r] of rows) {
      if (items.some((i) => i.pending_id === id)) continue;
      r.li.remove();
      rows.delete(id);
    }
    items.forEach((item, i) => {
      let r = rows.get(item.pending_id);
      if (!r) {
        r = makeRow(item);
        rows.set(item.pending_id, r);
      }
      const there = list.children[i] ?? null;
      if (there !== r.li) list.insertBefore(r.li, there);
    });
    element.hidden = items.length === 0;
  }

  function take(next: MissedItem[]): void {
    items = next.filter((i) => !gone.has(i.pending_id));
    draw();
    if (queued === null) return;
    const waiting = items.find((i) => i.pending_id === queued);
    if (waiting && waiting.conversation_id === here) {
      queued = null;
      sendFor(waiting, 'unseen.ask');
    } else if (!waiting) queued = null;
  }

  function settled(id: string, outcome: string, text: string | null): void {
    const row = rows.get(id);
    if (outcome === 'refused') {
      // Not asked again: it stays, saying why.
      if (!row) return;
      for (const b of row.buttons) b.disabled = false;
      row.note.textContent = text ?? '';
      return;
    }
    gone.add(id);
    items = items.filter((i) => i.pending_id !== id);
    draw();
  }

  return {
    element,
    handle(msg) {
      if (msg.type === 'session') here = typeof msg['id'] === 'string' ? msg['id'] : null;
      else if (msg.type === 'unseen') take(parseItems(msg['items']));
      else if (msg.type === 'unseen.done' && typeof msg['pending_id'] === 'string') {
        const text = typeof msg['text'] === 'string' ? msg['text'] : null;
        settled(msg['pending_id'], String(msg['outcome'] ?? ''), text);
      }
    },
    get ids() {
      return items.map((i) => i.pending_id);
    },
  };
}

export const feature: Feature = {
  setup({ send, openConversation, on }) {
    const missed = createMissed({ send, open: openConversation, now: () => Date.now() });
    // Above the conversation, in the sidebar: never inside the transcript, which is hers.
    document.getElementById('transcript')?.before(missed.element);
    on((msg) => missed.handle(msg));
  },
};
