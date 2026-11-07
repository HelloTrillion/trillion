# Confirmations: the decision record, on a page

**Date:** 2026-11-07 · **Requirements:** SUR-7, SPN-12 (viewable), SPN-17, SPN-17a, SPN-17c · **Phase 0 item:** 14 · **Builds on:** [the decision record, read back](2026-10-15-decision-record.md), [no double-send](2026-11-02-no-double-send.md)

Every action that asks for the user's yes is already recorded: what was asked, when its card was shown, who answered and how, and what happened. She can read it back (`decision_record`). The user could not, except by asking her. "Did that ever reach me?" should have a page that answers it without her.

## Decisions

1. **A Settings section, "Confirmations", under You.** It sits beside Reminders, which is the same kind of thing: a list of what happened, read from the core.
2. **It only reads.** There is no Approve or Decline anywhere on it. A card is approved on the card, in the conversation that raised it (SPN-14). A row still waiting has **Go to its card**, which opens that conversation and nothing more.
3. **Every line comes from the record.** The name is the host's friendly name for the tool and the summary is the host's frozen summary (SPN-2a). The model writes neither.
4. **Newest first, filtered by status and by date.** Status chips (All, Waiting, Done, Failed, Unknown, Declined, Expired, Cancelled, plus Approved and Running while any are) and a window (today, 7, 30, 90 or 365 days).
5. **Unknown stands out.** A row whose outcome is unknown is marked in the warning colour, and a line at the top says how many there are: it may or may not have happened, and nothing will retry it blind (SPN-17a, OPS-1).
6. **Only the slim arguments.** The exact request is shown under a fold, from the frozen slim copy: long values are their SHA-256 and length (SPN-17c). The full arguments are never read for this page, so nothing kept for 30 days and then cleared leaks through it.

## What the user sees

Each row:

- the tool's friendly name ("Create a file") and its summary;
- a status: Waiting, Approved, Running, Done, Failed, Unknown, Declined, Expired or Cancelled;
- **Asked**: when it was proposed;
- **Shown**: when its card was on screen, or "Never shown";
- **Answered**: when, how (a tap on the card, spoken, typed, a message), and by whom (the conversation that asked, or Trillion itself for an expiry);
- **Outcome**: done, failed or unknown with the recorded reason, or why it was cancelled (replaced by a newer request, what it would change had changed, or a check after a crash proved nothing went out);
- for a waiting row, **Go to its card**.

## How

- **Lifecycle.** The stored status maps to what the user reads: `proposed` is Waiting until it expires and Expired after; `superseded` and `invalidated` are Cancelled, with the reason; the rest keep their name.
- **Store.** `SqliteSpineStore.confirmations` reads the owner's actions in a window, newest first, with their lifecycle computed in SQL so the filter and the limit agree. It reads the frozen slim `args_json` and never `pending_action_args`. Deliveries and decisions for the page of rows come in two more queries. `confirmation_counts` gives the count for each status.
- **API.** `GET /api/confirmations?status=&days=&limit=`, behind the session token like every `/api/` route (SPN-11). It has no other method. "Answered" is the first decision after the proposal; "finished" is the last one once the action is final.
- **Page.** `apps/web/src/confirmations.ts`: the words are pure functions, tested on their own; the section reads the API again every 15 seconds while open.

## Checked

- `tests/api/test_confirmations_api.py`: the token is needed; a card approved by tap reads shown, answered by tap in the conversation that asked, done; every lifecycle state, including unknown, expired by time, superseded and not sent; the status filter, the window, the limit and the counts; long arguments arrive slim; and every write method on the route is refused.
- `apps/web/test/confirmations.test.ts`: the words for each status and channel; unknown flagged; the filter chips; Go to its card opens the conversation; the section only ever sends GET and has no Approve or Decline.

## Not now

- Escalating unseen decisions over time (the hour, the daily digest, 48 hours).
- The full tool audit (SPN-12) on a page of its own. This page covers the actions that asked first.
- A link from her answer to "did that ever reach me?" straight to the row.
