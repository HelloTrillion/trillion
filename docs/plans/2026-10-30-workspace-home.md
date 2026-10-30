# Workspace Home: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An empty workspace opens to Home. Home is a morning screen that greets the user, builds or opens today's briefing, and shows nine briefing widgets. Each widget is ready, asks first, or needs a connection, and has a one-tap Connect button.

**Spec:** `docs/specs/2026-10-30-workspace-home.md`. Read the whole spec first.

**Tech stack:** Python 3.13 (uv, pytest, ruff, strict mypy) and TypeScript (Vite, vitest, jsdom).

**Branch:** `workspace/home`, which holds the spec.

**House rules:**
- Commit with `git commit -s`. The message ends with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Only `git add` your own files.
- Use generic names only (`scripts/containment_check.py`).
- Write short, plain comments that say why.
- Never play sound.
- Never read, print or commit `src/trillion/connectors/google/google_client.json`.

**Checks:**
- `uv run pytest -q`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run mypy`
- `for s in containment_check check_agent_name check_raw_hex check_one_writer; do uv run python scripts/$s.py; done`
- Web: `pnpm -C apps/web test && pnpm -C apps/web typecheck && pnpm -C apps/web build`

---

### Task 1: Drive in Your day, and the Home model

**Files:**
- `src/trillion/persona/day.py`: `SetUp.drive`, `DAY_READS["google-drive"] = ("drive_search",)`, and a Drive row after Slack.
- `src/trillion/api/runtime.py`: `day_setup()` sets `drive`.
- `src/trillion/workspace/home.py`: pure. It holds the nine tiles of D3 with their text, a `home(setup, configured, google_ready, briefing_built, identity) -> dict` that builds the route's JSON, and the D8 choice.
- Tests: `tests/unit/workspace/test_home.py` and the day tests.

- [ ] Read first: `persona/day.py`, `api/runtime.py` `day_setup()` and `_reads_freely`, `connectors/mcp/catalog.py` (`BY_ID`, `how`, `category`, `service`), `workspace/packs.py`, `workspace/samples.py`.
- [ ] Write failing tests per the spec's Testing bullets, then implement, run the checks, and commit "Workspace: Home's tiles, decided by the core, and Drive in Your day".

### Task 2: The route

**Files:**
- `src/trillion/api/home.py`: `GET /api/workspace/home`. It gathers what is configured from `rt.google.saved`, `rt.slack`, `rt.mcp.states` (catalogue id → server name, on and not disabled) and the Google app's readiness, and gets `briefing` from today's day conversation and `rt.board_store`.
- Registration in `app.py`.
- `tests/api/test_home_api.py`.

- [ ] Read first: `api/conversations.py` `/day`, `api/mcp.py` and `api/google.py` (how they read state and the Google app's readiness), `storage/sqlite/board_store.py`, and an existing api test for the token and fixtures.
- [ ] Write failing tests, then implement, run the checks, and commit "Workspace: a route for Home".

### Task 3: The page

**Files:**
- `apps/web/src/home.ts`, as in the spec's "The page" section.
- `board.ts`: the `home` option.
- `connected.ts`: `start(id)` and `browse(category)`. The tile click becomes one shared function.
- `main.ts` wiring:
  - Home in the board view;
  - the actions;
  - refresh on connection changes;
  - the once-a-day opening, with its setting under Settings → General if there is a natural place, else a small toggle in Home's foot.
- `app.css`: tokens only.
- `icons.ts` if needed.
- Tests: `apps/web/test/home.test.ts`, plus additions to the board, connected and page tests.

- [ ] Read first:
  - `board.ts` (the empty state and `BoardOptions`);
  - `kit.ts` (how samples are drawn);
  - `blocks.ts` `drawBlocks`;
  - `connected.ts` `tile()`, `reveal`, `mark()`;
  - `main.ts` `showBoard`, `openBoard`, `onBoard`, `day`, `openSettings`, and how onboarding's `active(ob)` is checked;
  - `app.css` for the `bd-` and `connected-` styles and the workspace tokens.
- [ ] Write failing tests, then implement, run the web checks and raw-hex, and commit "Web: Home, the morning screen".

### Task 4: Docs

- [ ] Add a "Workspace Home" row to `docs/platforms.md`, add the manual checks to `docs/TESTING.md`, and add an "Exists:" sentence to SUR-1a in `docs/PRD.md`. Commit "Docs: Workspace Home".
