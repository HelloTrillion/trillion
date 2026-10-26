# Widget packs: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase 2 widgets (money, customers, building, work) on the design system, with three new blocks, amounts on rows, source tiles, and packs that tell her about a pack's widgets only while a matching connector is connected.

**Spec:** `docs/specs/2026-10-25-widget-packs.md` (read it, and `docs/specs/2026-10-25-widget-design-system.md` and `docs/design/widgets.md` for the system it extends).

**Architecture:** As phase 1: `blocks.py` validates blocks, `recipe_kit.py` validates what she writes, `recipes.py` expands, `widgets.py` maps names, `schema.py` holds the cards. New `packs.py` decides which cards she is told; the registry gains `redefine` so the workspace tools' description can change when connectors change. The page gains three block drawers, amounts on rows and source tiles.

**Tech stack:** Python 3.13 (uv, pytest, ruff, strict mypy); TypeScript (Vite, vitest, jsdom).

**Branch:** `workspace/widget-packs`.

**House rules:** `git commit -s`, message ending in a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; only `git add` your files; generic names only (Sam, Jo, Alex, Maple Park, Elm Street; `scripts/containment_check.py`); short plain comments that say why; never play sound; no browser. Checks: `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `python3 scripts/check_one_writer.py`, `python3 scripts/containment_check.py`, `python3 scripts/check_raw_hex.py`, `python3 scripts/check_agent_name.py`, `uv run python scripts/gen_kit_samples.py --check`; web `pnpm -C apps/web test && pnpm -C apps/web typecheck && pnpm -C apps/web build`.

---

### Task 1: Server blocks and rows

**Files:** `src/trillion/workspace/blocks.py`, `tests/workspace/test_blocks.py`.

- [ ] Failing tests: `bars`, `funnel`, `stages` with minimal and full fields and every limit in the spec (bars 1–10, values ≥ 0 and finite; funnel 2–6, counts ≥ 0; stages 2–7, `current` a label in `steps` or an index in range, else refused naming it); rows `amount` and `amount_tone` (tones as elsewhere; `amount` takes a number and writes it as text like other display fields).
- [ ] Implement; add the three to `BLOCK_TYPES`.
- [ ] Checks; commit "Workspace: bars, funnel and stages blocks, and amounts on rows".

### Task 2: Server widgets and packs

**Files:** create `src/trillion/workspace/packs.py`, `tests/workspace/test_packs.py`; modify `recipe_kit.py`, `recipes.py`, `widgets.py` (aliases, telling fields), `schema.py` (cards; the description built from `told(...)`), `samples.py`, regenerate `apps/web/src/kit/samples.json` (with `pack` per widget), `scripts/gen_kit_samples.py`, tests in `tests/workspace/`.

- [ ] Failing tests: every new type in the spec validates (minimal and full) and expands to exactly the spec's blocks; default titles for the new types; aliases; `PACKS` covers every type exactly once (phase 1 types are core); `PACK_OF` maps the spec's catalogue ids and every id exists in `connectors/mcp/catalog.py`; `told()` for none, each pack, all; the description built for `told(["core"])` lists no money types and for all lists every type; budgets: four tool definitions under 12,000 characters with core only and under 16,000 with every pack; a pack widget validates with nothing connected.
- [ ] Implement. `schema.type_cards(types)` takes the types to list; the tools build their description from it.
- [ ] Samples: one per new type, block samples for `bars`, `funnel`, `stages`; the generator writes `pack`; regenerate.
- [ ] Checks; commit "Workspace: widgets for money, customers, building and work".

### Task 3: Telling her what's connected

**Files:** `src/trillion/spine/registry.py` (+ its tests), `src/trillion/tools/workspace.py`, `src/trillion/api/runtime.py` (and wherever MCP servers are connected, disconnected and removed: `src/trillion/connectors/mcp/servers.py`, the settings API that calls it), `src/trillion/persona/prompts/text.md`, `voice.md`, tests in `tests/workspace/`, `tests/api/`, `tests/unit/test_persona_prompt.py`.

- [ ] Failing tests: `ToolRegistry.redefine(name, definition)` replaces the snapshot for a registered tool, validated like `register`; refuses an unknown name, a changed risk, a changed category; the router then uses the new definition. Through the runtime: at start with no servers the workspace tools describe core only; after a catalogue server with `catalog: "stripe"` is added and connected, `workspace_show`'s description lists `transactions`; after it is removed, not. (Use the existing MCP test server fixtures; see `tests/mcp/`.)
- [ ] Implement: a small runtime function `retell_workspace()` that computes the connected catalogue ids from the server configs that are connected (decide: connected now, or configured and enabled; prefer configured and enabled so a slow start doesn't hide widgets) and calls `redefine` for the four workspace tools. Call it at start and after every add, connect, disconnect, retune and remove.
- [ ] Prompt line (both files, short, no em or en dashes): more widget types appear in the workspace tool when a matching connector is connected; use what is listed; name connector data by the connector's name. Test.
- [ ] Checks; commit "Workspace: she's told about the widgets her connectors fill".

### Task 4: Web

**Files:** `apps/web/src/blocks.ts`, `apps/web/test/blocks.test.ts`, `apps/web/src/board.ts` (footer tile), `apps/web/test/board.test.ts`, `apps/web/src/kit.ts` (pack in captions), `apps/web/test/kit.test.ts`, `apps/web/src/main.ts` (pass the connected entries to the board), `apps/web/src/app.css` (Widget kit section).

- [ ] `bars`: label and value on one line, bar below scaled to the largest, `note` muted; tone colours the bar, else the series colour; `formatValue`-style numbers with `unit`.
- [ ] `funnel`: horizontal bars narrowing by count (widths relative to the first stage, a sensible minimum so labels fit), label and count inside or beside, "55% move on" between stages (none after a 0 stage), value muted when given.
- [ ] `stages`: dots joined by lines, done filled, current ringed, rest open; labels under, wrapping or truncating gracefully at narrow widths; screen-reader text "Step 3 of 4: Proposal".
- [ ] Rows: `amount` right-aligned, tabular, toned; `when` under it when both.
- [ ] Footer tile: board options take `connected: {id, title, color, glyph}[]` (from `connected.ts`'s data in `main.ts`); a source matching a title (case-insensitive) gets the tile (reuse `connected.ts`'s mark styling or a shared helper; no raw hex).
- [ ] `#kit` captions: "type · pack · size".
- [ ] Tests for each; web checks and raw-hex; commit "Web: bars, funnels, stages, amounts and source tiles".

### Task 5: Docs and the live run

- [ ] `docs/design/widgets.md`: new blocks, rows amount, the packs table and how packs switch, how to add a pack; `docs/architecture.md` row for `packs.py`; `docs/TESTING.md` manual checks (a pack's widgets appear after connecting a catalogue server; the footer tile). Commit "Docs: widget packs".
- [ ] The controller runs the live checks.
