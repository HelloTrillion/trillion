# Background reads

**Date:** 2026-10-27 · **Requirements:** VOI-17, SUR-9, SPN-5, MEM-5 · **Builds on:** the door (`delivery/door.py`), live widgets, routines' ready line

Ask "pull my Stripe numbers for the year" and today the whole conversation waits:
she says "Pulling it now", then the page shows "Thinking…" and anything else the user
says is turned away as busy until the read comes back. A slow read now runs in the
background instead. Her turn ends with her acknowledgement, the user keeps talking
with her, and at the next pause she brings the result in: "Your Stripe numbers are
in", the board, two sentences.

## Decisions

| | Decision | Why |
|---|---|---|
| D1 | **Slow reads only.** A call goes to the background when its tool is READ, runs without a card, and reads from outside: an MCP, Google or Slack tool (which includes `stripe_revenue`), or `web_fetch`. Her own quick tools (memory, board, schedules, rules, day, routines), `file_read` (on this computer, and quick) and anything gated or writing stay in the turn. | What the user chose. Writes and approvals keep their exact order and cards. |
| D2 | **At the next pause.** The result is brought in by a follow-up turn that starts only when the door would deliver a notice (`free()`): no turn running, nobody listening, nothing of hers still being heard. Never over the user, never over her own reply. | What the user chose; VOI-17. |
| D3 | **A job is a host note, not a second turn.** The model's call gets an immediate answer ("started in the background"), so every tool use is paired at once. The real result arrives later as a host-authored message, like a tapped card's result. Two turns never run at once in one conversation. | Keeps the history well formed and the session's one-turn-at-a-time rule. |

## What happens

1. **The call.**
   - `run_turn` asks the session, through a new `background(use, definition)` hook, whether this call goes to the background (D1, and fewer than **3** jobs already running for this conversation).
   - If so, the session starts a job and the call is answered at once with `{"status": "started", "job": "<id>", "detail": "Running in the background; you will be told when it is in. Do not call it again; say it is on its way if asked."}`.
   - The page gets `tool.end` with `summary: "background"` and the chip reads "working in background".
   - Other calls in the same round run as today.
2. **The turn ends** as soon as she has nothing else to call. She has already said her one line ("Pulling your Stripe numbers now."), so the session is free and the user can talk.
3. **The job** runs on the runtime (not the turn):
   - through the router, with the `ToolContext` captured at the call (same turn number, via and owner);
   - with the tool's own timeout;
   - its reads recorded as a turn's are, for live widgets.
   - A job is cancelled if its conversation is deleted, and when the runtime stops.
4. **When it ends,** the result, wrapped as untrusted as always, becomes a host message in that conversation. With a page open on it, the note is held in memory until the pause, so it is never written into the middle of a turn, and it is saved if the page closes first. With no page, it is saved at once:
   - `[host] The background read you started (<tool>, job <id>) is in. Result: <response JSON>`;
   - kind `tap`, with a `BACKGROUND` marker;
   - shown on reload as a small host line "Stripe numbers are in" (the tool's connector title, or its label).
5. **The follow-up turn.**
   - If a page is open on that conversation, the note is queued and a turn begins as soon as `free()` holds. Until then it waits, checked on each state change, as the door does, and once more when the door's playback grace runs out for a page that never says it played her reply.
   - She is told, in the note, to say in one short line that it's in, put it on the board if it is better seen, and say at most two sentences on what matters.
   - The follow-up turn counts as having read outside content (MEM-5): no reminders or memories as the user's words from it.
   - It is never a spoken yes, and can approve nothing (via `ask`, like a widget's ask).
6. **No page on that conversation** (the user switched away):
   - the note is saved as above;
   - a `ready` notice ("Your Stripe numbers are in", with Open) goes through the door to whichever page is open;
   - the follow-up turn runs when that conversation is next opened and free.
7. **Failure or timeout:** the note says so. In the follow-up she says plainly, once, that the read didn't come back, and offers to try again.

## While it runs

- Her per-turn context lists jobs still running: `background: stripe_revenue (job 3f2a) running 12 s`. Asked "is it in yet?", she says it's on its way, and she never starts the same read again while one is running (the hook refuses a duplicate tool+args with "already running as job …").
- The user can type, speak and tap as normal. A barge-in, stop or new message never cancels a job; "stop" stops her voice, not the read.
- A conversation with jobs running shows a small "1 in background" line under the input.

## Out of scope

- Moving a read to the background once it is already slow (a time-based switch).
- Writes or approved actions in the background.
- Notifications outside the app.

## Testing

- **Server:**
  - **What goes to the background:** D1, per tool family and risk; gated or write tools stay inline; three jobs max; a duplicate is refused.
  - **The immediate answer** pairs the tool use; the turn ends and the session is free (a new `turn.text` is accepted while the job runs).
  - **The job result** is saved as a host note and is never the user's words. The follow-up turn starts only when `free()` holds (not while listening, not while her reply is heard) and is marked as having read outside content.
  - **A failed job and a timeout** each come back as a note.
  - **No page:** a ready notice with Open.
  - **Ending:** conversation deleted → the job is cancelled; runtime close → jobs cancelled.
  - **Live widgets:** a widget from a background read gets `live`.
  - **The prompt line** is present.
- **Web:**
  - the chip's "working in background";
  - the "in background" line;
  - the host line on reload;
  - input stays enabled while a job runs.
- **Live** (the user's Claude preview): "pull my Stripe year to date", then talk about something else while it loads. She acknowledges at once, answers the side question, and when it's in she says so at the next pause, with the board.
