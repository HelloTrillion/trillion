/**
 * Her helpers in the conversation (the your helpers spec): her offer of one, as a line
 * with Create and No thanks, and the head of a helper's own chat. Every word is set as
 * text, never parsed as markup.
 */

import { connectionChip } from './draft';
import { type Look, picture } from './look';
import type { DraftConnection, Entry } from './view';

export type Offer = Extract<Entry, { kind: 'offer' }>;

// The core's area names a user reads differently (`helpers/needs.py`, label()).
const AREA_WORDS: Readonly<Record<string, string>> = { slack: 'Slack' };
const MCP_AREA = 'mcp:';

/** An area as the user reads it: an MCP server by its name. */
export function areaWords(area: string): string {
  if (area.startsWith(MCP_AREA)) return area.slice(MCP_AREA.length);
  return AREA_WORDS[area] ?? area;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

/**
 * "Want a helper for email? Inbox: Keeps your inbox tidy", with Create and No thanks.
 * Once tapped it is one short line: what came of it.
 */
export function offerLine(e: Offer, choose: (choice: 'create' | 'decline') => void, by = ''): HTMLElement {
  const line = el('div', `notice offer-line ${e.status}`);
  const question = `Want a helper for ${areaWords(e.area)}? ${e.name}: ${e.job}`;
  // Her idea, not the user's request: said so, so it is never taken for theirs.
  const from = el('p', 'offer-from', by ? `Suggested by ${by}` : 'Suggested');
  // A newer offer came: this one is only what she asked, with nothing to tap.
  if (e.status === 'past') {
    line.append(from, el('p', 'offer-words', question));
    return line;
  }
  if (e.status !== 'open') {
    const said = { creating: `Making ${e.name}.`, made: `Made ${e.name}.`, declined: 'Okay.', failed: e.note }[e.status];
    line.textContent = said;
    return line;
  }
  line.append(from, el('p', 'offer-words', question));
  const row = el('div', 'offer-actions');
  const create = el('button', 'approve', 'Create');
  create.type = 'button';
  create.addEventListener('click', () => choose('create'));
  const no = el('button', 'quiet', 'No thanks');
  no.type = 'button';
  no.addEventListener('click', () => choose('decline'));
  row.append(create, no);
  line.append(row);
  return line;
}

/**
 * The top of a helper's own chat: its picture, its name and its job, and the connections
 * its card suggested that are not set up yet, each opening Settings at it.
 */
export function chatHead(
  name: string,
  look: Look,
  job: string,
  toConnect: readonly DraftConnection[] = [],
  onConnect: (catalogId: string) => void = () => undefined,
): HTMLElement {
  const head = el('div', 'helper-head');
  const words = el('div', 'helper-head-words');
  words.append(el('p', 'helper-head-name', name), el('p', 'helper-head-job', job));
  const missing = toConnect.filter((c) => !c.connected);
  if (missing.length) {
    const row = el('div', 'draft-chips helper-head-chips');
    row.setAttribute('aria-label', 'Connections it would use');
    for (const c of missing) row.append(connectionChip(c, onConnect));
    words.append(row);
  }
  head.append(picture(look, 44, name), words);
  return head;
}
