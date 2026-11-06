# Your Helpers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user make specialist helpers under her, each with a name, a job, notes and a chat of its own. She hands them work, offers one when a need repeats, and the Helpers panel manages them. See [docs/specs/2026-11-05-your-helpers.md](../specs/2026-11-05-your-helpers.md). The spec is the source of truth for rules and wording.

**Architecture:**
- **Store.** A `helpers` table and store (`src/trillion/helpers/store.py`). A helper is run through the existing scheduler: a handoff is a due-now timed ask with `helper_id`, and a helper's routine is a routine with `helper_id`. So timeouts, failures, spend and run history all come for free.
- **Tools.** New tools in `src/trillion/tools/helper.py`: `helper_create`, `helper_update`, `helper_narrow`, `helper_widen`, `helper_ask` and `helper_suggest`. The router refuses the dispatch tools wherever `ToolContext.helper_id` is set.
- **Overlay.** One overlay line for a helper's chat and runs is built in `persona/prompt.py`.
- **Suggestions.** Needs are counted from `tool_audit`, with nothing new recorded.
- **Panel.** The helpers panel lists your helpers with Pause, Resume and Delete.

**Tech stack:** Python 3.13 (asyncio, aiosqlite through the existing stores), TypeScript web app (vitest, jsdom).

**Branch:** `helpers/create`. It's built on `voice/mic-wake`, which sits on `helpers/panel` and `spend/caps`. The spec is committed there.

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
- No real API calls; tests use fakes (`tests/brain/fakes.py`: `ScriptedProvider`, `say`, `use`).
- Read every source file with `encoding="utf-8"` (Windows CI).
- Never open `src/trillion/connectors/google/google_client.json`.
- Never touch the macOS keychain: tests use `MemoryStore`; set `PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring` for anything that runs the app.
- Never play sound.
- Don't touch `.claude/preview/home` or ports 7777, 7778 and 7790.
- Don't push.
- Don't widen the allow-lists in `tests/spend/test_structure.py`. A helper's run goes through the existing `Asker`, so it's already metered.
- New test directories must be listed in `.github/workflows/ci.yml` (a repo test checks this).
- Generic names only in tests and fixtures ("Invoices", "Inbox"), nothing from the owner's business.

### Task 1 (core): the helpers table and store

**Files:**
- Create: `src/trillion/storage/migrations/019_helpers.sql`, `src/trillion/helpers/store.py`, `src/trillion/storage/sqlite/helper_store.py`, `src/trillion/helpers/pausing.py`
- Modify:
  - `src/trillion/scheduler/store.py`: `Schedule.helper_id: str | None = None`;
  - `src/trillion/storage/sqlite/schedule_store.py`: read and write `helper_id`, plus `for_helper(owner, helper_id)` and `cancel_for_helper(owner, helper_id, now_ms)`;
  - `src/trillion/storage/sqlite/conversation_store.py`: `ConversationKind` gains `"helper"`, plus `set_kind(conversation_id, kind)`;
  - `src/trillion/api/runtime.py`: open the store as `rt.helpers`.
- Test: `tests/helpers/test_store.py`, `tests/storage/test_migrations.py` (the new migration applies on a copy of a 018 database and keeps every row)

- [ ] **Migration `019_helpers.sql`:**
  - `create table helpers (id text primary key, owner_id text not null references owners(id), name text not null, job text not null, notes text not null default '', conversation_id text not null references conversations(id), paused_ms integer, created_at integer not null)`;
  - `create unique index helpers_by_name on helpers (owner_id, lower(name))`;
  - `alter table schedules add column helper_id text references helpers(id)`;
  - the `conversations.kind` CHECK widened to include `'helper'`. Rebuild the table the way `014_routines.sql` did. Copy that file's pattern exactly, including foreign keys and indexes.
- [ ] **`helpers/store.py`:**
  - `@dataclass(frozen=True, kw_only=True) class OwnHelper(id, owner_id, name, job, notes, conversation_id, paused_ms, created_at)`;
  - `MAX_HELPERS = 20`, `NAME_MAX = 40`, `JOB_MAX = 200`, `NOTES_MAX` reused from `scheduler/store.py`;
  - a `HelperStore` Protocol with:
    - `create(owner, name, job, now_ms) -> OwnHelper | Literal["full", "taken"]`. It makes the `'helper'` conversation, titled with the name, in the same transaction.
    - `get(owner, id) -> OwnHelper | None`;
    - `by_conversation(owner, conversation_id) -> OwnHelper | None`;
    - `all(owner) -> list[OwnHelper]`, oldest first;
    - `update(owner, id, *, name=None, job=None) -> OwnHelper | Literal["taken"] | None`. A rename renames the conversation too.
    - `add_note(owner, id, line) -> bool`, which is False when full; it appends a line as routines do.
    - `pause(owner, id, now_ms) -> bool` and `resume(owner, id) -> bool`;
    - `delete(owner, id, now_ms) -> bool`. It cancels the helper's schedules, sets the conversation kind to `'chat'`, and removes the row, all in one transaction.
  - A `changed: Callable[[], None] | None` hook, called after every write, as `SqliteScheduleStore.changed` is.
- [ ] **`helper_id` on schedules:** `create` and the row mapping carry `helper_id`.
- [ ] **Pausing and resuming a helper reuse the schedules' own pause**, so nothing new runs in the runner:
  - `helpers/pausing.py` has `async def pause_helper(helpers, schedules, owner, id, now_ms)`. It sets the helper's `paused_ms`, pauses each of its routines with `ScheduleStore.pause`, and cancels its one-off asks still waiting (handoffs not yet run).
  - `async def resume_helper(helpers, schedules, runner, owner, id, now)` clears it, and resumes each routine with the same `runner.resume(store, owner, schedule_id, now)` that `api/routines.py` uses. So the next due time is worked out again and no missed run is replayed.
  - Test both: a paused helper's routine has `paused_ms`; a pending handoff is cancelled; after resume, the next due time is in the future.
- [ ] **Tests:** create (and the conversation exists with kind `'helper'` and the title), the 21st refused as `"full"`, a duplicate name in any case refused as `"taken"`, rename renames the conversation, notes full, pause and resume, and delete (schedules cancelled, the conversation kept as `'chat'`, the row gone).
- [ ] Commit: `Helpers: a store for the helpers you make`

### Task 2 (core): the spine knows a helper

**Files:**
- Modify:
  - `src/trillion/spine/contract.py`: `ToolContext.helper_id: str | None = None`, with the comment "Set by the host for a helper's chat or run, never from anything the model says.";
  - `src/trillion/spine/router.py`;
  - `src/trillion/spend/types.py`: `Work` gains `"helpers"`;
  - `src/trillion/spend/desk.py`: helper runs pause at a cap with the other background work.
- Test: `tests/spine/test_router_helpers.py`

- [ ] In `router.py`, add `HELPER_NEVER = frozenset({"helper_create", "helper_ask", "helper_suggest"})`. Before any tool code runs, a call whose name is in `HELPER_NEVER` while `ctx.helper_id` is set is refused (`Refusal.UNATTENDED` is wrong here, so add `Refusal.NOT_FOR_HELPERS` with the text "only Trillion makes helpers or hands them work"). The call is audited as a refusal.
- [ ] `helper_update`, `helper_narrow` and `helper_widen` are allowed in a helper's chat only for that helper. Task 3's precheck enforces it; the router doesn't need to.
- [ ] **Tests:** each name in `HELPER_NEVER` is refused with `helper_id` set on every `via`, allowed without it, and audited. A test asserts the names in `HELPER_NEVER` match the tool definitions, as `REFRESH_NEVER` is checked.
- [ ] Commit: `Spine: helpers never dispatch (AGT-2)`

### Task 3 (core): the helper tools

**Files:**
- Create: `src/trillion/tools/helper.py`
- Modify: `src/trillion/tools/__init__.py` (register them, `bind` at startup with `rt.helpers`, `rt.schedules` and the clock)
- Test: `tests/tools/test_helper_tools.py`

All tools are `category="helper"`, `trusted_output=True`, with explicit `timeout_s` (AGT-8). Shared refusals:
- `via` in `{"schedule", "routine", "ask", "refresh", "channel"}` refuses `create`, `update` and `widen`: nobody said it.
- `ctx.read_outside` refuses `create`, `update` and `widen`: "a helper can't be made or changed after reading a page, a file or a message in this turn; tell the user what it would be, then make it again".

- [ ] **`helper_create {name, job}`:**
  - `Risk.IRREVERSIBLE`, `recoverable=True`, so it asks on a card.
  - `describe`: "New helper: {name}. {job}".
  - The precheck refuses a name or job too long, a name already taken ("there's already a helper called X: suggest using it"), at the limit ("there are 20 helpers: suggest deleting one"), and inside a helper's chat (backstop to Task 2).
  - `execute` creates the helper, then saves its first message to its conversation as hers: `f"Hi, I'm {name}. {job} Tell me anything I should know to start."` (`inert()` on both).
  - It returns `{helper_id, conversation_id}` in the content and `effect="confirmed"`.
- [ ] **`helper_update {helper, name?, job?}`:**
  - `Risk.IRREVERSIBLE`, `recoverable=True`.
  - Only in that helper's own chat (`ctx.helper_id == helper`).
  - `describe`: "Change {old name}: name to X, job to Y".
- [ ] **`helper_narrow {helper, text}` and `helper_widen {helper, text}`:**
  - The same shape and checks as `RoutineNarrow` and `RoutineWiden` in `tools/routine.py`.
  - Reuse `narrows_routine()` and `clean()`, and import them, don't copy.
  - Only in that helper's own chat. Narrow is `Risk.WRITE`; widen is `Risk.IRREVERSIBLE` (a card).
  - Read back with "Noted for {name}: ...".
- [ ] **`helper_ask {helper, request}`:**
  - `Risk.WRITE`, no card.
  - The precheck refuses an unknown helper, and a paused one ("{name} is paused: answer yourself and say so in a few words"). If the desk has paused `"helpers"`: "{name} is at its spend cap: answer yourself and say so".
  - `execute` creates a `Schedule(kind="ask", title=f"{name}: {first 60 chars of request}", body=request, at_local=now, repeat=None, next_due_ms=now_ms, helper_id=helper, created_in=ctx.session_id)`.
  - It returns "asked {name}: say 'Asking {name}' in one line and carry on; the answer comes back by itself".
- [ ] **`helper_suggest {area, name, job}`:**
  - `Risk.READ`.
  - Only offered when the session has an open offer for that area (Task 6); otherwise refused "no helper is being offered now".
  - `execute` saves the offer in the preference `helper_offer` (Task 6), and returns "offered: say nothing more about it".
  - The page gets the line from the session, not from her text.
- [ ] **Tests**, one per behaviour above:
  - the card text;
  - each refusal;
  - the first message saved;
  - narrow at once and widen on a card;
  - update only in its own chat;
  - `helper_ask` makes a due-now ask with `helper_id`;
  - refused when paused or capped.
- [ ] Commit: `Tools: make, change, teach and ask a helper`

### Task 4 (core): a helper's chat, and her list of helpers

**Files:**
- Modify:
  - `src/trillion/persona/prompt.py`;
  - `src/trillion/api/session.py`;
  - `src/trillion/persona/prompts/` (a new `helpers.md` for her instructions about helpers).
- Test: `tests/api/test_helper_chat.py`, `tests/persona/test_prompt.py`

- [ ] **`prompt.py`:**
  - `def helper_overlay(h: OwnHelper) -> str` returns: "You are {name}, one of Trillion's helpers. Your job: {job}. Follow these notes from the user: {notes}" (the last sentence only when there are notes). Pass every field through `inert()`. Include a line for her tools: `helper: {id} "{name}"`, so narrow, widen and update can name it.
  - `volatile_context` gains `helper: str = ""` and `helpers: Sequence[tuple[str, str, str]] = ()`, as (id, name, job) for her own conversations. They ride in `<context>` like `routine`, never in the cached block. Helper names and jobs come from the user, but a job may have been drafted from something she read, so wrap the list as untrusted (`wrap("helpers", ...)`), as the board is.
- [ ] **`helpers.md`** (her instructions, loaded into the cached system block):
  - when to make one (the user asks; never when an existing helper's job covers it);
  - when to hand off (a request that is plainly a listed helper's job; otherwise answer herself);
  - say "Asking {name}" in one line and carry on;
  - suggest only through `helper_suggest` when a host note says so.
  - Keep it under 25 lines and in the voice of the other prompt files.
- [ ] **`session.py`:**
  - In `_read_routines` (rename it `_read_place`), also read `rt.helpers.by_conversation(OWNER, self.id)`.
  - In a helper's chat: set `self._helper`, pass `helper_id` into every `ToolContext`, put `helper_overlay` into the turn's context, and offer `helper_update`, `helper_narrow` and `helper_widen`, but never the dispatch tools.
  - In any other conversation: offer `helper_create` and `helper_ask` (and `helper_suggest` per Task 6), and pass the not-paused helpers as `helpers=`.
  - Extend `_offers` for category `"helper"`.
  - After `helper_create` is confirmed, the session sends `{"type": "helper.created", "helper_id", "conversation_id"}` to the page (Task 8 opens it).
- [ ] **Tests:**
  - a turn in a helper's chat carries the overlay and `helper_id` on its tool calls, and isn't offered the dispatch tools;
  - a turn in a chat lists the helpers and is offered `helper_ask`;
  - a paused helper isn't listed;
  - confirming the create card sends `helper.created`.
- [ ] Commit: `Session: a helper's chat speaks as it, and she knows her helpers`

### Task 5 (core): a helper's runs: handoffs and its routines

**Files:**
- Modify:
  - `src/trillion/scheduler/ask.py`: `Asker` and `_Run`;
  - `src/trillion/delivery/door.py`: the ready line's helper tags.
- Test: `tests/scheduler/test_helper_runs.py`

- [ ] **`Asker.place`:** a schedule with `helper_id` runs in the helper's conversation. If the helper is gone, the run fails with detail "helper deleted". The run's `ToolContext` carries `helper_id`, and the overlay is added to the run's instructions after the scheduled overlay.
- [ ] **A handoff** (a helper's one-off ask) always ends with one `'ready'` notice in the conversation it was asked from (`created_in`), with `ready_notice(owner, schedule.id, title, now_ms, conversation_id=helper's conversation)`:
  - the title is `f"{name}: {her first sentence}"`, at most 140 characters;
  - with nothing to say, the title is `f"{name}: nothing to report"`;
  - when her reply says she needs approval, nothing special is detected. Her overlay for a handoff run tells her to start the reply with "Needs you:" when an action needs the user, and the title then reads `f"{name} needs you: {rest}"`. Test both shapes.
- [ ] **A helper's routine** runs as any routine, in the helper's conversation, with the overlay.
- [ ] **`runner.py`:** nothing new for pausing. A paused helper's routines are paused schedules (Task 1), and its waiting handoffs were cancelled. `helper_ask` already refuses a paused helper.
- [ ] **`door.py`:** the ready notice gets `helper_id` and `helper_name` from the helper (the existing tagging handles `routine:`, `ask:` and `watcher:`; add the `helper:<id>` path).
- [ ] **Spend:** the run's caller kind is `"helpers"` when `helper_id` is set (`spend.caller.as_caller`), so `rt.spend.paused` stops it at a cap.
- [ ] **Tests:**
  - `helper_ask` then the runner: the reply is saved in the helper's chat, and a ready notice lands with the name;
  - "nothing to report";
  - "needs you";
  - a paused helper's routine doesn't run, and runs again after resume without a replay;
  - deleted means a failed run with the detail;
  - capped means not run;
  - a helper's routine runs with the overlay.
- [ ] Commit: `Scheduler: helpers run handoffs and their own routines`

### Task 6 (core): suggesting a helper

**Files:**
- Create: `src/trillion/helpers/needs.py`
- Modify:
  - `src/trillion/storage/sqlite/spine_store.py` (it owns `tool_audit`): add `recent_tools(owner, since_ms) -> list[tuple[str, str, int]]`, returning `(tool, session_id, at)` for calls whose decision allowed them;
  - `src/trillion/api/session.py`;
  - `src/trillion/api/protocol` handling in `session.handle`: the message `helper.offer`.
- Test: `tests/helpers/test_needs.py`, `tests/api/test_helper_offer.py`

- [ ] **`needs.py`:**
  - `AREAS` maps a tool name to an area by prefix. `gmail_` is `"email"`; `calendar_` is `"calendar"`; `drive_` and `file_` are `"files"`; `stripe_` and `mcp_stripe_` are `"payments"`; `slack_` is `"slack"`; `web_` is `"web"`. Any other `mcp_<server>_` is `f"mcp:{server}"`. Everything else is no area (her own tools: workspace, memory, rules, schedule, day, helper, `stay_quiet`, `describe_self`).
  - `def repeated(calls, kinds, zone, now_ms) -> list[str]`: the areas used on at least 3 different local days in the last 14. `calls` are only those in conversations whose kind is `chat` or `day` (`kinds` maps session id to kind).
  - `def to_offer(repeated, helpers, declined, last_offer_ms, now_ms) -> str | None`:
    - none if an offer was made in the last 24 hours;
    - otherwise the first repeated area that isn't declined and isn't covered.
  - "Covered" is a plain word check, so it stays testable and cheap. `COVER_WORDS` maps each area to a few words (email: email, inbox, mail; payments: payment, invoice, stripe, revenue; ...). An area is covered when a helper's name or job contains one.
- [ ] **Offers** live in preferences: `helper_offer` holds `{area, at_ms, name, job}` once made, and `helper_declined` holds a list of areas.
- [ ] **Session, at the start of each turn in a chat or day conversation:** read the audit for 14 days, compute `to_offer`, and if there is one, add a host note to the turn's context: "After answering, offer the user a helper for {area} with helper_suggest, once, in one sentence." Offer `helper_suggest` only on that turn.
- [ ] **When `helper_suggest` runs:** save `helper_offer`, and send the page `{"type": "helper.offer", "area", "name", "job"}`.
- [ ] **The page's answer:** `{"type": "helper.offer", "area", "choice": "create" | "decline"}`, accepted only when it matches the saved offer:
  - `create` makes the helper (your tap is the approval, as a card's is) and sends `helper.created`;
  - `decline` adds the area to `helper_declined`;
  - either clears `helper_offer`'s name and job, keeping `at_ms` for the once-a-day rule.
- [ ] **Tests:**
  - areas from tool names;
  - 3 days in 14 counted by local day, not by call;
  - helper and routine conversations don't count;
  - covered, declined and once-a-day each stop an offer;
  - the host note is added only then;
  - create from the page makes the helper;
  - a stale or forged answer does nothing;
  - decline is kept.
- [ ] Commit: `Helpers: she offers one when a need repeats`

### Task 7 (core): the panel lists your helpers

**Files:**
- Modify:
  - `src/trillion/helpers/model.py`: `kind` gains `"own"`; add `notes: str | None` and `routines: list[RoutineLine]` (`RoutineLine(id, name, next_ms, paused)`);
  - `src/trillion/helpers/build.py`;
  - `src/trillion/helpers/wiring.py`: hook `rt.helpers.changed`;
  - `src/trillion/api/helpers.py`.
- Test: `tests/helpers/test_build.py`, `tests/api/test_helpers_api.py`

- [ ] **`build.py`:**
  - Each own helper is a `Helper` with id `own:<id>`, `kind="own"`, its name and job, and `conversation_id`.
  - State: `paused` from `paused_ms`; `working` when any of its schedules is running; `needs_you` from cards in its chat, as today. Capped when the desk has paused `"helpers"`.
  - `last_run` and `next_ms` come from its schedules. Its routines are listed under it in `routines`, and are not separate rows.
  - Handoff asks never show as rows of their own.
- [ ] **`api/helpers.py`:**
  - `POST /api/helpers/{id}/pause` and `.../resume` (through `pause_helper` and `resume_helper`), and `DELETE /api/helpers/{id}`, for `own:` ids only. Anything else returns 404.
  - Runs (`GET /api/helpers/own:<id>/runs`) come from its schedules' history, newest first, 10 at most.
  - `POST /api/helpers/{id}/conversation` returns its chat.
- [ ] **Tests:**
  - an own helper's row in each state;
  - its routines nested and not listed twice;
  - a handoff in progress shows it as working;
  - the routes refuse without the token, from another origin, and for a non-`own:` id;
  - delete then list means it's gone and its chat still opens.
- [ ] Commit: `Helpers panel: your helpers, with pause and delete`

### Task 7b (core): a helper's picture and its first hello

**Files:**
- Create: `src/trillion/helpers/look.py`
- Modify:
  - `src/trillion/storage/migrations/019_helpers.sql`: it's unreleased, so add `icon text not null default 'spark'`, `color text not null default 'blue'` and `hello_ms integer` there, and update the checksum test;
  - `src/trillion/helpers/store.py` and `storage/sqlite/helper_store.py`: `OwnHelper.icon`, `.color` and `.hello_ms`; `set_look(owner, id, icon, color) -> bool`; `said_hello(owner, id, now_ms) -> bool` (True only the first time);
  - `src/trillion/tools/helper.py`: `helper_create` takes an optional `icon` and `color`; the card shows them;
  - `src/trillion/api/helpers.py`: `PUT /api/helpers/{id}/look`;
  - `src/trillion/helpers/build.py` and `model.py`: `icon` and `color` on every Helper (for own helpers; null for others);
  - `src/trillion/delivery/door.py`: a helper's notice carries `helper_icon` and `helper_color`;
  - `src/trillion/api/session.py`: the first hello.
- Test: `tests/helpers/test_look.py`, `tests/api/test_helper_hello.py`, plus additions to the store, tool and API tests.

- [ ] **`look.py`:**
  - `ICONS`: a tuple of 24 ids (receipt, envelope, calendar, chart, cart, folder, people, chat, star, heart, bell, globe, book, briefcase, wrench, leaf, home, plane, music, camera, code, shield, lightbulb, spark);
  - `COLORS`: 8 ids (blue, green, amber, rose, violet, teal, orange, slate);
  - `pick(name, job, icon=None, color=None) -> tuple[str, str]`. It keeps a valid `icon` and `color`. Otherwise:
    - the icon comes from a small keyword table on the job (invoice, payment, revenue: receipt; email, inbox, mail: envelope; calendar, meeting: calendar; report, numbers: chart; ...), else `spark`;
    - the colour comes from a stable hash of the casefolded name (not Python's `hash`).
- [ ] **`helper_create`:** the schema adds optional `icon` (enum `ICONS`) and `color` (enum `COLORS`); the description tells her to pick ones that fit the job. `execute` stores `pick(...)`. `describe` stays text, and the card JSON carries the look so the page can draw it, through whatever field cards use for extra display data. If cards have none, add an optional `look: {icon, color}` field to the pending card's page JSON. It's display only: never part of what is approved or hashed.
- [ ] **`PUT /api/helpers/own:<id>/look {icon, color}`:** the user's own tap, behind the usual token, origin and JSON checks. 400 for names not in the lists, 404 for a missing helper. It fires `changed`.
- [ ] **The first hello:**
  - The page sends `{"type": "helper.hello"}` when it opens a helper's chat.
  - The session (in a helper's chat only) calls `said_hello`. Only when that returns True (the first time ever), it runs one host turn there with the overlay and the note: "This is your first message to the user. In one or two sentences say what you'll do first and anything you need to know. If a regular check suits your job, offer one with ask_schedule as your routine (routine "own"), at a sensible time."
  - The fixed first line saved by `helper_create` stays as the chat's opening. If the turn fails, nothing more is said.
  - `ask_schedule` in a helper's chat sets `helper_id` on the schedule, so the routine is the helper's and runs in its chat. Check how `ask_schedule` sets a routine's conversation; an "own" routine made in a helper's chat must use the helper's chat as its conversation.
- [ ] **Tests:**
  - `pick`: kept, picked from the job, a stable colour, invalid names replaced;
  - the store's look and `said_hello` once;
  - the card carries the look;
  - the PUT route (token, origin, 400, 404, changed);
  - `helper.hello` runs one turn the first time and none after;
  - a card from the hello makes a routine with `helper_id`;
  - a model failure leaves the fixed line.
- [ ] Commit: `Helpers: a picture for each, and a first hello that offers a routine`

### Task 8 (web): making and managing helpers

**Files:**
- Modify:
  - `apps/web/src/helpers.ts`: the panel;
  - `apps/web/src/main.ts`: the `helper.created` and `helper.offer` messages;
  - `apps/web/src/view.ts`: the offer line;
  - `apps/web/index.html` only if a container is needed.
- Test: `apps/web/test/helpers.test.ts`, `apps/web/test/helper-offer.test.ts`

- [ ] **+ New helper** at the top of the panel opens one field, "What should it do?", with **Ask Trillion**. Submitting sends the text `New helper: {text}` as a user message in the current main conversation (not a helper's chat; if one is open, switch to the last chat conversation first). The panel then closes, so her card is seen.
- [ ] **`helper.created`:** open that conversation and the panel's details for it.
- [ ] **The picture** (Task 7b): `apps/web/src/look.ts` draws a helper's round picture from `{icon, color}`.
  - Each of the 24 icons is a simple inline SVG path, drawn here (stroke style, 24x24 viewBox). Lucide's ISC-licensed paths may be used if `NOTICE` gets the licence line.
  - The 8 colours are CSS tokens in `apps/web/src/app.css` (`--helper-blue` and so on) for light and dark, built from existing palette tokens where possible. No raw hex outside the token block (`check_raw_hex.py`).
  - It shows on the panel row (with the state dot on its edge), in details, at the top of a helper's chat, on its ready lines in her conversation (`helper_icon`, `helper_color`), and on the create card.
  - Tap it in details: a grid of the icons and a row of the colours. A tap sends `PUT .../look` and updates at once.
  - It has an accessible name, "Invoices picture: receipt, green".
- [ ] **The first hello:** when the page opens a helper's chat, it sends `{"type": "helper.hello"}` once per page load per helper. The core decides whether it's the first time.
- [ ] **Own helper rows** show the name, the state dot, and the job when idle.
- [ ] **Own helper details** show the job, its notes ("What it knows"), its routines with next runs, its last 10 runs, **Talk to it**, **Pause** or **Resume**, and **Delete**. Delete uses the same two-step confirm the panel uses for Cancel, with the spec's words: "Delete Invoices? Its routines stop. Its chat stays in History."
- [ ] **The offer line** goes in the conversation: "Want a helper for {area}? {name}: {job}" with **Create** and **No thanks**. It sends `helper.offer` with the choice, and is replaced by "Made {name}." or "Okay." The line belongs to the current page only and isn't redrawn from history.
- [ ] **Tests:**
  - the + flow sends the right message and closes the panel;
  - `helper.created` opens the chat;
  - the details for an own helper;
  - pause, resume and delete call the routes;
  - delete's two-step confirm;
  - the offer line and both choices;
  - keyboard use and focus;
  - phone width.
- [ ] Commit: `Web: make, offer and manage your helpers`

### Task 9 (docs)

- [ ] Amend PRD AGT-1 and AGT-2 to describe what exists and say what is deferred (the manifest fields, YAML, prompt versioning, per-helper models and permissions). Note the Helpers panel in AGT-3's row.
- [ ] `docs/platforms.md`: one row; it works everywhere the core and web app run.
- [ ] In the spec, say needs are counted from the tool audit (Suggestions section), as built.
- [ ] Commit: `Docs: the helpers you make exist`

### Task 10 (controller): review, live check, PR

- [ ] A final review of the whole branch against the spec, then fix what it finds.
- [ ] Live, on a throwaway install with a local model (`.claude/preview_spend.py`, port 7794, voice off):
  - make a helper by asking and by **+ New helper**;
  - talk to it and correct it (narrow at once, widen on a card);
  - hand it work and see "Asking ..." then the ready line;
  - pause it and see she answers herself;
  - seed `tool_audit` on three days and see one offer, then Create and No thanks;
  - delete it;
  - check phone width, light and dark.
- [ ] Open a PR with base `voice/mic-wake`, bind it with ccd_pr, and don't merge until the owner asks.
