# Live widgets: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Widgets built from a connector refresh themselves while the board is on screen: the server replays her reads and a fenced model step fills in the new data.

**Spec:** `docs/specs/2026-10-25-live-widgets.md` (read it, and `docs/design/widgets.md`).

**Architecture:** `workspace/live.py` records eligible reads per turn and builds a widget's `live`. The router learns `via="refresh"` (reads only). `workspace/fill.py` is the no-tools model call. `workspace/refresh.py` is the queue: who sees which board, what is due, one at a time, the hourly cap, backoff. The session reports reads and `board.seen`; the page sends `board.seen`/`board.refresh` and draws the live footer.

**Tech stack:** Python 3.13 (uv, pytest, ruff, strict mypy); TypeScript (Vite, vitest, jsdom).

**Branch:** `workspace/live-widgets`.

**House rules:** `git commit -s`, message ending in a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; only `git add` your files; generic names only (`scripts/containment_check.py`); short plain comments that say why; never play sound; no browser. Checks: `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `python3 scripts/check_one_writer.py`, `python3 scripts/containment_check.py`, `python3 scripts/check_raw_hex.py`, `python3 scripts/check_agent_name.py`, `uv run python scripts/gen_kit_samples.py --check`; web `pnpm -C apps/web test && pnpm -C apps/web typecheck && pnpm -C apps/web build`. Keep the workspace tool budgets (core < 12,000, every pack < 16,000 characters).

---

### Task 1: Recording reads and `live`

**Files:** create `src/trillion/workspace/live.py`, `tests/workspace/test_live.py`; modify `src/trillion/workspace/{widgets,recipe_kit,service}.py`, `src/trillion/tools/workspace.py`, `src/trillion/api/session.py` (record succeeded reads), `src/trillion/api/runtime.py` (wire), the `for_page` path (strip reads; send `live: {every, state, failed_at?}`), schema/cards (`refresh` field, within budget).

- [ ] Failing tests per the spec's "What a live widget remembers": eligibility (READ and `runs_unattended`, minus web_fetch, describe_self, memory and workspace tools), connector naming (MCP server title, gmail/calendar/drive/slack prefixes; check the real tool names in `connectors/google/tools.py` and `connectors/slack/tools.py`), matching by source (title or short name, the same rule as the page's `tileNames`; put one Python helper beside it), last five, `live` dropped from her input, `refresh` "off" or 2–1440 (numbers as text accepted), defaults by type, `workspace_update` rebuilds or keeps `live`, `for_page` never sends reads.
- [ ] Implement. Reads are kept per (session id, turn) in memory and cleared when a turn ends.
- [ ] Checks; commit "Workspace: a widget remembers the reads that made it".

### Task 2: Replay, fill-in and the queue

**Files:** create `src/trillion/workspace/fill.py`, `src/trillion/workspace/refresh.py`, tests; modify `src/trillion/spine/contract.py` (`Via` gains `"refresh"`), `src/trillion/spine/router.py` (refuse per spec D4 for `via == "refresh"`), `src/trillion/spine/confirm.py` if needed (a refresh can never settle or raise a card), `src/trillion/workspace/service.py` (save a refreshed widget in place; failures), `src/trillion/api/session.py` (`board.seen`, `board.refresh`, turn running → wait), `src/trillion/api/runtime.py` (start/stop the refresher task with the runtime), the preferences store for "Keep widgets fresh" (find how preferences are read and written, e.g. voice settings, and follow it).

- [ ] Failing tests per the spec's "How a refresh runs" and Testing, using the scripted provider and a fake clock: replay through the router with `via="refresh"` (refusals for gated, non-READ, workspace, schedule; deny floor still applies), fill-in keeps the fixed fields and a bad answer keeps old data, the queue (due only while seen, catch-up, one in flight, 60/hour, 3 failures then stop until seen again, waits while a turn runs, the preference stops it), `board.refresh` refreshes now, `board.refreshing` sent on and off, nothing added to the conversation, usage recorded (see how `brain/title.py`'s caller records usage, and do the same).
- [ ] Prompt line in `text.md` and `voice.md` (short, no em or en dashes) per the spec; test.
- [ ] Checks; commit "Workspace: live widgets refresh while the board is seen".

### Task 3: Web

**Files:** `apps/web/src/board.ts`, `apps/web/src/main.ts`, `apps/web/src/app.css`, the Settings section that holds preferences (find it), tests in `apps/web/test/`.

- [ ] `board.seen {on}` when the board is shown or hidden and on `visibilitychange`; never twice in a row with the same value.
- [ ] Footer: "Stripe · live · 1 min ago" for `live`; a small pulsing dot while `board.refreshing` is on for that card (still with reduced motion); `state: "failed"` → "Couldn't refresh · 12 min ago" in the attention tone.
- [ ] A refresh button beside the × on live cards (same quiet style as the ×, an accessible name "Refresh “title”") sends `board.refresh {id}`; disabled look while refreshing.
- [ ] Settings switch "Keep widgets fresh", wired to the preference.
- [ ] Tests for each; web checks and raw-hex; commit "Web: live cards, their footer and a refresh button".

### Task 4: Docs and the live run

- [ ] `docs/design/widgets.md` (a "Live widgets" section: what is remembered, rates, caps, what she can change, safety), `docs/architecture.md` rows, `docs/TESTING.md` manual checks, `docs/PRD.md` SUR-1 one sentence. Commit "Docs: live widgets".
- [ ] The controller runs the live checks.
