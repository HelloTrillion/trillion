# Voice Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make her typed answers brief, her spoken answers short, and her "the data isn't there" answers one plain line, as set out in [docs/specs/2026-11-01-voice-fixes.md](../specs/2026-11-01-voice-fixes.md).

**Architecture:** Prompt text only. Three overlay files under `src/trillion/persona/prompts/` change (`voice.md`, `text.md`, `rules.md`), their unit tests follow, and the PRD's overlay lines match. The persona gate then measures the change on gemma4:e4b, then once on Claude, and both baselines are re-saved because the prompts' fingerprint changes.

**Tech Stack:** Markdown prompts, pytest, `evals/gate.py`, Ollama (gemma4:e4b), the Anthropic API (owner's key, owner's terminal).

**Branch:** `voice/fixes` (already created from main at 4a9cbdc).

---

### Task 1: The prompt changes and their tests

**Files:**
- Modify: `src/trillion/persona/prompts/voice.md` (one line)
- Modify: `src/trillion/persona/prompts/text.md` (lines 1 and 9)
- Modify: `src/trillion/persona/prompts/rules.md:15`
- Modify: `docs/PRD.md:739-741`
- Test: `tests/unit/test_persona_prompt.py`

Context for the implementer: `system_blocks(identity, soul)` builds the typed system prompt (it includes `rules.md` and `text.md`); `volatile_context(now, [], spoken=identity)` builds the per-turn context, which includes `voice.md` when the message was spoken. `{user_name}` is filled in from the identity. No prompt may contain an em dash (U+2014) or en dash (U+2013). Look at `test_she_answers_first_then_reaches_for_a_tool_in_both_modalities` and `test_she_offers_only_what_her_tools_reach` for the pattern.

- [ ] **Step 1: Write the failing tests.** Add to `tests/unit/test_persona_prompt.py`, after `test_she_offers_only_what_her_tools_reach`:

```python
def test_spoken_replies_are_short_and_unlabelled() -> None:
    # The Claude baseline: spoken answers ran 45 to 58 words, often after "Short version:".
    ident = Identity(agent_name="Nova", user_name="Sam")
    spoken = volatile_context(datetime(2026, 11, 1, tzinfo=UTC), [], spoken=ident)
    assert "One or two short sentences, about thirty words in all" in spoken
    assert 'No label such as "Short version:" before it' in spoken
    assert "under ten seconds" not in spoken


def test_typed_replies_offer_the_rest_instead_of_a_list() -> None:
    # The Claude baseline: typed how-to answers came as bolded lists with a
    # "What makes it work" section, which her soul calls essay mode.
    typed = system_blocks(Identity(agent_name="Nova", user_name="Sam"), "soul")[0].text
    assert "give the one thing that matters in a sentence or two, then offer the rest" in typed
    assert "Write a list only when asked for steps, options or a plan" in typed
    assert "no bold labels" in typed
    assert "A draft that was asked for, such as an email, comes whole." in typed
    assert "Light markdown is fine" not in typed


def test_quick_reads_get_no_line_first() -> None:
    # The Claude baseline: every numbers answer opened "Checking what I can actually
    # see." before reading the shared folder, a read that takes no time.
    ident = Identity(agent_name="Nova", user_name="Sam")
    typed = system_blocks(ident, "soul")[0].text
    spoken = volatile_context(datetime(2026, 11, 1, tzinfo=UTC), [], spoken=ident)
    for text in (typed, spoken):
        assert "a read that takes a moment, such as a connected app or the web" in text
        assert "Your shared folders and your memory are quick: read them without a line first." in text


def test_missing_data_is_one_line() -> None:
    (block,) = system_blocks(Identity(agent_name="Nova", user_name="Sam"), "soul")
    assert "say so in one line: that it is not in anything you can see" in block.text
    assert "the one thing that would get it" in block.text
```

Check that `system_blocks(..., "soul")` is how the neighbouring tests pass a soul; if they use `load_soul(tmp_path)`, do the same.

- [ ] **Step 2: Run them and see them fail.**

Run: `uv run pytest tests/unit/test_persona_prompt.py -q -k "short_and_unlabelled or offer_the_rest or quick_reads or one_line"`
Expected: 4 failed (the new strings aren't in the prompts yet).

- [ ] **Step 3: Change `voice.md`.** In its single line, replace

`Your reply will be heard, not read. One or two sentences, under ten seconds aloud, leading with the answer.`

with

`Your reply will be heard, not read. One or two short sentences, about thirty words in all, leading with the answer. No label such as "Short version:" before it; if there is more, offer it.`

and replace

`When answering needs a tool that reads something, such as a connected app, the web or a file, say one short line first, before you call it, that answers what you can or says what you are getting, such as "Reading that page now."`

with

`When answering needs a read that takes a moment, such as a connected app or the web, say one short line first, before you call it, that answers what you can or says what you are getting, such as "Reading that page now." Your shared folders and your memory are quick: read them without a line first.`

Leave everything else in the line as it is.

- [ ] **Step 4: Change `text.md`.** In line 1, replace

`Light markdown is fine when it helps: a short list, a code span. No headings, no tables unless asked.`

with

`Still brief: give the one thing that matters in a sentence or two, then offer the rest, such as "Want the steps?" Write a list only when asked for steps, options or a plan, and keep it to a few short lines, with no bold labels and nothing after it on why it works. A draft that was asked for, such as an email, comes whole. A code span is fine. No headings, no tables unless asked.`

and delete the trailing ` Still brief.` at the end of line 1 (keep the dash rule before it). In line 9 (REACHING FOR A TOOL), make the same replacement as the second one in Step 3.

- [ ] **Step 5: Change `rules.md` line 15.** Replace

`say plainly that it is not in anything you can see, and what would get it, such as connecting the app in Settings or sending the file.`

with

`say so in one line: that it is not in anything you can see, and the one thing that would get it, such as connecting the app in Settings or sending the file.`

- [ ] **Step 6: Run the prompt tests.**

Run: `uv run pytest tests/unit/test_persona_prompt.py -q`
Expected: all pass, including the 4 new ones and the existing `say one short line first, before you call it` and `Offer only what your tools can reach.` checks.

- [ ] **Step 7: Update the PRD.** In `docs/PRD.md` around line 739, make the voice overlay read: "one or two short sentences, about thirty words in all, with no label such as \"Short version:\". Lead with the answer." and add after "Summarize tool results; never narrate the tool call.": "Say a line before a read only when it takes a moment, such as a connected app or the web." Make the text overlay read: "brief: the one thing that matters in a sentence or two, then offer the rest. A list only when asked for steps, options or a plan, short and without bold labels; a requested draft comes whole." followed by the existing quote.

- [ ] **Step 8: Run the full checks.**

Run: `uv run pytest -q -x --ignore=tests/spine 2>&1 | tail -3 && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/containment_check.py`
Expected: all pass, except `tests/evals/test_baselines.py`, which fails because no baseline matches the new fingerprint. That is expected until Task 3; note it and don't change it.

- [ ] **Step 9: Commit.**

```bash
git add src/trillion/persona/prompts/voice.md src/trillion/persona/prompts/text.md src/trillion/persona/prompts/rules.md tests/unit/test_persona_prompt.py docs/PRD.md
git commit -s -m "Persona: brief when typed, short when spoken, one line when the data isn't there

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 2: Measure on gemma (controller)

- [ ] **Step 1: One gemma run, nothing saved.**

Run (background, about 40 minutes): `env -u TRILLION_ANTHROPIC_API_KEY uv run python -m evals.gate --model gemma4:e4b --runs 1 > <scratchpad>/voice-gemma-1.txt`

- [ ] **Step 2: Read it against the three-run baseline** (mean 3.53; voice_length 92.3%; repeated_opener 96%; numbers 3.17; everyday 3.22; voice-length 2.53). Look at the everyday, numbers and voice-length replies themselves, not just the scores. A single run is noisy, so a drop matters only if the replies show the change caused it.
- [ ] **Step 3: If a change made her worse,** rework that sentence (back to Task 1's steps for that file and its test) and run again. At most two rounds before reporting to the owner.

### Task 3: Save the baselines

- [ ] **Step 1: Three-run gemma baseline** (background, about 2 hours):

`env -u TRILLION_ANTHROPIC_API_KEY uv run python -m evals.gate --model gemma4:e4b --save-baseline [--accept-drop "<why>"] > <scratchpad>/voice-gemma-3.txt`

Use `--accept-drop` only if a drop is explained by the change being intended (for example a longer reply now offered instead of given), and say so in the reason.

- [ ] **Step 2: Claude run in the owner's terminal.** Use `run_in_terminal` with:

`cd ~/code/Hello-Trillion-Public && read -rs "TRILLION_ANTHROPIC_API_KEY?Anthropic key: " && export TRILLION_ANTHROPIC_API_KEY && echo && uv run python -m evals.gate --model claude-opus-5-5 --save-baseline`

and tell the owner to type the key there. Never ask for the key in chat.

- [ ] **Step 3: Check it against Done** (spec): voice_length ≥ 95%, repeated_opener ≥ 95%, every group ≥ 3.3, no drop past the gate's limits. Report the result either way.
- [ ] **Step 4:** `uv run pytest tests/evals -q` passes (both baselines match the new fingerprint).

### Task 4: Record and open the PR (controller)

- [ ] **Step 1:** Add a "First run" section to the spec with both models' before and after numbers.
- [ ] **Step 2:** Commit the two baselines and the spec (`git commit -s`, Co-Authored-By line).
- [ ] **Step 3:** Push `voice/fixes`, open the PR (body ends with the Claude Code line), bind it, and watch CI with a background `gh run watch`.
