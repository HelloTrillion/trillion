# Reminders and timed asks

**Date:** 2026-10-24 · **Requirements:** AGT-4, AGT-8, AGT-9, SUR-9 (the door), VOI-17 · **Respects:** PER-11, SPN-2b, SPN-6, OPS-3, TESTING §4 and §5 · **Leads into:** AGT-3 (routines), AGT-5, AGT-6

"Remind me at four to call Sam" becomes a scheduled task, never a memory note
(AGT-9). So does "every weekday at 9, check my email and tell me what's urgent":
at the time, she runs the ask with her tools and tells the user what she found.
This is the first slice of routines. It builds the queue (AGT-4) and the one door
for delivery (SUR-9) that routines, alerts and sub-agents will use later.

## Decisions

| | Decision | Why |
|---|---|---|
| D1 | Two kinds: a **reminder** (her words, delivered as written) and a **timed ask** (a request she runs at the time). Each runs once or repeats. | What the user chose. Named routines and the Briefings inbox are the next slice (AGT-3). |
| D2 | A **scheduler inside the running server**, with the queue in SQLite. | The same on every platform, nothing to install, and the database is already there. If running only while Trillion runs proves to be a problem, an OS scheduler can wake it later. |
| D3 | **Late, and say so.** A missed reminder arrives when Trillion is back, marked with its due time. A missed timed ask runs late if it is less than **one hour** past due; older than that it is reported as missed instead of run stale. A repeating task fires **once** on catch-up, however many times it missed. | What the user chose (TESTING §4.1). |
| D4 | Away from Trillion, a notice shows as a **system notification** and is **held** for the next time the page opens. With a page open, it arrives in the conversation. | What the user chose. |
| D5 | **An unattended run gets only tools that never ask.** A tool that would show an approval card is not offered to it. She says what she would do; the user approves it the normal way, in their own conversation. | A card belongs to the session that raised it (`WRONG_SESSION`), and nobody is there to tap it. Leaving those tools out means nothing acts unattended that would have asked (SPN-2b, TESTING §4.3). |
| D6 | **Setting a timed ask asks first** (an approval card). Setting a reminder runs at once. | A timed ask is a future instruction that runs on its own: a page or an email must not be able to schedule one. A reminder only delivers words she wrote and is cheap to cancel. |
| D7 | **Missed and failed notices are not spoken.** They are shown and notified with the task's own title. | No stock speech (PER-11): nothing reaches the speaker that the model or the user did not write. |

## What the user sees

- **Asking.**
  - "Remind me at four to call Sam."
  - "Every weekday at 9, check my email and tell me what's urgent."
  - "What reminders do I have?", "Cancel the 9 o'clock one."

  She works out the exact time in the user's timezone (`USER.md`, from Names and tone; the computer's own if none is set) and confirms it: "4:00 pm today."
- **When it is due.**
  - **A page is open:** the notice arrives in the conversation as her message, spoken if a voice is on. It never arrives while she is replying (VOI-17); it waits, then arrives.
  - **No page is open:** the computer shows a notification, and the notice is held for the next time a page opens.
  - **Late:** a notice that arrives late carries its due time ("due 4:00 pm") beside it.
- **Settings → Reminders.**
  - What is coming up, each with its next time and how it repeats.
  - Recent results: delivered, late, missed, blocked or failed.
  - A cancel button on each.
  - When the scheduler last checked. If the scheduler stops, that time goes stale and the page says so (TESTING §4.5).

## Where things live

Migration `008_schedules.sql`:

- **`schedules`**: one row per task the user asked for.
  - Who and what: `id`, `owner_id`, `kind` (`reminder` | `ask`), `title` (short, for lists and missed notices), `body` (her words for a reminder, the request for an ask).
  - When: `zone` (IANA), `at_local` (the first time, local, `YYYY-MM-DDTHH:MM`), `repeat` (JSON, below), `next_due_ms` (UTC epoch ms, or NULL when finished).
  - State: `status` (`active` | `done` | `cancelled`), `created_ms`, `created_in` (the conversation it was set in).
- **`task_runs`**: the queue (AGT-4).
  - Which and when: `id`, `schedule_id`, `due_ms`, `claimed_by` (a per-process id), `claimed_ms`, `started_ms`, `finished_ms`, `timeout_s`.
  - `status`: `queued` | `running` | `delivered` | `late` | `missed` | `blocked` | `failed` | `interrupted`.
  - Outcome: `attempts`, `result_notice` (a `notices` id), `detail` (a short reason, never content).
  - At most one run per schedule per due time: a unique index on `(schedule_id, due_ms)`.
- **`notices`**: everything waiting to reach the user (SUR-9).
  - What: `id`, `owner_id`, `source` (`schedule`), `source_id`, `title`, `text` (her words; NULL for missed or failed), `due_ms`.
  - Delivery: `created_ms`, `status` (`held` | `delivered`), `delivered_ms`, `delivered_to` (a conversation id or `system`), `notified_ms` (when the system notification was shown).

`repeat` is one of:
- `{"every": "day"}`
- `{"every": "weekday"}`
- `{"every": "week", "days": ["mon","thu"]}`
- `{"every": "month", "day": 15}`

When `day` is past the month's end, it means the month's last day. A missing `repeat` means the task runs once.

## Pieces

### `scheduler/when.py`: times

- `parse_local(text, zone) -> datetime`: reads `YYYY-MM-DDTHH:MM` (seconds allowed) as a local time in `zone`.
  - A time that falls in the hour a spring change skips moves forward to the first real minute after it.
  - A time that occurs twice in autumn means the first one.
- `next_after(at_local, repeat, zone, after_ms) -> int | None`: the next occurrence strictly after `after_ms`, in UTC ms, or None when a one-off has passed.
  - It is computed from the local time each time, so 9:00 stays 9:00 across daylight-saving changes.
- Pure, no I/O.

### `scheduler/store.py`: the tables

An async store over the shared connection, like `SqliteConversationStore`:
- Schedules: create, cancel, list active, set `next_due_ms`.
- Runs:
  - Enqueue a run (insert-or-ignore on the unique index, so the same occurrence is never recorded twice).
  - Claim one atomically: `UPDATE … SET status='running', claimed_by=?, claimed_ms=? WHERE id = (SELECT id … WHERE status='queued' AND due_ms <= ? ORDER BY due_ms LIMIT 1) RETURNING *`.
  - Finish a run.
  - Reclaim: runs left `running` by another `claimed_by`, or past their timeout, become `interrupted` (AGT-4 dead-worker reclaim).
- Notices: add, list held, mark delivered (conditional on `status='held'`, so a notice is delivered once), mark notified.

### `scheduler/runner.py`: the loop

`Scheduler(store, clock, run_ask, door, tick_s=15)`, started and stopped with the runtime. Each tick:

1. Records `checked_ms`, which Settings shows.
2. **Enqueue:** for each active schedule with `next_due_ms <= now`:
   - It enqueues one run at that due time.
   - It advances `next_due_ms` with `next_after(…, now)`, which skips every occurrence already past. Catching up therefore enqueues one run, never one per missed time (D3).
   - A one-off becomes `done` once its run is enqueued.
3. **Claim and run** due runs one at a time:
   - **Reminder:** a notice with `text = body`. The status is `delivered`, or `late` when `now - due_ms > 120 s`.
   - **Timed ask, more than 1 h past due:** `missed`, and a notice with no text.
   - **Timed ask, no model available:** `blocked`. The run goes back to `queued`, to be tried on later ticks; once it is more than 1 h past due, it becomes `missed`.
   - **Timed ask, otherwise:** `run_ask` (below), with `timeout_s = 120`.
     - It completed: a notice with her reply.
     - It timed out or raised: `failed`, a notice with no text, and `detail` saying why.
     - It is never retried silently (AGT-8).
4. **Reclaim at startup:** a run left `running` by a previous process is `interrupted`.
   - It is enqueued once more, attempt 2, if it is still within the late window.
   - Otherwise it becomes `missed`.
   - Unattended tools never ask, and every tool they have only reads or writes something the user can undo. A second attempt is safe, and the second attempt is recorded.

The runner never touches a surface. It only writes notices (SUR-9).

### `scheduler/ask.py`: running a timed ask unattended

`run_ask(schedule) -> str | None`:

- **The run's context.** It opens a background conversation (`kind = scheduled`, kept and not shown in the chat list) and calls `brain.loop.run_turn` with:
  - the same system blocks as a live turn, plus a short overlay `persona/prompts/scheduled.md`. The overlay tells her the user is away, and that she should do the ask with the tools she has, say plainly what she would do with any tool she does not have, and keep it short, because it is read on arrival;
  - `ToolContext.via = "schedule"`, a new value of `Via`.
- **Tools that never ask.** The registry passed in is a read-only view, `registry.unattended()`, that lists only tools the router would run without a card. That means:
  - `READ`, plus `WRITE` tools the user has not set to ask;
  - no `IRREVERSIBLE` or `SPEND` tool;
  - no MCP or Google tool the user left on "asks first";
  - no meta tools that approve or deny.
- **The router enforces it too.** For `via == "schedule"` it refuses any call that would create a pending card, so a tool missing from the view can't be called anyway.
- **The result** is her final text. An empty reply means there is nothing to deliver.
- **Untrusted content** stays wrapped as always (SPN-6). What she read while unattended is recorded as read, never as the user's words (MEM-5).
- **Blocked:** `NoModel` is raised when there is no provider, or when the provider reports `unreachable` or `unauthorized` before the first token. The runner treats it as blocked (TESTING §4.2).

### `delivery/door.py`: the one door (SUR-9)

`Door(store, sessions, notify, clock)` is the only component that turns a notice into something the user sees or hears.

- **When a notice is added,** it is delivered to the live page if there is one and it is free. Otherwise the system notification is shown once (`notified_ms`), and the notice is held.
  - **The live page:** the most recently active connected session.
  - **Free:** no reply is in flight, judged from facts, as VOI-17 says: the session's turn task is not running and its audio is not playing.
- **When a session becomes free, or a page opens,** every held notice is delivered to it, oldest first, each marked delivered (conditional update) before it is sent.
- **Delivering to a page** appends an assistant message to that conversation through the session, saved like any other message, so it survives a reload. It is sent as a `notice` event: `{id, title, text, due_ms, late, kind}`.
  - When `text` is set and a voice is on, she speaks it through the normal speech path.
  - When `text` is NULL (missed or failed), the page shows the title and the state, and nothing is spoken.
- **Nothing else holds a route to a surface.** A test asserts that the scheduler package imports neither `api.session` nor the notifier. Only the door imports them.

### `delivery/notify.py`: system notifications

`notify(title, body) -> bool`, best effort:

- **macOS:** `osascript -e 'display notification … with title …'`, with the arguments passed as AppleScript strings, quoted safely.
- **Linux:** `notify-send`, if it is on the PATH.
- **Windows:** a PowerShell toast through `Windows.UI.Notifications`, launched with `CREATE_NO_WINDOW` so no console flashes (OPS-3).
- **On every platform:**
  - The arguments are passed as an argument list, never through a shell.
  - The call times out after 5 s.
  - A failure is logged by its kind only and returns False. The notice stays held either way.
  - Text is cut to 200 characters.
  - Her words are shown only when the task was a reminder, which she wrote for this. A timed ask's notification shows the title and "ready in Trillion", so that email content never lands on the lock screen.

### The tools (`tools/schedule.py`)

- **`reminder_set(when, say, title, repeat?)`: `Risk.WRITE`, runs at once.**
  - It refuses a time that isn't in the future (allowing for clock drift up to 60 s), a `say` over 500 characters, and a `repeat` it can't read.
  - It returns the exact time it understood in words ("Fri 24 Oct, 4:00 pm") and the schedule id.
  - It refuses after the turn has read outside content (`ctx.read_outside`): "set it again once you've told the user what it's for". A page or an email can't plant a reminder that speaks in her voice.
- **`ask_schedule(when, ask, title, repeat?)`: gated, so it shows a card** (D6).
  - The summary reads "Every weekday at 9:00 am: check my email and tell me what's urgent".
  - The same checks as `reminder_set`, with `ask` up to 1000 characters.
  - It is declared `irreversible`, because setting it is a standing instruction. That gives a card that a yes or a tap approves.
- **`schedule_list()`: `READ`.** What is active, with next times, and the last result of each.
- **`schedule_cancel(id)`: `WRITE`.** It cancels one, and queued runs for it become `cancelled`.

`when` is a local date and time, `YYYY-MM-DDTHH:MM`. The current local time and zone are already in her volatile context, so she computes "at four" and "in 20 minutes" herself, and the tool checks them. The prompt tells her to confirm the time it returns.

### API and Settings

- **`GET /api/schedules`:** active schedules (title, kind, next time, repeat in words), the last 20 results, and `checked_ms`. Behind the session token.
- **`DELETE /api/schedules/{id}`:** cancels one.
- **Settings → Reminders** (`apps/web/src/reminders.ts`):
  - the lists, cancel with a confirm, and "Checked 12 s ago";
  - it turns into a warning when the last check is older than three ticks plus a margin.
- **The page:** handles the `notice` event in the conversation view. The message shows "due 4:00 pm" when late, and a missed or failed notice shows as a quiet line, not as her speech.

### `trillion doctor`

One line, **Scheduler**: how many tasks are active and the next one due. It warns if any run is `blocked` or `failed` in the last day. Doctor reads only; it never starts the scheduler.

## Out of scope

- Named routines and the Briefings inbox (AGT-3).
- Staying quiet when there is nothing to report (AGT-6): a timed ask is something the user asked to be told, so it always delivers.
- Alerts and checkers (AGT-5).
- Waking a sleeping computer, or running while Trillion is not running.
- Delivery to Telegram or Slack.
- Approving, from a notice, an action a timed ask suggested.

## Testing

All with a fake clock and fake providers.

- **when:**
  - one-offs and every repeat;
  - 9:00 across both daylight-saving changes in a zone that has them;
  - the skipped hour and the repeated hour;
  - month day 31 in February.
- **store:**
  - an occurrence is enqueued once (unique index);
  - claiming is atomic: two concurrent claimers get different runs, or one gets nothing;
  - a stale `running` row is reclaimed as `interrupted`.
- **runner:**
  - on time;
  - asleep through one due time, and through five due times of a repeating task (one run, marked late);
  - a timed ask 30 min late runs, and one 2 h late is missed;
  - no model: blocked, then runs once the model is back, or is missed after an hour;
  - timeout: failed, with no retry;
  - a restart mid-run: interrupted, then run once more and recorded;
  - cancel removes queued runs.
- **ask:**
  - the unattended view lists no gated tool and no "asks first" MCP or Google tool;
  - the router refuses a card for `via=schedule`;
  - untrusted results stay wrapped;
  - the overlay is present.
- **door:**
  - a free page gets the notice at once;
  - a busy page gets it after the reply ends (VOI-17);
  - no page means a notification once and held, then delivered on the next page, once;
  - two pages: only the most recent gets it;
  - missed and failed notices carry no speech;
  - only `delivery/door.py` imports the session and the notifier (a structural test).
- **notify:** the argument list per platform, no shell, the timeout, a failure that doesn't raise, the Windows creation flag.
- **tools:**
  - a past time is refused;
  - she gets the exact time back;
  - `reminder_set` is refused after reading outside content;
  - `ask_schedule` shows a card and is set only when approved;
  - list and cancel work.
- **API and web:** the routes, the token, the stale warning, the late label.
- **End to end:** with the local model, "remind me in two minutes to stretch" delivers "stretch" in the open page two minutes later. With the page closed, the system notification is shown and the notice arrives on the next open.
