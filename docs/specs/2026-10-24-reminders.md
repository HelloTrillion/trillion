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

  She works out the exact time in the user's timezone (`USER.md`, from Names and tone; else the computer's own, where the system reports one) and confirms it: "4:00 pm today." Windows reports no IANA name the standard library can read, so there the user sets one in Names and tone; with no known zone, `reminder_set` and `ask_schedule` refuse and tell her to ask for it.
- **When it is due.**
  - **A page is open:** the notice arrives in the conversation as her message, spoken if a voice is on. It never arrives while she is replying (VOI-17); it waits, then arrives. A busy live page gets no system notification: the notice just waits for it.
  - **No page is open:** the computer shows a notification, and the notice is held for the next time a page opens.
  - **Late:** a notice that arrives late carries its due time, told in the schedule's own zone ("due Sat 4:00 pm"), beside it.
  - **Missed and failed** notices are shown as a quiet line with the task's title. They are not saved to the conversation, so they do not come back after a reload; Settings → Reminders keeps them under recent results.
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
  - `status`: `queued` | `running` | `delivered` | `late` | `missed` | `blocked` | `failed` | `interrupted` | `cancelled`.
  - Outcome: `attempts`, `result_notice` (a `notices` id), `detail` (a short reason, never content).
  - At most one run per schedule per due time: a unique index on `(schedule_id, due_ms)`.
- **`notices`**: everything waiting to reach the user (SUR-9).
  - What: `id`, `owner_id`, `source` (`schedule`), `source_id`, `kind` (`reminder` | `ask` | `missed` | `failed`), `title`, `text` (her words; NULL for missed or failed), `due_ms`, `late`.
  - Delivery: `created_ms`, `status` (`held` | `delivered`), `delivered_ms`, `delivered_to` (the session's conversation id; never `system`), `notified_ms` (when the system notification was shown; a notification never delivers a notice, it stays held).
- **`conversations.kind`**: `chat` | `scheduled`. A timed ask runs in a `scheduled` conversation, kept like any other; `exists()` finds only chats, so no page can open one.

The claim uses `UPDATE … RETURNING`, which needs SQLite 3.35 or newer.

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

`ScheduleStore` is the protocol the scheduler and the door depend on; `storage/sqlite/schedule_store.py` is the SQLite store, with a connection of its own and one change at a time inside `BEGIN IMMEDIATE`:
- Schedules: create, get, cancel (its queued and blocked runs become `cancelled`), list active, list due.
- Runs:
  - Enqueue a run and move the schedule on, together (insert-or-ignore on the unique index, so the same occurrence is never recorded twice).
  - Claim one atomically, optionally only of one kind: `UPDATE … SET status='running', claimed_by=?, claimed_ms=? WHERE id = (SELECT … WHERE due_ms <= ? AND (status='queued' OR blocked and last tried before this pass) ORDER BY due_ms, id LIMIT 1) RETURNING *`. A queued run's claim counts an attempt; a blocked one's does not, since it never ran.
  - Finish a run; finish a run **with its notice** in one transaction, so a crash between them can never leave a run to be tried again with its notice already written.
  - Reclaim: runs left `running` by another `claimed_by`, and this process's own runs more than 30 s past their timeout, become `interrupted` (AGT-4 dead-worker reclaim). It assumes one Trillion process per data folder.
  - Recent results, and the latest finished run of each schedule (one row per schedule).
- Notices: get, list held, mark delivered (conditional on `status='held'`, so a notice is delivered once), put back (only the session it was marked for), mark notified (conditional on `notified_ms IS NULL`).

### `scheduler/runner.py`: the loop

`Scheduler(store, clock, run_ask, door, tick_s=15)`, started and stopped with the runtime. Each tick, one at a time:

1. **Reclaim**, on every tick (not only at startup): runs left `running` by another process, and its own runs past their timeout plus 30 s, are `interrupted`.
   - A reminder runs no tools: it is queued once more, attempt 2, and the second attempt is recorded. Interrupted again, it becomes `missed`.
   - A timed ask is never run again: it becomes `failed`, with `detail` "interrupted" and a notice with no text. Unattended tools never ask, but a write the user let run without asking (an MCP tool set to "write", say) need not be one they can undo, and it may already have happened.
2. **Enqueue:** for each active schedule with `next_due_ms <= now`:
   - It enqueues one run at that due time.
   - It advances `next_due_ms` with `next_after(…, now)`, which skips every occurrence already past. Catching up therefore enqueues one run, never one per missed time (D3).
   - A one-off becomes `done` once its run is enqueued. A repeat whose zone this computer no longer knows runs this once and ends.
3. **Heartbeat:** once the queue has been read, `checked_ms` is recorded. Settings shows it. A pass that can't read the store raises before this, so `checked_ms` goes stale, as it should.
4. **Reminders first:** every due reminder is claimed and run, one at a time. Each becomes a notice with `text = body`; the run is `delivered`, or `late` when `now - due_ms > 120 s`. A reminder run's timeout is 10 s.
5. **Then timed asks**, one at a time:
   - **More than 1 h past due:** `missed`, and a notice with no text.
   - **No model available:** `blocked`. It stays blocked and is tried again on a later tick (at least one tick later, never twice in a pass), and a retry does not count as an attempt. Once it is more than 1 h past due, it becomes `missed`.
   - **Otherwise:** `run_ask` (below), with a timeout of 120 s.
     - It returned her reply: a notice with it.
     - It timed out, raised `AskFailed` (including an empty reply), or raised anything else: `failed`, a notice with no text, and `detail` saying why in a few words.
     - It is never retried silently (AGT-8).
   - **While an ask runs,** every tick the scheduler enqueues what came due, delivers the reminders among it, records the heartbeat and lets the door flush. A reminder due during a two-minute ask goes out on time, not late, and Settings never sees a stopped scheduler. A store error meanwhile is logged and the ask goes on.
6. **Flush:** the door is told to offer what is held (and, with no page open, to notify; see below). A cancelled tick (shutdown) skips it.

A run that raises is finished as `failed` with a notice when its schedule can still be read, so it is neither stuck nor run again. One schedule or run that breaks is logged by its error's class name and skipped.

The runner never touches a surface. It only writes notices (SUR-9).

### `scheduler/ask.py`: running a timed ask unattended

`run_ask(schedule) -> str`:

- **The run's context.** It runs in a background conversation (`kind = scheduled`, kept and not shown in the chat list), created only once something happens in it, so an ask blocked on every tick leaves no empty conversations. It calls `brain.loop.run_turn` with:
  - the same system blocks as a live turn, plus a short overlay `persona/prompts/scheduled.md`. The overlay tells her the user is away, and that she should do the ask with the tools she has, say plainly what she would do with any tool she does not have, and keep it short, because it is read on arrival;
  - `ToolContext.via = "schedule"`, a new value of `Via`.
- **Tools that never ask.** The registry passed in is a read-only view, `registry.unattended()`, that lists only tools the router would run without a card. That means:
  - `READ`, plus `WRITE` tools the user has not set to ask;
  - no `IRREVERSIBLE` or `SPEND` tool;
  - no MCP or Google tool the user left on "asks first";
  - no meta tools that approve or deny.
  - no schedule tools at all: an unattended run can't set, list or cancel reminders.
- **The router enforces it too.** It refuses, by name, any tool that does not run unattended (`runs_unattended`) to `via == "schedule"`, so a tool missing from the view can't be called anyway.
- **The result** is her final text. An empty reply, or a provider error after she had begun, raises `AskFailed`, which the runner reports as a failed notice.
- **Untrusted content** stays wrapped as always (SPN-6). What she read while unattended is recorded as read, never as the user's words (MEM-5).
- **Blocked:** `NoModel` is raised when there is no provider, or when the provider reports `unreachable` or `unauthorized` before the first token: before any text or tool call. The runner treats it as blocked (TESTING §4.2).
- **Usage** is recorded as it is spent, so a run cut off by its timeout still records what it used.

### `delivery/door.py`: the one door (SUR-9)

`Door(store, sessions, *, clock, owner, notify)` is the only component that turns a notice into something the user sees or hears.

- **When a notice is added,** it is offered to the live page if there is one; if that page is busy it just waits, with no system notification. With no page open, the system notification is shown once (`notified_ms`), and the notice is held.
  - **The live page:** the most recently active connected session.
  - **Free:** nothing in flight, judged from facts the session reports, as VOI-17 says: no turn task running, the microphone closed, no input being taken in, the session fully started, no sentence of hers being said outside a turn, and the last turn sent to be heard reported played (or sent more than 120 s ago, for a page that never reports it).
- **When a session becomes free, or a page opens,** every held notice is offered to it, oldest first, each marked delivered (conditional update) before it is sent. If marking is stopped or fails part way, every notice marked so far is put back to held before the error goes on. A session that finds itself no longer free raises `NotFree`, taking none, and they are held again.
- **On every scheduler tick** the door offers what is held to the live page, in case it came free without saying so. With no page open, it shows the system notification for any held notice never notified (held when its page closed, or written just before a crash), once; a notification that can't be shown is tried once per process.
- **Delivering to a page** saves the ones with her words as her messages in that conversation, in one statement, so they survive a reload; missed and failed ones are not saved. Each is sent as a `notice` event: `{code: "schedule", id, kind, title, text, due_ms, due, late}`, where `due` is the due time in words in the schedule's own zone (empty when its zone is unknown here).
  - When `text` is set and a voice is on, she speaks it through the normal speech path (below).
  - When `text` is NULL (missed or failed), the page shows the title and the state, and nothing is spoken.
- **Speaking a notice.** Not when muted, and not when it arrives at the first rest after the user pressed Stop (VOI-9): then it is shown only. Notice speech is numbered apart from turns (`OUT_OF_TURN`), sent with `state … notice: true`, and gives way at once to typing, talking or a tap. A tap's answer in a voiced turn gets an out-of-turn number of its own.
- **After a timed ask's answer** (MEM-5), the current and the next turn count as having read outside content, since her answer may quote an email. So `reminder_set` and `schedule_cancel` are refused right after one; the user can say it again, or use Settings.
- **Her messages side by side.** A notice is her message outside a turn, so the history can hold two of hers in a row, or start with one. The providers send hers that touch as one message, and a conversation she opened starts with a short user placeholder that is never shown or spoken.
- **Nothing else holds a route to a surface.** A test asserts that the scheduler package imports neither `api.session` nor the notifier. Only the door imports them.

### `delivery/notify.py`: system notifications

`notify(title, body) -> bool`, best effort:

- **macOS:** `osascript` with a fixed `on run argv` script; the title and body follow `--` as arguments, never spliced into the script.
- **Linux:** `notify-send`, if it is on the PATH, with `&`, `<` and `>` escaped in the body (some servers render markup).
- **Windows:** a PowerShell toast through `Windows.UI.Notifications`: a fixed script sent with `-EncodedCommand`, the title and body passed as environment values and added as text nodes, launched with `CREATE_NO_WINDOW` so no console flashes (OPS-3).
- **On every platform:**
  - The arguments are passed as an argument list, never through a shell.
  - The call times out after 5 s.
  - A failure is logged by its kind only and returns False. The notice stays held either way.
  - Text is made one line, with control and format characters stripped, and cut to 200 characters.
  - Her words are shown only when the task was a reminder, which she wrote for this. A timed ask's notification shows the title and "Ready in Trillion", so that email content never lands on the lock screen. Missed and failed notifications use fixed lines with the title.

### The tools (`tools/schedule.py`)

- **`reminder_set(when, say, title, repeat?)`: `Risk.WRITE`, runs at once.**
  - It refuses a one-off time that isn't in the future (allowing for clock drift up to 60 s), a `say` over 500 characters, a `repeat` it can't read, and any time when the user's zone isn't known. A repeating one whose first time has passed starts at its next occurrence.
  - It returns the exact time it understood in words ("Fri 24 Oct, 4:00 pm") and the schedule id.
  - It refuses after the turn has read outside content (`ctx.read_outside`), and right after a timed ask's answer (above): "tell the user what it would be for, then set it again". A page or an email can't plant a reminder that speaks in her voice.
- **`ask_schedule(when, ask, title, repeat?)`: gated, so it shows a card** (D6).
  - The summary reads "Every weekday at 9:00 am: check my email and tell me what's urgent".
  - The same checks as `reminder_set`, with `ask` up to 1000 characters.
  - It is declared `irreversible`, because setting it is a standing instruction. That gives a card that a yes or a tap approves.
- **`schedule_list()`: `READ`.** What is active, with next times, and the last result of each.
- **`schedule_cancel(id)`: `WRITE`.** It cancels one, and its queued and blocked runs become `cancelled`. It is refused after reading outside content too, so an email can't talk her into cancelling a timed ask the user approved; the user can cancel it in Settings.

None of the four is offered to an unattended run.

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
  - a claim can take only reminders;
  - the last result is the latest run of each schedule;
  - a stale `running` row is reclaimed as `interrupted`.
- **runner:**
  - on time;
  - asleep through one due time, and through five due times of a repeating task (one run, marked late);
  - a timed ask 30 min late runs, and one 2 h late is missed;
  - no model: blocked, then runs once the model is back, or is missed after an hour;
  - timeout: failed, with no retry;
  - a restart mid-run: a reminder is interrupted, then run once more and recorded; a timed ask is interrupted, then failed, and not run again;
  - cancel removes queued runs;
  - reminders go before asks in a pass, and a reminder due while an ask runs is delivered before it ends, on time, with the heartbeat moving meanwhile;
  - a pass that can't read the store leaves `checked_ms` where it was.
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
  - marking stopped or failing part way puts every marked notice back;
  - with no page open, a tick notifies a held notice never notified, once;
  - only `delivery/door.py` imports the session and the notifier (a structural test).
- **notify:** the argument list per platform, no shell, the timeout, a failure that doesn't raise, the Windows creation flag.
- **tools:**
  - a past time is refused;
  - she gets the exact time back;
  - `reminder_set` and `schedule_cancel` are refused after reading outside content;
  - `ask_schedule` shows a card and is set only when approved;
  - list and cancel work.
- **API and web:** the routes, the token, the stale warning, the late label.
- **End to end:** with the local model, "remind me in two minutes to stretch" delivers "stretch" in the open page two minutes later. With the page closed, the system notification is shown and the notice arrives on the next open.
