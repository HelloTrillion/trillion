# Shared folders: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Shares the user adds, changes and removes while she runs (from Settings, or by her proposing on a card the user taps), each with a name, `read`/`change` access and a scope (`everyone` or one routine), plus a folder browser panel she and the user drive by voice or clicking, on macOS, Windows and Linux, in a browser and the desktop app.

**Spec:** `docs/specs/2026-10-29-shared-folders.md` (read it whole first).

**Tech stack:** Python 3.13 (uv, pytest, ruff, strict mypy), TypeScript (Vite, vitest, jsdom), Tauri v2 (Rust) for the picker.

**Branch:** `files/shared-folders` (holds the spec).

**House rules:** `git commit -s`, message ending in a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; only `git add` your files; generic names only (`scripts/containment_check.py`); short plain comments that say why; never play sound; never print or commit `src/trillion/connectors/google/google_client.json`. Checks: `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `for s in containment_check check_agent_name check_raw_hex check_one_writer; do uv run python scripts/$s.py; done`; web `pnpm -C apps/web test && pnpm -C apps/web typecheck && pnpm -C apps/web build`.

---

### Task 1: Shares and their rules

**Files:** `src/trillion/files/__init__.py`, `src/trillion/files/shares.py` (the `Share` record, the `Shares` store over `~/.trillion/folders.json` per the spec's Shares section, run-only shares from `--grant`, `in_scope(scope) -> (roots, read_only_roots)`), `src/trillion/files/rules.py` (D4: `why_not(path) -> str | None`, reusing `spine/deny_floor.py`'s `canonical` and protected checks and `paths.py` for the data directory), `src/trillion/paths.py` (the file's path), `scripts/check_one_writer.py` (only `files/shares.py` writes `folders.json`), tests in `tests/unit/files/`.

- [ ] Read first: `spine/deny_floor.py`, `tools/beneath.py`, `skills/` library config writer (the pattern for a user config file), `paths.py`, `scripts/check_one_writer.py`.
- [ ] Failing tests per the spec's Testing bullets for `test_shares.py` and `test_share_rules.py` (D4 with fake home and fake mounts; Windows and macOS spellings through the same helpers the deny floor tests use).
- [ ] Implement; checks; commit "Files: shares kept in a file, and folders that can never be shared".

### Task 2: Live shares in every turn, and read-only at the floor

**Files:** `src/trillion/api/runtime.py` (the runtime owns one `Shares`; `granted_roots` becomes a read of it; `--grant` becomes run-only shares; a removed routine's shares go with it), `src/trillion/spine/contract.py` (`ToolContext.read_only_roots`), `src/trillion/spine/deny_floor.py` (D11 for write tools, innermost share decides), `src/trillion/api/session.py` and `src/trillion/scheduler/ask.py` (contexts built with the shares in scope at call time and again when an approved card executes, per the spec), `src/trillion/persona/prompt.py` (shares listed by name and access), `src/trillion/tools/describe.py`, `src/trillion/cli/main.py`, tests.

- [ ] Read first: every `granted_roots` use (`grep -rn granted_roots src`), `router.py` confirm paths (lines ~530–700).
- [ ] Failing tests: `tests/spine/test_read_only_share.py`; a share added mid-session reaches the next tool call; a card approved after its folder was removed or set to `read` is refused at execution with the reason; a routine run reaches `everyone` plus its own only; her prompt lists shares in scope; `--grant` shares appear, are never written.
- [ ] Implement; checks; commit "Files: shares apply live, by scope, and read only holds at the floor".

### Task 3: The browser's listing

**Files:** `src/trillion/files/browse.py` (`places() -> list[Place]`, `listing(path) -> Listing` with names only, hidden and protected left out, 200 cap, the fence D6, `up`; one function per system taking its system calls as parameters, an entry point by `sys.platform`; Windows known folders via `SHGetKnownFolderPath` with ctypes and drive letters via `GetLogicalDrives`, Linux `xdg-user-dir` with a 2 s timeout), `tests/unit/files/test_browse.py`, a real-system test marked as the probes tests are.

- [ ] Failing tests per the spec; implement; checks; commit "Files: a folder listing that shows names only, inside a fence".

### Task 4: Her tools and the panel frame

**Files:** `src/trillion/tools/folders.py` (`folders_browse`, `folder_share`, `folder_unshare` per the spec's Tools table; card summary composed by the host; D4 at call and at approval), registration where file tools are registered, `src/trillion/api/session.py` (the `folders` frame; the browser's current place per session; the one-line host note on her next turn after a click), tests `tests/api/test_folders_tools.py`.

- [ ] Read first: `tools/files.py`, `tools/speak_up.py` (a narrowing tool that never asks), how a tool's result reaches the page as a frame (`workspace_show`).
- [ ] Failing tests per the spec; implement; checks; commit "Files: she browses, proposes a share on a card, and narrows at once".

### Task 5: The routes

**Files:** `src/trillion/api/folders.py` (the four routes in the spec), registration in `app.py`, `tests/api/test_folders_api.py`.

- [ ] Follow `api/skills.py`'s shape. Failing tests; implement; checks; commit "Files: routes for Settings and the panel".

### Task 6: The page

**Files:** `apps/web/src/folders.ts` (the panel and the form), Settings → Shared folders in `apps/web/src/browser.ts` rebuilt per the spec, `main.ts` wiring (the `folders` frame, the desktop picker through the bridge when present), `app.css` with design tokens only, `icons.ts`, tests `apps/web/test/folders.test.ts`.

- [ ] Read first: `browser.ts` `createFolders`, `skills.ts` (a Settings panel with cards and add/remove), `notice.ts` and `speaking.ts` (recent panels), how the board is laid out in `view.ts`.
- [ ] Failing tests per the spec; implement; web checks and raw-hex; commit "Web: shared folders and the folder browser".

### Task 7: The desktop picker

**Files:** `apps/desktop/src-tauri` (dialog plugin, `pick_folder` command, capability), the bridge the page reads, `apps/desktop` tests as they exist.

- [ ] `cargo check` and the desktop tests in CI's Desktop job; commit "Desktop: the system folder picker".

### Task 8: Docs

- [ ] `docs/platforms.md` rows from the spec's Platforms table; `docs/architecture.md` row for `trillion/files`; `docs/TESTING.md` manual checks; `docs/PRD.md` CON-3a and CON-3b "Exists:" sentences. Commit "Docs: shared folders".
