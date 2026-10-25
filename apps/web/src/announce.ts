/**
 * What a hidden sidebar would have said (SUR-6). Hiding it makes it inert, which
 * silences its status and its live log, so a separate polite region outside it
 * carries the same news. Pure, so every case can be tested; only used while hidden,
 * so nothing is announced twice.
 */
import { STATE_TEXT, type View } from './view';

/** The words in the sidebar's status. */
export function statusText(view: View): string {
  if (view.link === 'ready') return view.noticeSpeaking ? 'Speaking' : STATE_TEXT[view.orb];
  return view.link === 'connecting' ? 'Connecting' : 'Offline';
}

/** The line to announce for the step from `prev` to `next`, or null for nothing new. */
export function announcement(prev: View, next: View): string | null {
  const lines: string[] = [];
  // One entry added on the end, the rest untouched: not a history load or a reset.
  const added =
    next.entries.length === prev.entries.length + 1 && prev.entries.every((e, i) => e === next.entries[i])
      ? next.entries[next.entries.length - 1]
      : undefined;
  // What she heard: a typed message waits as `pending`, a heard one never does.
  if (added?.kind === 'user' && next.pending === null && added.text) lines.push(`You said: ${added.text}`);
  // Her reply is finished once it stops being the open one.
  if (prev.replyOpen && !next.replyOpen) {
    const reply = [...next.entries].reverse().find((e) => e.kind === 'assistant');
    if (reply?.kind === 'assistant' && reply.text) lines.push(reply.text);
  }
  const status = statusText(next);
  if (status !== statusText(prev)) lines.push(status);
  return lines.length ? lines.join('. ') : null;
}
