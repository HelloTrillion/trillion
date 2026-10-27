# Background reads: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A slow read from outside runs in the background, so the user can keep talking; at the next pause she brings the result in.

**Spec:** `docs/specs/2026-10-27-background-reads.md`. Read it first, along with `delivery/door.py` (`free()`, Facts) and how a tapped card's result is queued (`Session._tapped`, `_queued`, `_begin`).

**Architecture:**
- **The handoff.** `run_turn` gets a `background(use, definition)` hook from the session. An eligible call is answered at once with `status: "started"`, and a job starts on a small per-runtime `Jobs` registry.
- **The result.** When a job ends, its result is saved as a host note (kind `tap`, a `BACKGROUND` marker).
- **The follow-up turn.** If a page is on that conversation, the session queues the note and begins a follow-up turn once `free()` holds. Otherwise a `ready` notice with Open goes through the door, and the follow-up runs when that conversation is next opened and free.

**Tech stack:** Python 3.13 (uv, pytest, ruff, strict mypy); TypeScript (Vite, vitest, jsdom).

**Worktree and branch:** `/private/tmp/claude-501/-Users-kevinfremon-code-Hello-Trillion-Public/5f127305-340b-46e3-b9fd-057da687edcf/scratchpad/background`, on branch `voice/background-reads` (from main). Work only there.

**House rules:**
- Commit with `git commit -s`, the message ending in a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Only `git add` your own files.
- Generic names only.
- Short, plain comments that say why.
- Never play sound; no browser.
- Checks: `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `python3 scripts/check_one_writer.py`, `python3 scripts/containment_check.py`, `python3 scripts/check_raw_hex.py`, `python3 scripts/check_agent_name.py`, `uv run python scripts/gen_kit_samples.py --check`.
- Web checks: `pnpm -C apps/web install --frozen-lockfile` if needed, then `pnpm -C apps/web test && pnpm -C apps/web typecheck && pnpm -C apps/web build`.

---

### Task 1: Server

**Files:**
- Create `src/trillion/api/jobs.py`: the `Jobs` registry, eligibility (`goes_to_background(definition)`), the duplicate check, the cap of 3 per conversation, cancelling a conversation's jobs, and stopping them all.
- Modify:
  - `src/trillion/brain/loop.py` (the hook in `call`; `ResponseStatus` gains `"started"` in `spine/router.py`, kept out of `is_error`);
  - `src/trillion/api/session.py` (the hook; the note; the follow-up turn when free; volatile context listing running jobs; reload of the host line; `tool.end` summary `"background"`);
  - `src/trillion/api/runtime.py` (owns `Jobs`; cancel on conversation delete in `api/conversations.py`; stop in `close`);
  - `src/trillion/persona/prompt.py` (`volatile_context(..., jobs=...)`);
  - `persona/prompts/text.md` and `voice.md` (one line each, no em or en dashes: a read that says it started is on its way, so don't call it again; if asked, say it's coming; when a background note arrives, say in one short line that it's in, show it if it's better seen, then at most two sentences);
  - `delivery/door.py` only if needed for the `ready` notice from a job (reuse the routines `ready` kind and its conversation id).
- Tests: `tests/api/test_background_reads.py`, plus unit tests for `jobs.py`.

- [ ] **Read first:**
  - `run_turn`'s `call` and `run_all` (`brain/loop.py`);
  - `Session._one_turn`, how `ToolEnded` marks outside reads and records live reads (`_remember_read`), `_tapped` / `_queued` / `_begin`, `deliver` and the door's `free()`;
  - routines' `ready` notice and `READY_LINE` host line (`session.py`), and the `ask` via (a follow-up turn can approve nothing).
- [ ] **Failing tests first,** per the spec's Testing (server):
  - eligibility per family and risk, with gated and write tools inline;
  - the cap and duplicates;
  - an immediate paired answer, after which the session is free (`turn.text` is accepted while a job runs);
  - the note is saved as a host note, never the user's words;
  - the follow-up begins only when `free()` holds; test it waits while listening and while her reply is still heard, using the scripted provider and Page from `tests/api/test_session.py`;
  - the follow-up counts as an outside read and can't approve (`via="ask"`);
  - failure and timeout notes;
  - no page on the conversation → a `ready` notice with Open; opening the conversation later runs the follow-up;
  - delete → cancelled; close → cancelled;
  - a widget made in the follow-up from the background read gets `live`;
  - the volatile `background:` line;
  - the prompt lines.
- [ ] **Implement:**
  - The job runs `router.dispatch` with the call's `ToolContext` and the tool's own timeout, and is never cancelled by a barge-in or stop.
  - The follow-up input is host-authored and includes the wrapped response JSON, as `_tapped` does.
- [ ] Run the checks; commit "Background reads: a slow read runs while you keep talking".

### Task 2: Web

**Files:** `apps/web/src/view.ts` (a `tool.end` summary of `background` means the chip reads "working in background" and the page isn't busy; the host line for a `BACKGROUND` note on reload), `apps/web/src/main.ts` (the "N in background" line under the input, from a new `jobs` message the session sends when the count changes), `apps/web/src/app.css`, and tests in `apps/web/test/`.

- [ ] **Failing tests:** the chip wording; input enabled while a job runs; the line appears and clears; the host line on reload.
- [ ] Implement; run the web checks and the raw-hex check; commit "Web: reads in the background".

### Task 3: Docs and the live run

- [ ] Update `docs/architecture.md` (session/loop rows and `api/jobs.py`), `docs/TESTING.md` (a manual check) and `docs/PRD.md` (one sentence under VOI-17). Commit "Docs: background reads".
- [ ] The controller runs the live check.
