# Helpers: her background work, in one place

**Date:** 2026-11-05 · **Requirements:** AGT-3, AGT-5, AGT-6, AGT-9, SUR-9, SPN-14, and where sub-agents appear (GAL-7 was dropped on 2026-10-31) · **Builds on:** [routines](2026-10-27-routines.md), [reminders](2026-10-24-reminders.md), [speaking up](2026-10-29-speaking-up.md), [spend caps](2026-11-04-spend-caps.md)

Trillion already has background workers: routines, timed asks and three watchers (meetings, urgent email, payments). They behave like helpers, but nothing shows them as helpers. To see what is running, waiting or paused, you visit three corners of Settings, and nothing tells you at a glance that one of them needs you.

This change shows them as **her helpers**: a panel beside the chat with her at the top and every helper grouped under her by what it is doing. It follows the patterns that make agent apps such as Grok Bot feel easy: she stays your one contact, helpers have names and one job each, and they interrupt you only when they need you.

This is step one. Step two (AGT-1, AGT-2) adds new helpers that you or she create and hand work to. They join the same panel.

## Decisions

1. **A live view over what already exists.** The core builds the helper list from the scheduler, the watcher settings and the cards and notices waiting on you. It stores no second copy of any helper's state.
2. **A panel beside the chat.** A Helpers button sits next to Workspace and History. On a phone the panel is a full-height sheet.
3. **Grouped under her, by state.** "Trillion" heads the panel. Under her: **Needs you**, **Working**, **Idle**. Never a flat list of everything.
4. **Tap for details, then talk to it.** A helper's details show its job, recent runs, next run, and pause or resume. "Talk to it" opens its conversation. She stays the default contact.
5. **The main conversation shows only four moments:** a result for you, a question, an approval, or a step you must do yourself. Everything else stays in the panel. The Helpers button shows a dot while anything needs you.
6. **The panel only displays.** It cannot start a run. Approvals still need your tap on the card (SPN-14). Everything that reaches you still goes through the one door (SUR-9).

## What a helper is

| Kind | Comes from | Name | Job line |
|---|---|---|---|
| Routine | a schedule of kind routine | its title | what it does, from its ask, one line |
| Timed ask | a schedule of kind ask (AGT-9) | what it will say, e.g. "Remind me at 4" | when it fires |
| Watcher | `meetings`, `email`, `payments` | "Meetings", "Urgent email", "Payments" | what it watches, one line |

Each helper has:

- `id`: `routine:<schedule_id>`, `ask:<schedule_id>`, or `watcher:meetings`, `watcher:email` or `watcher:payments`.
- `kind`, `name`, `job`.
- `state`:
  - `needs_you`: a card, question or step from this helper is waiting on you;
  - `working`: a run is in progress;
  - `idle`: neither;
  - `paused`: you paused it (a routine) or turned it off (a watcher). A timed ask can't be paused, only cancelled;
  - `capped`: paused because its model is at its spend cap (`rt.spend.paused`).
- `doing`: a short line while working, else null. A watcher says what it reads ("Reading your inbox"); a routine or timed ask says "Working", since the row already names it.
- `since_ms`: when the current run started (for "working, 0:12").
- `last_run`: `{at_ms, outcome: "succeeded" | "skipped" | "failed" | "nothing", reason}` or null. `nothing` is a run that had nothing for you (AGT-6).
- `next_ms`: its next scheduled time, or null (watchers: the next check).
- `waiting`: what needs you, `[{type: "approval" | "question" | "step", id, text, at_ms}]`, empty when nothing does. `at_ms` is when it began waiting, or null when that isn't known; Needs you lists the oldest first. An approval's `text` is always "Approve a change", never the card's own words: the list goes to every page.
- `conversation_id`: the conversation "Talk to it" opens, or null if none exists yet.
- `paused`: true for a routine you paused, false otherwise (and for anything but a routine). The page offers Pause or Resume from this, never from `state`.
- `switched_on` and `connected`: a watcher's switch and whether its source (Google or Stripe) is connected, else null. The page offers Turn off, Turn on or Connect from these.

A timed ask that has fired and will not fire again leaves the list. A deleted routine leaves the list.

## The core

A new module, `src/trillion/helpers/`, builds the list. It reads; it never writes a helper's state.

- **Routines and timed asks:** from the scheduler store (schedules, `task_runs` outcomes and reasons, next due).
- **Watchers:** from the watcher settings (on or off), the runner's current check and last check, and their recent finds.
- **Waiting on you:**
  - an approval: a card still proposed in the helper's own conversation. A routine or timed-ask run can never raise a card (the router refuses carded tools to unattended runs), so a card lands on a helper only when it was raised while you talked to it there. A card from any other conversation is on no helper;
  - a step: a watcher switched on whose source isn't connected, or is signed out of Google. Meetings is on by default once Calendar is connected, so Meetings on but not connected asks nothing. Urgent email or Payments on but not connected asks "Connect Google to use Urgent email" or "Connect Stripe to use Payments";
  - a question: nothing raises one yet. It comes with step two.
- **Capped:** from the spend desk's paused kinds.

### Live updates

- `GET /api/helpers` returns `{helpers: [...], needs_you: n}`.
- The core pushes `helpers.changed` with the same body to every open page when anything in the list changes: a run starts or ends, a card is raised or answered, a helper is paused or resumed, a watcher is turned on or off, a cap pauses or releases background work.
- Changes are coalesced: one push at most every 250 ms, however many changes came in.
- With no page open nothing is built or sent. A page opening marks the list changed, so it gets a push.
- A card can lapse with no change anyone makes, so each build sets a timer for the earliest open card's expiry, and the push then clears its dot.

### Runs and actions

- `GET /api/helpers/{id}/runs` returns the last 10 runs, newest first: `{at_ms, outcome, reason, conversation_id}`. A watcher keeps no history of its checks, so its runs are its last 10 finds: one told is `succeeded`, one judged not worth saying is `nothing`, and one claimed but never told is `failed`.
- `POST /api/helpers/{id}/conversation` returns `{conversation_id}`, the one "Talk to it" opens.
- The actions use what exists:
  - a routine: `POST /api/routines/{schedule_id}/pause|resume`;
  - a timed ask: it can't be paused, so it offers Cancel, which the panel confirms, then `DELETE /api/schedules/{schedule_id}`;
  - a watcher: `PUT /api/attention/settings` to turn it off or on. One switched on whose source isn't connected offers Connect, which opens that connection in Connected tools.
- Every route sits behind the same token, origin and JSON checks as the rest of the API.

### Talk to it

- **A routine** opens its own conversation, or today's day conversation if it builds there.
- **A timed ask** opens the conversation it reports to.
- **A watcher** opens a conversation of its own, made on the first "Talk to it" and remembered in the preference `helper_conversations` (`{"watcher:<name>": conversation_id}`). Its first turn carries one host note saying the conversation is about that watcher. The note is kept per conversation (the preference `helper_notes`), so it reaches that conversation only. What you say there edits what it watches, through the speaking-up tools that already exist (`speak_up_narrow` and `speak_up_widen`, with widening asking on a card).

## The page

### The Helpers button

- It sits beside Workspace and History in the header.
- While anything needs you it shows a dot and the count (`needs_you`). Its accessible name says it: "Helpers, 1 needs you".
- The dot clears when nothing needs you.

### The panel

- Header: "Helpers". First row: "Trillion", with a one-line summary ("2 working, 1 needs you", or "All quiet").
- Groups, in order, each shown only when it has a helper: **Needs you**, **Working**, **Idle**. Paused and capped helpers sit in Idle, saying so.
- A row: the state dot, the name, and one short status:
  - needs you: what it is ("Approve a change", "A question for you"), with "Open to approve" or "Open to reply", which opens the conversation the card or question waits in. The panel never answers a card itself: a tap must come from the conversation it was raised in, and what she is told of it belongs there (SPN-14);
  - a step: what to do ("Connect Google to use Urgent email"), with Open Settings, which opens that connection in Connected tools and starts it;
  - working: `doing` and the time since it started;
  - idle: the next run ("next 9:55"), "paused", or "paused: Anthropic is at its cap";
  - a watcher: "off" when switched off, or "off: Google isn't connected" (or Stripe) when switched on but its source isn't connected;
  - the last run's outcome when it failed ("failed at 7:00: couldn't reach the inbox").
- The order within a group: Needs you by oldest first, Working by start, Idle by next run.

### Helper details

- The name and job line.
- The last 10 runs, each with its time, outcome and reason, and Open when it has a conversation.
- The next run.
- **Pause** or **Resume** for a routine. **Cancel** for a timed ask, after a second button that confirms it. For a watcher: **Turn off** or **Turn on**, or **Connect** when it is switched on but its source isn't connected.
- **Talk to it**.

### In the main conversation

Only the four moments appear, each through the one door as today:

1. **A result for you:** the existing ready line with Open.
2. **A question:** a line from the helper, with a Reply that opens its conversation.
3. **An approval:** the same card as today.
4. **A step you must do:** a line saying what and where, with Open. For now a step shows only in the panel (its row, with Open Settings) and on the Helpers button's dot; no line reaches the chat yet.

Each line from a helper leads with its name, as the panel says it, and a tap on the name opens its details. The name rides on the notice (`helper_id`, `helper_name`), only while that helper is still listed (a one-off ask that has fired, or a removed routine, gets none). It is live only: a line drawn again from history after a reload shows no name. A step never reaches the chat: it shows only in the panel and on the Helpers button's dot.

A run with nothing for you shows only in its history (AGT-6). Quiet hours, a meeting under way, the microphone in use and Focus still hold delivery (AGT-5, VOI-17).

## Out of scope

- New helpers that you or she create and hand tasks to (AGT-1, AGT-2): step two.
- Helpers talking to each other.
- A live view of a helper's screen or steps while it works.
- Renaming helpers.

## Platforms

Everything here is in the core and the web page, so it works wherever the app runs. On a phone-width window the panel is a full-height sheet.

## Tests

- **Core:**
  - the list for each kind and each state, including `capped`, `paused`, a failed last run with its reason, and `nothing`;
  - a card raised in a routine's own conversation is under that routine's `waiting`, and `needs_you` counts it (a routine's run itself can never raise a card);
  - `helpers.changed` is pushed when a run starts and ends, a card is raised and answered, a helper is paused and resumed, and coalesced under bursts;
  - a fired one-off ask leaves the list;
  - the routes refuse without the token or from another origin;
  - "Talk to it" for a watcher makes one conversation and reuses it.
- **Web:** the groups and their order, the dot and its accessible name, the row status lines, the details and run history, pause and resume, Talk to it, the phone sheet, keyboard use and focus.
- **Live**, on a throwaway install with a local model: a routine running then finishing, a timed ask firing, a watcher turned off and on, a card waiting on you, opened from the panel and answered in its conversation, at phone width and in light and dark.
