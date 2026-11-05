# Helpers Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show routines, timed asks and watchers as her helpers in a live panel beside the chat, grouped under her by state, with details, run history, pause and resume, and "Talk to it". See [docs/specs/2026-11-05-helpers-panel.md](../specs/2026-11-05-helpers-panel.md); the spec is the source of truth for rules and wording.

**Architecture:**
- A new read-only package, `src/trillion/helpers/`, builds the list from the scheduler store, the watcher settings and runner, pending cards and notices, and the spend desk.
- `src/trillion/api/helpers.py` serves it and pushes `helpers.changed` (coalesced) to every page.
- The web app adds a Helpers button and panel (`apps/web/src/helpers.ts`).

**Tech stack:** Python 3.13 (asyncio, sqlite via the existing stores), TypeScript web app (vitest, jsdom).

**Branch:** `helpers/panel` (built on `spend/caps`; the spec is committed there).

**Rules for every task:**
- TDD.
- `git commit -s`; each message ends with a blank line, then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Gate before each commit:
  - `uv run pytest -q -x --ignore=tests/spine`
  - `uv run pytest tests/spine -q`
  - `uv run ruff check .`
  - `uv run ruff format --check .`
  - `uv run mypy`
  - `uv run python scripts/containment_check.py`
  - web tasks also: `pnpm typecheck`, `pnpm test` (in `apps/web`), and `python3 scripts/check_raw_hex.py`
- No em or en dashes in code comments or user-facing text.
- No real API calls; tests use fakes.
- Never open `src/trillion/connectors/google/google_client.json`.
- Never touch the macOS keychain: tests use `MemoryStore`; set `PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring` for anything that runs the app.
- Never play sound.
- Don't touch `.claude/preview/home` or ports 7777, 7778 and 7790.
- Don't push.
- Don't widen the allow-lists in `tests/spend/test_structure.py`.
- New test directories must be listed in `.github/workflows/ci.yml` (a repo test checks this).

### Task 1 (core): the helper list

**Files:**
- Create: `src/trillion/helpers/__init__.py`, `src/trillion/helpers/model.py` (the `Helper`, `LastRun`, `Waiting` dataclasses and `to_json`), `src/trillion/helpers/build.py`
- Test: `tests/helpers/__init__.py`, `tests/helpers/test_build.py`

- [ ] **`model.py`:** frozen dataclasses matching the spec's fields:
  - `Helper(id, kind, name, job, state, doing, since_ms, last_run, next_ms, waiting, conversation_id)`, where `kind` is `"routine" | "ask" | "watcher"` and `state` is `"needs_you" | "working" | "idle" | "paused" | "capped"`;
  - `LastRun(at_ms, outcome, reason)`, where `outcome` is `"succeeded" | "skipped" | "failed" | "nothing"`;
  - `Waiting(type, id, text)`, where `type` is `"approval" | "question" | "step"`.
- [ ] **`build.py`:** `async def helpers(sources) -> list[Helper]`, where `sources` is a small Protocol bundle so tests pass fakes:
  - **Routines and asks:**
    - from `ScheduleStore.active(owner)` plus `last_results(owner)`;
    - `paused_ms` gives `paused`; a run claimed and not finished gives `working` with `since_ms` from `started_ms`;
    - `next_ms` from `next_due_ms`;
    - the last run's outcome from `task_runs` status and `detail`. Map a run that ended with no notice and no board to `nothing` (AGT-6): check how `routine_nothing` records it in `scheduler/` and match that exactly.
    - A one-off ask that has fired is not `active`; confirm it is absent.
  - **Watchers:**
    - from the runner's `watchers()` and the attention settings: off gives `paused`; currently checking gives `working`;
    - the last check's time and outcome from the runner's per-watcher state (add a small read-only accessor on `AttentionRunner` if none exists: `status(name) -> (checking_since_ms, last_check_ms, last_error_kind)`);
    - `next_ms` is the next check.
  - **Names and jobs:**
    - routines use their title;
    - asks use their title and when they fire;
    - watchers are "Meetings", "Urgent email", "Payments": reuse `_NAMED` in `tools/speak_up.py` rather than copying it.
  - **Capped:** a routine, ask or watcher whose work kind is in `await rt.spend.paused(pid)` for the background provider is `capped`. `capped` outranks `idle` but not `needs_you`.
  - **Order:** needs you (oldest first), working (by start), then idle, paused and capped (by `next_ms`, nulls last).
- [ ] **Tests:** every kind in every state; a failed last run with its reason; a `nothing` run; a paused routine; a capped watcher; a fired one-off ask absent; the order.
- [ ] **Commit:** "Helpers: one list of her routines, timed asks and watchers".

### Task 2 (core): what is waiting on you

**Files:**
- Modify: `src/trillion/helpers/build.py`; the spine's pending-action store and the notice store if they need a read-only "raised by" lookup
- Test: `tests/helpers/test_waiting.py`

- [ ] **Find where a pending approval card records its origin.** Grep the spine for how a routine run's tool call creates a pending action (session id, conversation id, the routine's schedule id). If an action raised during a routine run can't be traced to its schedule, add the schedule id to what the run already records (read-only for the helpers module; written where the run creates the action), with a migration only if a column is needed.
- [ ] **Map waiting items to helpers:**
  - an approval card from a routine or ask run gives `Waiting("approval", action_id, its title)`;
  - a notice that asks the user to do something (a held routine result marked as needing a step, if any exists) gives `Waiting("step", ...)`.
  - `question` has no source today: keep the type, add no producer, and say so in a comment pointing to step two.
  - A helper with any `waiting` item is `needs_you`.
- [ ] **`needs_you` count:** the number of helpers in `needs_you`, not the number of items.
- [ ] **Tests:** a card raised by a routine run lands on that routine and makes it `needs_you`; answering it clears it; a card from the main conversation lands on no helper.
- [ ] **Commit:** "Helpers: what each one is waiting on you for".

### Task 3 (core): the API, live updates and "Talk to it"

**Files:**
- Create: `src/trillion/api/helpers.py`, `src/trillion/helpers/live.py` (the coalescing notifier)
- Modify: `src/trillion/api/app.py` (mount the router), `src/trillion/api/runtime.py` (build the notifier, wire change hooks), and the places that change a helper (scheduler claim and finish, pause and resume, attention runner check start and end, attention settings save, pending action raised and answered, spend pause and release)
- Test: `tests/helpers/test_api.py`, `tests/helpers/test_live.py`

- [ ] **Routes:**
  - `GET /api/helpers` returns `{helpers: [...], needs_you: n}`.
  - `GET /api/helpers/{id}/runs` returns the last 10 runs `{at_ms, outcome, reason, conversation_id}`. For a watcher: its last 10 checks if the runner keeps them; otherwise the last 10 finds told, from the attention store. Pick and document.
  - `POST /api/helpers/{id}/conversation` returns `{conversation_id}`:
    - a routine returns its conversation, or today's day conversation if it builds there;
    - an ask returns the one it reports to;
    - a watcher makes its conversation on first use, remembers it in the preference `helper_conversations`, and queues one host note for it ("This conversation is about the <Name> watcher: what the user says here is about what it watches. Use speak_up_narrow and speak_up_widen as usual.").
  - All three behind the existing token, origin and JSON checks; a test for 401 and 403.
- [ ] **Live updates (`live.py`):**
  - `Notifier.changed()` marks the list dirty. A single task waits 250 ms, rebuilds the list once and sends `{"type": "helpers.changed", "helpers": [...], "needs_you": n}` to every open page (follow `_broadcast_title` in `session.py` for how pages are reached).
  - Bursts coalesce. A failure to build is logged (no amounts, no content) and retried on the next change.
  - Stopped on runtime close.
- [ ] **Hooks:** call `rt.helpers_changed()` where each change happens. Keep each hook one line, behind an optional callable, so the scheduler and attention modules don't import the API layer.
- [ ] **Tests:** each route's shape; refusals; a burst of ten changes gives one push; a run start and end each push; pause, resume and a watcher toggle push; a card raised and answered push; the watcher conversation is made once and reused, and its host note comes once.
- [ ] **Commit:** "Helpers: an API, live updates, and a conversation to talk to each one".

### Task 4 (web): the Helpers button and panel

**Files:**
- Create: `apps/web/src/helpers.ts`
- Modify: `apps/web/index.html` (the button beside `#workspace-btn` and `#history`, and a panel region), `apps/web/src/main.ts` (wiring, socket message), `apps/web/src/app.css` (tokens only)
- Test: `apps/web/test/helpers.test.ts`

- [ ] **The button:** an icon button with a dot and count while `needs_you > 0`. Its accessible name is "Helpers", or "Helpers, 1 needs you". Opens and closes the panel; Escape closes it; focus returns to the button.
- [ ] **The panel:** follow how the Workspace panel is built and placed (and how it becomes a sheet on a phone).
  - Header "Helpers", then the "Trillion" row with its summary line ("2 working, 1 needs you", or "All quiet").
  - Groups Needs you, Working, Idle, each shown only when not empty, in the spec's order.
  - Rows with the state dot, name and one status line, exactly as the spec words them.
  - A needs-you approval row shows the card's buttons inline by reusing the existing approval card component, so a tap goes through the same path (SPN-14).
- [ ] **Data:** load from `GET /api/helpers` on open; replace on each `helpers.changed`; keep focus and scroll position across updates (update rows in place, keyed by id).
- [ ] **Tests:** the groups and order; the dot and accessible name; each status line; an inline approval tap uses the existing path; updates keep focus; empty state.
- [ ] **Commit:** "Web: a Helpers panel beside the chat".

### Task 5 (web): helper details, history, pause and talk

**Files:**
- Modify: `apps/web/src/helpers.ts`, `apps/web/src/main.ts`
- Test: `apps/web/test/helpers.test.ts`

- [ ] **Details view** inside the panel (a back control returns to the list): the name, job line, the last 10 runs from `GET /api/helpers/{id}/runs` (time, outcome, reason, Open when there is a conversation), the next run, Pause or Resume (Turn off or Turn on for a watcher, using the existing routes), and **Talk to it**, which calls `POST /api/helpers/{id}/conversation` and opens that conversation the way History opens one.
- [ ] **Phone:** the panel is a full-height sheet; the details view fits it; the back control and Escape both return.
- [ ] **Tests:** history renders each outcome; Open opens the conversation; pause and resume call the right route and the list updates from the push; Talk to it opens the returned conversation; phone layout class; keyboard and focus through list, details and back.
- [ ] **Commit:** "Web: helper details, run history, pause, and Talk to it".

### Task 6 (core and web): the four moments in the chat

**Files:**
- Modify: wherever a routine's ready line and a watcher's notice are rendered (`apps/web/src/view.ts` or the notice renderer), and the delivery door only if a field is missing
- Test: extend the notice tests on both sides

- [ ] Check that each of the four moments reaches the main conversation through the one door, and nothing else from a helper does:
  - **result:** the ready line with Open;
  - **approval:** the card;
  - **step:** a line with Open;
  - **question:** not produced yet (Task 2).
- [ ] Each line names the helper it came from ("Morning brief: 3 things for you", with Open), using the same name as the panel.
- [ ] A run with nothing for you adds nothing to the chat (AGT-6). Add a test asserting it.
- [ ] Tapping a helper's name in such a line opens its details in the panel.
- [ ] **Commit:** "Helpers: the chat shows only what needs you, and says which helper".

### Task 7 (docs)

- [ ] **`docs/PRD.md`:** the GAL-7 row's "where they appear is still to be decided" points to the helpers panel spec. AGT-3 and AGT-5's "Exists" lines mention the panel. Add the panel to the Phase table line that lists GAL-7.
- [ ] **`docs/platforms.md`:** a row for the Helpers panel.
- [ ] **Spec:** update `docs/specs/2026-11-05-helpers-panel.md` with any decision made during the build (for example, what a watcher's run history shows).
- [ ] **Commit:** "Docs: the helpers panel exists".

### Task 8 (controller): review, live check, PR

- [ ] A whole-branch review: correctness, SPN-14 on inline approvals, SUR-9 (nothing from a helper reaches a surface except through the door), no content or amounts in logs, and the change hooks covering every state change.
- [ ] Live check on a throwaway install with a local model, voice off:
  - a routine running then finishing;
  - a timed ask firing;
  - a watcher turned off and on;
  - a card waiting on you, answered from the panel;
  - Talk to it for a routine and a watcher;
  - at phone width, and in light and dark.
- [ ] Open the PR. Don't merge until the owner asks.
