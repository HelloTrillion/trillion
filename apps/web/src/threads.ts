/**
 * The thread list (SUR-1). The pure part, grouping by day, is tested on its own;
 * the DOM part below it only draws and calls the API.
 */

export interface Thread {
  readonly id: string;
  readonly title: string | null;
  readonly lastMessageAt: number;
}

export interface Group {
  readonly label: string;
  readonly threads: readonly Thread[];
}

const DAY = 86_400_000;

function midnight(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

export function groupByDay(threads: readonly Thread[], now: number): Group[] {
  const today = midnight(now);
  const yesterday = midnight(today - 1);
  const week = today - 7 * DAY;
  const buckets: [string, Thread[]][] = [['Today', []], ['Yesterday', []], ['Previous 7 days', []], ['Older', []]];
  for (const t of threads) {
    const i = t.lastMessageAt >= today ? 0 : t.lastMessageAt >= yesterday ? 1 : t.lastMessageAt >= week ? 2 : 3;
    buckets[i]![1].push(t);
  }
  return buckets.filter(([, list]) => list.length > 0).map(([label, list]) => ({ label, threads: list }));
}

/** What a row shows when a conversation has no title yet. */
export const untitled = (t: Thread): string => t.title?.trim() || 'New conversation';
