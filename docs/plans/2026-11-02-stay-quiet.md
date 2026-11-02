# Staying Quiet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** She can choose to say nothing: `listening` when the user trails off mid-thought (spoken), `done` when they sign off. Her spoken style gives thinking aloud room. See [docs/specs/2026-11-02-stay-quiet.md](../specs/2026-11-02-stay-quiet.md).

**Architecture:**
- A `stay_quiet` tool ends a turn without text: the loop returns `TurnDone(stop="quiet", quiet=kind)`.
- The session keeps the microphone open 8 seconds after `listening` and nudges once through a host note; after `done` it closes the microphone and sends a saved "seen" mark.
- Prompt changes teach when to use it.
- The persona eval learns cases that expect quiet, plus two new code checks.

**Tech Stack:** Python 3.13, asyncio, pytest; TypeScript (Vite) in `apps/web` with its own tests (`npm test` there; see `apps/web/package.json`).

**Branch:** `voice/stay-quiet` (from main at ba3a869).

**Rules for every task:**
- Commit with `git commit -s` and end the message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No em or en dashes in prompts or code comments. Generic names only (Sam, example.com).
- Never open `src/trillion/connectors/google/google_client.json`. Never play sound. Don't touch ports 7777, 7778 or 7790.
- No eval runs in Tasks 1 to 5: the controller runs them in Task 6.
- Full check before each commit:
  - `uv run pytest -q -x --ignore=tests/spine 2>&1 | tail -2 && uv run pytest tests/spine -q 2>&1 | tail -2 && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/containment_check.py`
  - plus, for web changes, the web app's typecheck and tests.
- From Task 4 on, `tests/evals/test_baselines.py` fails, because the prompts' fingerprint changes. That is expected until Task 6; note it, don't work around it.

## File map

| File | Change |
|---|---|
| `src/trillion/tools/quiet.py` (new) | The `stay_quiet` tool |
| `src/trillion/brain/loop.py` | A quiet round ends the turn: `TurnDone.quiet` |
| `src/trillion/api/runtime.py` | Register the tool |
| `src/trillion/api/session.py`, `src/trillion/api/listening.py` | The 8-second window, the nudge, `done`, the "seen" mark, the hot-mic race |
| `apps/web/src/*` | Render and replay "seen" |
| `src/trillion/persona/prompts/voice.md`, `text.md`, `docs/PRD.md` | Her style |
| `tests/unit/test_persona_prompt.py` | Prompt assertions |
| `evals/persona/cases.py`, `checks.py`, `run.py`, `evals/harness.py` | Quiet cases, `answered`, `no_menu`, `one_question` |

---

### Task 1: The tool and the loop

**Files:**
- Create: `src/trillion/tools/quiet.py`
- Modify: `src/trillion/brain/loop.py` (`TurnDone`, around line 84; the round handling, around line 276)
- Modify: `src/trillion/api/runtime.py` (where built-in tools are registered)
- Test: `tests/unit/test_quiet_tool.py`, `tests/brain/` (find the loop's tests; e.g. `grep -rl "run_turn" tests/brain`)

- [ ] **Step 1: Read first.**
  - How a simple read-only built-in tool is defined and registered: `src/trillion/tools/describe.py`, `src/trillion/tools/day.py`.
  - `ToolContext.via` (`src/trillion/spine/contract.py`).
  - How unattended runs filter tools (`registry.unattended()` / `offered()` in `src/trillion/spine/registry.py`).
  - How a host note's turn is marked. Find how `heads_up_msg` / `background_msg` turns run in `session.py`, and whether `ToolContext` or the session knows the turn was started by a host note rather than the user. If nothing does, add `host_turn: bool = False` to `ToolContext`, set by the session for note-started turns.

- [ ] **Step 2: Write the failing tool tests** (`tests/unit/test_quiet_tool.py`):

```python
async def test_done_is_accepted_spoken_and_typed() -> None:
    for via in ("voice", "text"):
        r = await StayQuiet().execute({"kind": "done"}, ctx(via=via))
        assert r.ok and r.effect == "none"


async def test_listening_is_spoken_only() -> None:
    assert (await StayQuiet().execute({"kind": "listening"}, ctx(via="voice"))).ok
    typed = await StayQuiet().execute({"kind": "listening"}, ctx(via="text"))
    assert not typed.ok and "answer" in typed.content


async def test_never_on_a_host_note_turn() -> None:
    r = await StayQuiet().execute({"kind": "done"}, ctx(via="voice", host_turn=True))
    assert not r.ok


def test_not_offered_unattended(registry_with_quiet) -> None:
    assert "stay_quiet" not in {d.name for d in registry_with_quiet.unattended()}
```

`ctx(...)` builds a `ToolContext` (copy the helper the spine tests use). Use whatever "not offered unattended" mechanism the registry has, such as a category or flag.

- [ ] **Step 3: Write the tool.**

```python
"""Saying nothing this turn (VOI-7). The loop ends the turn on it; the session does the rest."""

QUIET = "stay_quiet"
KINDS = ("listening", "done")


class StayQuiet(BaseTool):
    definition = ToolDefinition(
        name=QUIET,
        description=(
            "Say nothing this turn. listening: they are still thinking aloud or trailed off"
            " mid-thought (spoken only). done: they signed off, such as \"okay, thanks\" or"
            " \"bye\". Never after a clear question. Call it alone, with no text."
        ),
        input_schema={
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": list(KINDS)}},
            "required": ["kind"],
            "additionalProperties": False,
        },
        risk=Risk.READ,
        category="host",
        timeout_s=1.0,
    )

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        kind = args.get("kind")
        if ctx.host_turn:
            return _no("this turn came from the host, not from them: answer it")
        if kind == "listening" and ctx.via != "voice":
            return _no("typed: there is no pause to wait through, so answer it")
        return ToolResult(ok=True, content=f"quiet: {kind}", effect="none")
```

`_no(text)` returns `ToolResult(ok=False, content=text, effect="none", error_kind="refused")`. Match how other tools mark a refusal and how the router wraps results; read-only tools may need `effect="confirmed"` to count as success. Check `final_status` and the direct path in `router.py`, and pick what makes an accepted call read as success.

- [ ] **Step 4: Write the failing loop tests** (in the loop's test file, using its scripted provider):
  - A round with only `stay_quiet` `done`, no text → `TurnDone.stop == "quiet"`, `TurnDone.quiet == "done"`. The assistant tool_use and its result are saved as a pair, and no further round runs.
  - The same with `listening` on a voice turn → `quiet == "listening"`.
  - A refused `stay_quiet` (typed `listening`) → the loop goes on to another round, as with any tool result, and the scripted model's next answer is the reply.
  - Text plus `stay_quiet` → refused ("call it alone"); the text is the reply.
  - `stay_quiet` plus another tool → `stay_quiet` refused, the other runs as normal.
  - No text and no tools → still `stop == "empty"`.

- [ ] **Step 5: The loop.**
  - `TurnDone` gains `quiet: str | None = None`.
  - Before `round_trip()`, if `QUIET` is among `uses` and (`text` or `len(uses) > 1`), answer that call with a refusal result rather than running it. Do this in `run_all`'s per-call path, or by pre-building its result, whichever fits the code.
  - After `round_trip()`: if the round was exactly one `stay_quiet` call, with no text, and its result was ok, return `TurnDone(stop="quiet", quiet=kind, ...)` through `done`.
  - Make sure the session's `turn.complete` carries `stop="quiet"` and the kind (Task 2 uses it).

- [ ] **Step 6: Register the tool** in `runtime.py` with the other built-ins.
  - Check `describe_self` (BRN-6) lists it sensibly or skips host tools, as it does for others.
  - Check `api/draft.py`: a VOI-12 draft builds its call with the tool list, and must end the same way.

- [ ] **Step 7: Run the tests, the full check, and commit:** "Brain: she can say nothing this turn (stay_quiet)".

### Task 2: The session

**Files:**
- Modify: `src/trillion/api/session.py`, `src/trillion/api/listening.py`
- Test: the session tests (`grep -rl "FOLLOW_UP_S\|follow_up" tests/` to find where the hot mic is tested; add `tests/api/test_stay_quiet.py` beside them if there's a natural home)

Context from the explore:
- `_heard` (`session.py` around 1974) saves a spoken user message and runs the turn.
- `_one_turn` (2160) → `run_turn`.
- The hot mic opens on `playback.ended` (1053-1072) when `_answered_aloud()` and `self._task is None`, via `_on_listen(FOLLOW_UP_S, follow_up=True)` → `_listen` (1810) → `mic.start`.
- `turn.complete` is sent around 2334.

- [ ] **Step 1: Failing tests** (drive a session with a scripted provider and a fake recognizer, the way existing session tests do; read two of them first):
  1. A spoken turn ending in quiet `listening`:
     - no audio, orb `listening`, `mic.start` with an 8-second window;
     - with nothing heard in the window, one nudge turn runs, whose last user-role message is the host note and is spoken;
     - after it, the usual follow-up window.
  2. Speech inside the window → a normal turn, no nudge.
  3. A nudge turn that itself ends quiet → no second nudge. A nudge turn's `stay_quiet` `listening` is refused anyway, because it is a host turn.
  4. Quiet `done`, spoken: `mic.stop`, orb `idle`, `turn.complete` with `stop: "quiet"`, `quiet: "done"`, a `seen` message, no follow-up window.
  5. Quiet `done`, typed: a `seen` message, and after reloading the conversation (whatever `history`/replay the page asks for) the mark is there.
  6. The race: `playback.ended` arrives while `_task` is still set, after a spoken turn with no audio, and the follow-up window still opens once the task clears.

- [ ] **Step 2: The window and the nudge.**
  - In `listening.py`: `QUIET_LISTEN_S = 8.0`.
  - When `run_turn` returns `quiet == "listening"`, skip speech and call the listen path with `QUIET_LISTEN_S`, marked as a quiet window. That follows how `follow_up=True` is passed, as `quiet=True`.
  - Where the listener ends with nothing heard (the "unheard" path, around 2032, or the window's timeout; find which fires when a follow-up window passes in silence), check for a quiet window that has not nudged yet, and start the nudge.
  - The nudge is a turn whose message is a host note, built like `heads_up_msg` / `background_msg`:

    "They went quiet after trailing off. Say one short line inviting them to go on, in your own words, or call stay_quiet with done if they seem finished."

    Mark the turn `host_turn=True` and spoken.
  - Keep one flag per quiet window so a nudge never chains. Make sure a cancel, a new user turn, the tab closing or the session ending clears it.

- [ ] **Step 3: Done.** On `quiet == "done"`:
  - Spoken: send `mic.stop`, rest the orb, and open no follow-up window.
  - Both spoken and typed: send `{"type": "seen", "message_id": <the user message's id>}`, and save the mark.
  - Find where a message's metadata or a per-message flag can be stored. The conversation store may have a meta column; if none fits, the saved `stay_quiet` tool pair already records it, and the replay can derive "seen" from it: the user message is followed by an assistant `stay_quiet` `done` call that was ok. Prefer deriving: no migration.

- [ ] **Step 4: The race.** Move the follow-up decision so it runs after `_task` clears. For example, when `playback.ended` arrives while the task is set, remember it, and decide in `_run`'s `finally`. Test 6 proves it.

- [ ] **Step 5: Full check, then commit:** "Session: wait through a trailed-off thought, nudge once, and rest after a sign-off".

### Task 3: The page

**Files:** `apps/web/src/` (`main.ts`, `view.ts`, the conversation replay code; find where a `turn.complete` and history are rendered), and the web tests.

- [ ] **Step 1: Failing web tests.** A `seen` message renders a small "seen" mark under that user message. A replayed conversation whose user message is followed by a `stay_quiet` `done` shows the mark. A `turn.complete` with `stop: "quiet"` shows no "model sent back nothing" notice.
- [ ] **Step 2: Implement.**
  - The mark is quiet text, styled like the message's existing small metadata, using the design tokens. Not a bubble. Label "Seen". Accessible: the live region announces nothing for it.
  - The "empty" notice stays for `stop: "empty"` only.
- [ ] **Step 3: Web typecheck and tests, the Python full check, then commit:** "Web: a sign-off shows as seen, not unanswered".

### Task 4: Her style

**Files:**
- Modify: `src/trillion/persona/prompts/voice.md`, `src/trillion/persona/prompts/text.md`, `docs/PRD.md` (PER-3 voice and text overlays; VOI-7)
- Test: `tests/unit/test_persona_prompt.py`

- [ ] **Step 1: Failing tests:**

```python
def test_spoken_replies_leave_room() -> None:
    spoken = _spoken()  # volatile_context(..., spoken=ident), as the file's other tests do
    assert "stay_quiet" in spoken
    assert "with done" in spoken and "with listening" in spoken
    assert "A clear, finished question always gets an answer" in spoken
    assert "is not a decision" in spoken
    assert "At most one question" in spoken
    assert "if there is more, offer it" not in spoken


def test_a_typed_sign_off_is_quiet_too() -> None:
    typed = _typed()
    assert "stay_quiet with done" in typed
    assert "with listening" not in typed
```

Update `test_spoken_replies_are_short_and_unlabelled`, which asserts the old "if there is more, offer it" wording, to the new sentence.

- [ ] **Step 2: `voice.md`.** Replace `No label such as "Short version:" before it; if there is more, offer it.` with:

`No label such as "Short version:" before it. Say the one thing; if they want more, they will ask. When they sign off, such as "okay, thanks", "that's all" or "bye", call stay_quiet with done and say nothing. When they are thinking aloud and trail off on an unfinished thought, such as "I'm just wondering if" or "maybe what I need is", call stay_quiet with listening and let them go on. A clear, finished question always gets an answer, at once, however softly it ends. When they think aloud, help with the thought they are on: don't solve it before they ask, offer a menu, or ask them to pick a direction, and something they muse about is not a decision or a go-ahead. At most one question, and only when it moves things on. When they correct you, say in a few words that you have it, use it, and drop the old idea.`

No em or en dashes.

- [ ] **Step 3: `text.md`.** After its brevity sentence, add: `When they sign off, such as "thanks, that's all", call stay_quiet with done and write nothing.`
- [ ] **Step 4: PRD.**
  - PER-3's voice overlay gets a short version of Step 2.
  - VOI-7 gets an "Exists:" note naming this spec.
  - `docs/architecture.md:24` no longer says sign-off detection is unbuilt.
- [ ] **Step 5: Tests, the full check, then commit:** "Persona: she leaves room when you think aloud and says nothing when you sign off". `tests/evals/test_baselines.py` fails, as expected; say so in the commit body.

### Task 5: The eval learns quiet

**Files:** `evals/persona/cases.py`, `evals/persona/checks.py`, `evals/persona/run.py`, `evals/harness.py`, `tests/evals/`

- [ ] **Step 1: Read** `evals/persona/run.py` (`ask`, `code_checks`, `summarize`), `evals/harness.py` (`Turn`, how `turn.complete` is read, `converse`), `checks.py`, and their tests in `tests/evals/`.
- [ ] **Step 2: Failing tests (no model):**
  - `Turn` records `stop` and `quiet` from `turn.complete`.
  - A case with `quiet="done"` whose turn ended quiet `done` passes, is not retried, and is not sent to the judge.
  - The same case answered fails `answered` in reverse; call that check `quiet`.
  - A case without `quiet` that ended quiet fails `answered`, and isn't retried as silent.
  - `no_menu` flags each of "would you like me to", "do you want me to", "which do you prefer", "should I", case-insensitively.
  - `one_question` flags two question marks.
  - Summaries count the new checks like the others.
- [ ] **Step 3: Implement.**
  - `Prompt.quiet: Literal["listening", "done"] | None = None`.
  - Multi-turn cases: if `Prompt` has no multi-turn form, add `before: tuple[str, ...] = ()`, earlier user lines asked first in the same conversation, with only the last reply judged. Use it for the correction case.
  - The cases from the spec go in two new groups, `quiet` and `thinking`. The correction case's first line must make her suggest something, for example "Give me one idea for making team meetings shorter." Its second line is the correction.
  - Group pass lines: `quiet` cases aren't judged, so the `quiet` group's mean is left out of the judge average and the group check. Keep `MIN_GROUP_MEAN` meaningful.
- [ ] **Step 4: Full check (evals tests pass except `test_baselines`), then commit:** "Evals: cases that expect her to stay quiet, and checks for menus and stacked questions".

### Task 6: Measure and save (controller)

- [ ] **Step 1: One Claude run, nothing saved.** Use `run_in_terminal` in a new tab:

  `cd ~/code/Hello-Trillion-Public && read -rs "TRILLION_ANTHROPIC_API_KEY?Anthropic key: " && export TRILLION_ANTHROPIC_API_KEY && echo && uv run python -m evals.gate --model claude-opus-5-5 --runs 1`

  The owner types the key there; never in chat.
- [ ] **Step 2: Read the replies against Done** (spec). If a line misses, rework that sentence (Task 4) and run once more. At most two rounds before reporting to the owner.
- [ ] **Step 3: The three-run Claude gate, saved.** Same command with `--save-baseline` (and `--accept-drop "<why>"` only if a drop is explained by the change), in a new terminal tab.
- [ ] **Step 4:** `uv run pytest tests/evals -q` passes.
- [ ] **Step 5:** Add a "First run" section to the spec with before and after numbers. Commit the baseline and the spec.
- [ ] **Step 6:** Push, open the PR (the body ends with the Claude Code line), bind it, and watch CI with a background `gh pr checks --watch`.
