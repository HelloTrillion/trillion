# The widget design system: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every widget on her board is drawn from a small set of blocks, through server recipes, with tokens, rules, tap-to-ask and a `#kit` style guide, so built-in and custom widgets look like one system.

**Spec:** `docs/specs/2026-10-25-widget-design-system.md`. Read it first; it holds every block's and recipe's fields and limits, which this plan does not repeat.

**Architecture:** `workspace/blocks.py` validates blocks; `workspace/kit.py` validates what she writes (recipe inputs, `source`, `asks`); `workspace/recipes.py` expands a validated widget into blocks. The saved board keeps what she wrote; `service.for_page` expands it on the way out. The page draws only blocks (`blocks.ts`), with `when.ts` for times; `board.ts` keeps the grid and card shell.

**Tech stack:** Python 3.13 (uv, pytest, ruff, strict mypy); TypeScript (Vite, vitest).

**Branch:** `workspace/widget-system` (the spec is its first commit).

**House rules:** `git commit -s`, message ending in a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; only `git add` your files; no real people or businesses (generic names like Sam, Jo, Alex, Maple Park; `scripts/containment_check.py`); short plain comments that say why; never play sound; no browser (the controller checks visually). Checks: `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `python3 scripts/check_one_writer.py`, `python3 scripts/containment_check.py`, `python3 scripts/check_raw_hex.py`, `python3 scripts/check_agent_name.py`; web `pnpm -C apps/web test && pnpm -C apps/web typecheck && pnpm -C apps/web build`.

---

### Task 1: Blocks (`src/trillion/workspace/blocks.py`)

**Files:** create `src/trillion/workspace/blocks.py`, `tests/workspace/test_blocks.py`. Read `src/trillion/workspace/kit.py` first and reuse its helpers (`cap`, `_str`, `_array`, `_number`, `_shown`, `BoardError`, the caps); move a helper into a shared place only if both files need it, keeping `kit.py`'s behaviour identical.

- [ ] Failing tests first:
  - every block in the spec's table accepted with minimal and full fields; unknown fields dropped; strings trimmed and capped;
  - tones: `good|bad|attention|neutral` accepted, `up`→`good`, `down`→`bad`, anything else refused naming the block;
  - limits refused with a message naming the widget, the block ("block 3 (meter)") and the problem: sparkline with 1 or 61 values, meter `max <= 0`, people with 9 names, chips with 7, facts with 11, quote over 600, text over 2000, columns with 1 or 5 columns, columns inside columns, 9 top-level blocks, 9 blocks in a column, rows over 30 items across the card, `highlight` outside the rows, a `time`/`when` that is not ISO 8601 (`2026-10-25T14:30`, `2026-10-25T14:30:00Z`, `2026-10-25T14:30-07:00` and a date `2026-10-25` accepted);
  - `strip_emoji(text)` removes emoji and pictographs (and variation selectors and joiners left behind) and keeps letters, digits, punctuation, `$ € £ % ° → ✓`-style symbols used in data; applied to every string in a block.
- [ ] Implement `validate_blocks(raw, where) -> list[Block]` and `strip_emoji`. `chart`, `table` and `timeline` reuse the existing checks in `kit.py` (call them, do not copy them).
- [ ] Checks; commit "Workspace: blocks every widget is made of".

### Task 2: Recipes and the kit

**Files:** create `src/trillion/workspace/recipes.py`, `tests/workspace/test_recipes.py`; modify `src/trillion/workspace/kit.py`, `tests/workspace/test_kit.py` (or the existing kit tests; find them with `grep -rl validate_widget tests`).

- [ ] Failing tests first:
  - `validate_widget` accepts every type in the spec's recipe table with minimal and full fields, including `custom` (via `validate_blocks`);
  - `source` as a string becomes `{"name": …}` (≤30); an `at` she sends is dropped; `asks` capped at 2 and 40 characters, emoji stripped, empty ones dropped;
  - emoji stripped from every string of every type;
  - the lenient names: `agenda`→`timeline`, `next`/`next_meeting`→`next_up`, `inbox_summary`/`email`→`inbox`, `checklist`/`steps`→`plan` (check the existing alias table first: `checklist` maps to `list` today; change it to `plan` and update its test), `callout`/`alert`/`banner`→`callout`, `goal`/`progress`→`goal`;
  - `expand(widget) -> list[Block]` for every recipe gives exactly the spec's blocks (one test per recipe, asserting the block list), e.g. `plan` with 2 of 5 done → `meter {value: 2, max: 5, label: "2 of 5 done"}` then `rows`; `free_time` groups slots by date as written and formats `9:00–10:30` and `2:00–3:00 pm` style ranges; `decision` totals each option's scores and highlights the pick's row; `compare` keeps today's fields and becomes `columns` then `text`;
  - every block `expand` returns passes `validate_blocks` (property-style loop over the samples from Task 4 once they exist; for now over the recipe tests' inputs);
  - a board saved with today's seven types (copy a fixture from `tests/workspace/test_board.py`) validates and expands.
- [ ] Implement: new validators in `kit.py` for the new types, `source`, `asks`; `recipes.py` with one function per type and `expand(widget)`, `expand_board(board)` (each widget gains `blocks` and keeps `id`, `type`, `size`, `title`, `source`, `asks`; recipe fields are not sent).
- [ ] Default sizes for new types when `size` is absent: `next_up`, `goal`, `callout`, `facts`, `quote` → `small`; `decision`, `mentions` → `wide`; others `medium`.
- [ ] `board.summary` names the new types sensibly (`w3 next up "Design review"`).
- [ ] Checks; commit "Workspace: recipes turn her widgets into blocks".

### Task 3: Schema, tools, service

**Files:** modify `src/trillion/workspace/schema.py`, `src/trillion/tools/workspace.py`, `src/trillion/workspace/service.py`; tests in `tests/workspace/test_schema.py` (create if absent), `tests/workspace/test_service.py`, `tests/workspace/test_tools.py`.

- [ ] Failing tests:
  - the widget schema lists every type and the new fields, still flat (no `oneOf`, no `enum`), and `blocks` items are loose objects with `type`;
  - `workspace_show`'s description carries a one-line card per type (`next_up: event, start, end?, where?, people[]?, note?`), and the whole JSON of the four tool definitions is under 12,000 characters;
  - service: a widget with `source` saved by `show` or `update` gets `source.at` from the service's clock as ISO 8601 UTC (`…Z`); a widget kept unchanged keeps its `at`; `for_page` returns expanded widgets with `blocks` (and the stored board is unchanged).
- [ ] Implement. The clock: the service already has one for `put` (`now_ms`); use it.
- [ ] Checks; commit "Workspace: the kit's new widgets reach her tools and the page".

### Task 4: Samples and the prompt

**Files:** create `src/trillion/workspace/samples.py`, `scripts/gen_kit_samples.py`, `apps/web/src/kit/samples.json` (generated), `tests/workspace/test_samples.py`; modify `src/trillion/persona/prompts/text.md`, `src/trillion/persona/prompts/voice.md`, `tests/unit/test_persona_prompt.py`.

- [ ] `samples.py`: `SAMPLES: list[dict]`, one recipe input per type (two for `custom`: "Who's on call" with rows of people and chips, and a `columns` example), realistic, generic names (Sam, Jo, Alex, Maple Park), each with a `source` where it would come from a connector and an ask where one is obvious. Plus `BLOCK_SAMPLES`: one minimal `custom` widget per block type, for the style guide's block section.
- [ ] `scripts/gen_kit_samples.py`: validates each sample, stamps a fixed `source.at` (`2026-10-25T09:00:00Z`), expands, writes `apps/web/src/kit/samples.json` as `{"widgets": [...], "blocks": [...]}` with stable formatting (`indent=2`, sorted nothing, trailing newline). `--check` exits 1 when the file differs.
- [ ] Test: running the generator in check mode passes; every sample validates; each recipe type appears at least once.
- [ ] Prompt (both files, short, each in its own voice, no em or en dashes): the catalogue in one line; prefer a catalogue widget, make `custom` from blocks only when none fits; the spec's eight rules, condensed; name the source of connector data; asks at most two and only for the obvious next question. Test that both prompts mention `custom` and `source`.
- [ ] Checks; commit "Workspace: samples for the style guide, and her rules for widgets".

### Task 5: Asks can't approve

**Files:** modify `src/trillion/spine/contract.py` (`Via` gains `"ask"`), `src/trillion/spine/confirm.py` (`decide`), the `Refusal` enum (find it with `grep -rn "class Refusal" src`), `src/trillion/api/session.py` (`turn.text` with `from: "ask"`; `_via` literal); tests in `tests/spine/` (beside the existing `decide` tests) and `tests/api/` (beside `test_approval_card.py`).

- [ ] Failing tests:
  - `decide(..., "confirm", ctx(via="ask"))` → `Rejection(Refusal.ASK, "a tap on a widget can't approve; use the card")`; `"cancel"` with `via="ask"` → `REJECTED`;
  - through the session with the scripted provider: a waiting approval, then `turn.text {text: "Yes, go ahead", from: "ask"}` whose scripted reply calls `confirm_pending_action` → the action stays pending and the tool result is the refusal; the same text without `from` approves as today (for a recoverable write);
  - any other `from` value is treated as plain text.
- [ ] Implement. Check every place that matches on `Via` (mypy will help; `grep -rn "via ==" src`) so `"ask"` is handled like `"text"` everywhere except `decide`.
- [ ] Checks; commit "Spine: a tap on a widget can't approve".

### Task 6: Web: times and blocks

**Files:** create `apps/web/src/when.ts`, `apps/web/test/when.test.ts`, `apps/web/src/blocks.ts`, `apps/web/test/blocks.test.ts`; modify `apps/web/src/app.css` (new section "Widget kit": `--w-` tokens and `bk-` classes). Read `apps/web/src/board.ts` first: the chart, donut, table, timeline, note and compare drawing there moves into blocks (reuse it; do not rewrite `chart.ts`).

- [ ] `when.ts`, pure, tested with a fixed `now`:
  - `parseWhen(text)`: ISO with offset or `Z` is absolute; without one it is local; a bare date is local midnight; anything else `null`;
  - `relative(at, now)`: "now", "in 25 min", "in 2 h 10 min", "started 5 min ago", "5 min ago", "yesterday 3:00 pm", "Tue 3:00 pm" (within 6 days), "Oct 12" (further);
  - `range(at, end)`: "2:30–3:00 pm", "11:30 am–12:15 pm", across days "Tue 3:00 pm–Wed 9:00 am";
  - `age(at, now)`: "just now", "2 min ago", "1 h ago", "yesterday".
- [ ] `blocks.ts`:
  - pure helpers, tested: `initials(name)` ("Sam" → "S", "Jo Lee" → "JL", "maple park team" → "MP"); `tintOf(name)` → 0…4, stable; `heroIndex(blocks)` (the first top-level `value`); `toneClass(tone)`;
  - `drawBlocks(blocks, { now }) -> { el, tick(now), dispose }`: one function per block per the spec, `textContent` only, never `innerHTML`; time-bearing elements carry `data-at` and are refreshed by `tick`; an unknown block type is skipped; `columns` draws its columns side by side and stacks under a container width of 420px (container query on the card).
  - DOM tests (jsdom, as the existing board tests do): each block shows its fields; the hero rule; an unknown block is skipped; a `<script>`-like string appears as text.
- [ ] CSS: tokens per the spec (type: hero value, value, title, body, label, meta; spacing 4/8/12/16; tones good/bad/attention/neutral with fill, text and tint; avatar tints from `--chart-0…4` at low strength), both themes, reduced motion (no entrance animation), no raw hex (`black`/`white` or tokens).
- [ ] Web checks and the raw-hex check; commit "Web: blocks and times for her widgets".

### Task 7: Web: the board draws blocks, with sources and asks

**Files:** modify `apps/web/src/board.ts` (+ `apps/web/test/board.test.ts`), `apps/web/src/main.ts`, `apps/web/src/view.ts` if it types widgets, `apps/web/src/app.css`.

- [ ] Cards draw `widget.blocks` with `drawBlocks`; remove the old per-type drawing now inside blocks (keep pure helpers other code imports; move their tests if they move). Keep `planRows`/`rowTemplate`, in-place reconcile, `revive`, `dispose`, the × and the dock-hole clearance exactly as they behave now.
- [ ] Footer: `source.name` and `age(source.at)` ("Calendar · 2 min ago"); none when there's no source.
- [ ] Asks: quiet links "Prep me for this ↗" at the card's foot; `onAsk(text)` option. One board-wide minute timer calls every card's `tick`; cleared on dispose.
- [ ] `main.ts`: `onAsk` sends `{type: "turn.text", text, from: "ask"}` the same way the ask bar sends a typed turn (find it); asks are hidden (a class on the board) while `approvals.ts` has a waiting card.
- [ ] Tests: the footer, the asks and their message, asks hidden while an approval waits, the timer cleared on dispose, a board from before this change (no `blocks`: draw nothing for that card's body rather than throw).
- [ ] Web checks; commit "Web: her board draws blocks, with sources and asks".

### Task 8: Web: the `#kit` style guide

**Files:** create `apps/web/src/kit.ts`, `apps/web/test/kit.test.ts`; modify `apps/web/src/main.ts` (route `#kit`, the way `#showcase` is routed), `apps/web/src/app.css`.

- [ ] A page over the galaxy, using the board's own card drawing: a header with a light/dark switch and a narrow/wide switch; "Blocks" (each block sample in a card, labelled with its block name); "Widgets" (every recipe sample, labelled with its type); "Rules" (the spec's eight rules, as text). Asks on the page do nothing but flash "would ask: …".
- [ ] Test: every sample in `samples.json` is drawn once; the switches set `data-theme` and the width class.
- [ ] Web checks; commit "Web: the widget style guide at #kit".

### Task 9: Docs

**Files:** create `docs/design/widgets.md`; modify `docs/PRD.md` (SUR-1, one sentence), `docs/architecture.md` (rows for `blocks.py`, `recipes.py`, `blocks.ts`, `when.ts`, `kit.ts`), `docs/TESTING.md` (a manual check for `#kit` and an ask tap).

- [ ] `widgets.md`: principles (one job per widget, content is hers and emphasis is the system's), tokens, the block table, the recipe table, the rules, how to add a block or a recipe (blocks.py → recipes.py → samples.py → regenerate → blocks.ts → `#kit`), and phases 2 and 3 in a short "Next" section.
- [ ] Full check suite; commit "Docs: the widget design system".
- [ ] The controller runs the live checks.
