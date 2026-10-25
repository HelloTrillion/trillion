# Approval cards on her board: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pending approvals open on the board as large host-composed cards, with her context widgets beside them, without changing what approving means.

**Spec:** `docs/specs/2026-10-25-board-approvals.md` (read it first).

**Tech stack:** Python 3.13 (uv, pytest, ruff, mypy); TypeScript (Vite, vitest).

**Branch:** `board/approvals`.

**House rules:** `git commit -s`, message ending in a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; only `git add` your files; no real people or businesses; short plain comments; never play sound; no browser (the controller checks visually). Checks: `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, the four `scripts/check_*.py` and `containment_check.py`; web `pnpm -C apps/web test && pnpm -C apps/web typecheck && pnpm -C apps/web build`.

---

### Task 1: Server: richer `confirm.request` and the prompt line

**Files:** `src/trillion/api/session.py` (`_send_card`), a small `tool_label` helper (new `src/trillion/spine/labels.py` or beside the session; your call, keep it pure and tested), `src/trillion/persona/prompts/text.md`, `voice.md`, tests in `tests/api/` and `tests/unit/test_persona_prompt.py`.

- [ ] Failing tests: `confirm.request` for a file create (write, recoverable), a pending action with `recoverable=False` and one with `Risk.SPEND` carries `tool_label` and `reason` (`write`, `irreversible`, `spend`; `read` for a read that somehow asks).
- [ ] `tool_label(name, definition)`: a table for first-party tools (file_create → "Create a file", file_overwrite → "Replace a file", file_append → "Add to a file", file_edit → "Edit a file", rule_keep → "Keep a standing rule", rule_drop → "Drop a standing rule", ask_schedule → "Schedule a timed ask", schedule_cancel → "Cancel a schedule", memory_forget → "Forget a memory"; check the registry for every tool that can ask and cover each); connected tools: the first sentence of the definition's description, cut to 48 characters; else the name with underscores as spaces.
- [ ] `reason`: `spend` if risk is SPEND; `irreversible` if not recoverable or risk IRREVERSIBLE; else `write` for WRITE, `read` for READ.
- [ ] Prompt line (short, in each file's voice, no em/en dashes): when asking for approval on something with content, show the content on the board; never present a widget as the approval.
- [ ] Checks; commit "Spine: approval requests carry a friendly name and a reason".

### Task 2: Web: approvals on the board

**Files:** `apps/web/src/approvals.ts` (new), `apps/web/test/approvals.test.ts` (new), `apps/web/src/board.ts`, `apps/web/src/main.ts`, `apps/web/src/view.ts` (+test), `apps/web/src/app.css` (section "Approvals", `ap-` classes), maybe `apps/web/index.html`.

- [ ] Pure helpers and tests per the spec: `approvalsOf`, `reasonWords`, `spokenHint`, `placement`, `collapseAt`, and a `toStamp(cards, drawnFull, stamped)` helper that returns the ids to send `confirm.shown` for.
- [ ] `view.ts`: card entries carry `toolLabel` and `reason` from `confirm.request`.
- [ ] The strip in the board (above the grid), with cards as the spec describes; Approve/Decline send exactly what the conversation card sends today (read main.ts for the message shape); countdown ring from `expires_at` (reuse the showcase's ring idea); outcome for 4 s then a dismissible one-line row; reduced motion.
- [ ] "From Trillion" heading above her widgets while a waiting card is pinned. The board shows with no widgets when an approval is pinned.
- [ ] `main.ts`: placement on `confirm.request` (desktop and Settings not showing → board), open the board, conversation chip instead of the card when on the board, `confirm.shown` once when the full card first renders wherever it is, close the board afterwards if it was opened only for approvals. Onboarding's example card keeps working in the conversation.
- [ ] Web checks; commit "Web: approvals open on her board".

### Task 3: Docs and the live run

- [ ] `docs/PRD.md` SUR-1 one sentence; `docs/architecture.md` web table row for `approvals.ts`; `docs/TESTING.md` §2.7 a manual check. Commit "Docs: approvals on the board".
- [ ] The controller runs the live checks.
