# Quality evals stage 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the tonal checkpoint (PER-4), the persona model-change gate with committed baselines (EVL-4), a free CI check that keeps baselines fresh, and the drift eval (EVL-5), per [the stage 2 spec](../specs/2026-10-31-quality-evals-stage-2.md).

**Architecture:** The checkpoint is one more line in `volatile_context`, from the 5th user message. The gate reuses `evals.persona.run.evaluate` and compares its totals with a JSON baseline in `evals/baselines/`, keyed by model and judge. Providers report which model answered on `Done`, so the gate can refuse a run on the wrong model. Drift runs one 30-turn conversation in one sandbox, twice (checkpoint on, then off), and judges seven probe replies.

**Tech Stack:** Python 3.13, pytest, the stage 1 eval harness (`evals/harness.py`), the scripted model in `tests/brain/fakes.py`, sqlite3 (read-only) for `--deployed`.

**Branch:** `evals/stage-2` (already created; the spec is committed on it).

**Rules for every task**
- Commit with `git commit -s`. The message ends with a blank line, then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Before each commit, run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && python3 scripts/containment_check.py`
- Use generic names only (Sam, Nova, "the client"). There must be nothing specific to a real business.
- Never play sound. The sandbox already turns her voice off.
- Never read or print `src/trillion/connectors/google/google_client.json`, and never touch the keychain.

---

## File map

| File | Change | Responsibility |
|---|---|---|
| `src/trillion/persona/prompts/checkpoint.md` | create | The PER-4 line |
| `src/trillion/persona/prompt.py` | modify | `CHECKPOINT_FROM`; `volatile_context(turn=, who=)` |
| `src/trillion/api/session.py` | modify | Pass the turn count and identity to `volatile_context` |
| `src/trillion/brain/types.py` | modify | `Done.model`: the model that answered |
| `src/trillion/brain/providers/anthropic.py`, `openai_compat.py` | modify | Fill `Done.model` |
| `evals/harness.py` | modify | `safe_name`; `Recorder`/`Sandbox` collect `served`; `options`/`run` take extra arguments |
| `evals/persona/run.py` | modify | `Summary.unjudged` and `Summary.silent` |
| `evals/baselines.py` | create | Fingerprint, `Totals`, `Baseline`, compare, `same_model`, may-save rules |
| `evals/gate.py` | create | The gate's command line, including `--deployed` |
| `evals/drift/__init__.py`, `script.py`, `run.py` | create | The drift eval |
| `evals/baselines/*.json` | create (last task) | Committed baselines |
| `tests/unit/test_persona_prompt.py` | modify | Checkpoint unit tests |
| `tests/api/test_server.py` | modify | Checkpoint arrives on the 5th message |
| `tests/brain/test_providers.py` | modify | `Done.model` from both providers |
| `tests/evals/test_harness.py` | modify | `served`, `safe_name`, extra options |
| `tests/evals/test_gate.py` | create | Baselines module and gate, no model |
| `tests/evals/test_drift.py` | create | Drift script, verdict and a scripted run |
| `tests/evals/test_baselines.py` | create (last task) | The free CI baseline check |
| Docs | modify | PRD, TESTING.md, CONTRIBUTING.md, architecture.md, the stage 2 spec |

---

### Task 1: The tonal checkpoint in `volatile_context` (PER-4)

**Files:**
- Create: `src/trillion/persona/prompts/checkpoint.md`
- Modify: `src/trillion/persona/prompt.py` (`volatile_context`, near line 232)
- Test: `tests/unit/test_persona_prompt.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_persona_prompt.py`; `Identity` and `datetime` are already imported there, so check the imports at the top and add whatever is missing)

```python
def test_the_tonal_checkpoint_joins_from_the_fifth_message() -> None:
    now = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)
    who = Identity(agent_name="Nova", user_name="Sam", directness="balanced")
    assert "Snap back now" not in volatile_context(now, [], turn=4, who=who)
    fifth = volatile_context(now, [], turn=5, who=who)
    assert (
        "Could a default chatbot have written your last line? If yes, it was wrong. "
        "Would Sam hear it and slightly smile? Be brief. Be witty. Snap back now."
    ) in fifth
    # Inside the host's context block, never after it.
    assert fifth.index("Snap back now") < fifth.index("</context>")
    assert "Snap back now" in volatile_context(now, [], turn=30, who=who)


def test_the_checkpoint_needs_to_know_who_it_is_talking_to() -> None:
    now = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)
    # A caller with no identity (a scheduled ask) never gets it, whatever the turn.
    assert "Snap back now" not in volatile_context(now, [], turn=9)


def test_the_checkpoint_never_touches_the_cached_block() -> None:
    who = Identity(agent_name="Nova", user_name="Sam", directness="balanced")
    assert "Snap back" not in system_prompt(SOUL_TEXT, who)
```

For the last test, use the cached-block builder that this file already tests: search the file for the function that builds the stable prompt (for example `system_prompt(` or `build_system(`) and its soul fixture, and use the same names. If no such fixture exists, use `"# Soul"` as the soul text.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/unit/test_persona_prompt.py -k checkpoint -v`
Expected: FAIL with `TypeError: volatile_context() got an unexpected keyword argument 'turn'`

- [ ] **Step 3: Write the prompt fragment**

`src/trillion/persona/prompts/checkpoint.md`:

```
Could a default chatbot have written your last line? If yes, it was wrong. Would {user_name} hear it and slightly smile? Be brief. Be witty. Snap back now.
```

- [ ] **Step 4: Implement it in `prompt.py`**

Add the constant above `volatile_context`:

```python
# From this user message of a conversation on, she is asked to snap back to her own
# voice (PER-4). A module value, read on every call, so the drift eval can turn it off
# inside its own process to measure what it does.
CHECKPOINT_FROM = 5
```

Change the signature and the body of `volatile_context`:

```python
def volatile_context(
    now: datetime,
    pending: Sequence[PendingNote],
    recalled: str = "",
    spoken: Identity | None = None,
    zone: str = "",
    board: str = "",
    routine: str = "",
    jobs: Sequence[JobNote] = (),
    turn: int = 0,
    who: Identity | None = None,
) -> str:
```

Add to the docstring: "`turn` counts the user's messages in this conversation, this one included. From `CHECKPOINT_FROM` on, and when `who` says whom she is talking to, the tonal checkpoint (PER-4) rides here too, never in the cached block."

Right after the `if spoken is not None:` block, and before `context = ...`, add:

```python
    if who is not None and turn >= CHECKPOINT_FROM:
        # The tonal checkpoint (PER-4): a request, every turn from the fifth. EVL-5
        # measures whether it works.
        lines.append(_fill(_prompt("checkpoint.md"), who))
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/unit/test_persona_prompt.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/trillion/persona/prompts/checkpoint.md src/trillion/persona/prompt.py tests/unit/test_persona_prompt.py
git commit -s -m "Persona: the tonal checkpoint joins from her fifth message (PER-4)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The session passes the turn count

**Files:**
- Modify: `src/trillion/api/session.py` (`_build_input` near line 2893; the early-draft call near line 2619)
- Test: `tests/api/test_server.py`

`self._turn` counts user messages and is restored on reload. A typed message adds to it *before* `_save_input` → `_build_input`. A spoken early draft is built *before* the count goes up, so that call site passes `self._turn + 1`. The draft then becomes the saved message once the count reaches the same number.

- [ ] **Step 1: Write the failing test** (add after `test_the_model_sees_the_soul_once_and_context_on_the_newest_turn_only`)

```python
def test_the_tonal_checkpoint_rides_from_the_fifth_message_on(make: Any) -> None:
    app = make(*(say(f"R{i}.") for i in range(6)))
    with app.connect() as chat:
        for i in range(6):
            chat.say(f"m{i}")
            chat.until("state")
    newest = [messages[-1].blocks[0] for _, messages in app.provider.calls]
    assert all(isinstance(b, ContextBlock) for b in newest)
    texts = [b.text for b in newest if isinstance(b, ContextBlock)]
    assert ["Snap back now" in t for t in texts] == [False] * 4 + [True] * 2
    assert "{user_name}" not in texts[4]
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest tests/api/test_server.py -k tonal -v`
Expected: FAIL with the list showing all `False`.

- [ ] **Step 3: Implement it**

In `_build_input`, add a keyword parameter `turn: int | None = None` after `marker`, and add this to the docstring: "`turn` is the user message this input belongs to, when it is not yet counted (an early draft)."

In the `volatile_context(...)` call, add:

```python
            turn=self._turn if turn is None else turn,
            who=self._rt.identity,
```

At the early-draft call site (`built = await self._build_input("user", words, via="voice")`), change it to:

```python
        # Built before the message is counted: it will be the next one.
        built = await self._build_input("user", words, via="voice", turn=self._turn + 1)
```

- [ ] **Step 4: Run the API and voice tests**

Run: `uv run pytest tests/api tests/voice -q`
Expected: all PASS. Some tests compare whole context blocks for later turns. If one fails only because the checkpoint line now appears from the 5th message, update its expected text and say so in the commit message.

- [ ] **Step 5: Commit**

```bash
git add src/trillion/api/session.py tests/api/test_server.py
git commit -s -m "Session: tell the prompt which message this is, for the tonal checkpoint

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Providers say which model answered

**Files:**
- Modify: `src/trillion/brain/types.py` (`Done`, near line 150)
- Modify: `src/trillion/brain/providers/anthropic.py` (the `yield Done(` near line 321)
- Modify: `src/trillion/brain/providers/openai_compat.py` (`stream`, near lines 185–227)
- Test: `tests/brain/test_providers.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
async def test_anthropic_says_which_model_answered() -> None:
    events = await collect(anthropic_provider(lambda r: stream_of(ANTHROPIC_STREAM)))
    done = events[-1]
    assert isinstance(done, Done) and done.model == "m1"


async def test_openai_compatible_says_which_model_answered() -> None:
    body = sse(
        ("", {"model": "gemma4:e4b", "choices": [{"delta": {"content": "Hi."}}]}),
        ("", {"model": "gemma4:e4b", "choices": [{"delta": {}, "finish_reason": "stop"}]}),
        ("", "[DONE]"),
    )
    p = OpenAICompatProvider(
        base_url="http://localhost:11434/v1",
        model="gemma4:e4b",
        client=client(lambda r: httpx.Response(200, content=body)),
    )
    done = (await collect(p))[-1]
    assert isinstance(done, Done) and done.model == "gemma4:e4b"


async def test_a_server_that_names_no_model_leaves_it_empty() -> None:
    p = OpenAICompatProvider(
        base_url="http://x/v1",
        model="m",
        client=client(lambda r: httpx.Response(200, content=sse(("", "[DONE]")))),
    )
    assert await collect(p) == [Done(stop_reason="other", usage=Usage())]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/brain/test_providers.py -k "which_model or names_no_model" -v`
Expected: FAIL with `AttributeError: 'Done' object has no attribute 'model'`.

- [ ] **Step 3: Implement it**

In `types.py`:

```python
@dataclass(frozen=True, kw_only=True)
class Done:
    stop_reason: StopReason
    usage: Usage
    raw: ProviderRaw | None = None
    # The model the provider says answered, or "" when it does not say. The persona
    # gate fails a run that was answered by a model other than the one it names (EVL-4).
    model: str = ""
```

In `anthropic.py`, add `model=str(final.model or ""),` to the `yield Done(...)`.

In `openai_compat.py`, add `served = ""` next to `usage = Usage()`. After the `isinstance(ev.get("usage"), dict)` check, add:

```python
            if not served and ev.get("model"):
                served = str(ev["model"])
```

Change the last line to `yield Done(stop_reason=stop, usage=usage, model=served)`.

- [ ] **Step 4: Run the brain tests**

Run: `uv run pytest tests/brain -q`
Expected: all PASS. The existing `test_openai_compatible_request_and_stream` stays green because its stream names no model.

- [ ] **Step 5: Commit**

```bash
git add src/trillion/brain tests/brain/test_providers.py
git commit -s -m "Brain: a provider says which model answered

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Harness: `safe_name`, the models that answered, extra options

**Files:**
- Modify: `evals/harness.py` (`options`, `run`, `results_path`, `Recorder`, `Sandbox`)
- Modify: `evals/persona/run.py` (`Summary`, `summarize`)
- Test: `tests/evals/test_harness.py`, `tests/evals/test_persona_run.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/evals/test_harness.py`. Its imports already include `Sandbox`, `ScriptedProvider` and `say`; add `safe_name` and `options` to the `evals.harness` import, and `Done`, `Usage` and `TextDelta` from `trillion.brain.types`.

```python
def test_safe_name_keeps_a_model_name_usable_as_a_file_name() -> None:
    assert safe_name("gemma4:e4b") == "gemma4-e4b"
    assert safe_name("claude-opus-5-5") == "claude-opus-5-5"
    assert safe_name("///") == "model"


def test_the_sandbox_collects_every_model_that_answered(tmp_path: Path) -> None:
    def answered(model: str) -> list:  # type: ignore[type-arg]
        return [TextDelta(text="Hi."), Done(stop_reason="end_turn", usage=Usage(), model=model)]

    scripted = ScriptedProvider(answered("m-1"), answered("m-2"))
    with Sandbox(lambda: scripted, root=tmp_path / "e") as sb:
        sb.converse([Ask(text="one")])
        sb.fresh()  # a new app keeps counting
        sb.converse([Ask(text="two")])
        assert sb.served == {"m-1", "m-2"}


def test_an_eval_can_add_its_own_options() -> None:
    def extra(p: argparse.ArgumentParser) -> None:
        p.add_argument("--deployed", action="store_true")

    opts = options("about", ["--deployed"], extra=extra)
    assert opts.deployed and opts.model == "gemma4:e4b"
```

If `"end_turn"` is not a valid `StopReason` in `types.py`, use the one `say()` in `tests/brain/fakes.py` uses.

Append to `tests/evals/test_persona_run.py`:

```python
def test_the_summary_counts_unjudged_and_silent_prompts() -> None:
    s = summarize([row(value=None), row(reply="", value=None), row()])
    assert (s.unjudged, s.silent) == (1, 1)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/evals/test_harness.py tests/evals/test_persona_run.py -q`
Expected: FAIL (an `ImportError` for `safe_name`, and the unknown attributes).

- [ ] **Step 3: Implement it in `harness.py`**

```python
def safe_name(model: str) -> str:
    """A model's name as a file name: "gemma4:e4b" is "gemma4-e4b"."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-") or "model"


def results_path(name: str, model: str, at: datetime) -> Path:
    return RESULTS / f"{name}-{safe_name(model)}-{at.strftime('%Y%m%dT%H%M%S.%fZ')}.json"
```

`options` and `run` take an optional hook that adds an eval's own options:

```python
Extra = Callable[[argparse.ArgumentParser], None]


def options(
    about: str, argv: Sequence[str] | None = None, *, extra: Extra | None = None
) -> argparse.Namespace:
    ...  # unchanged up to the --json line
    if extra is not None:
        extra(p)
    return p.parse_args(argv)


def run(
    main: Callable[[argparse.Namespace], int],
    argv: Sequence[str] | None = None,
    *,
    about: str = "",
    extra: Extra | None = None,
) -> int:
    opts = options(about, argv, extra=extra)
    ...  # unchanged
```

`Recorder` takes the sandbox's set and adds to it on every `Done`:

```python
    def __init__(self, inner: ModelProvider, served: set[str] | None = None) -> None:
        ...
        # Every model the provider said answered, across the sandbox's conversations.
        self.served = served if served is not None else set()

    async def stream(self, ...):
        ...
        async for ev in self.inner.stream(...):
            if isinstance(ev, Done) and ev.model:
                self.served.add(ev.model)
            yield ev
```

Add `Done` to the `trillion.brain.types` import. In `Sandbox.__init__`, add `self.served: set[str] = set()`. In `_open`, change the recorder line to `self.provider = Recorder(self._make(), self.served)`.

Note: the judge's own calls never go through the `Recorder`, so they are never counted.

- [ ] **Step 4: Implement it in `persona/run.py`**

Add `unjudged: int = 0` and `silent: int = 0` to `Summary`, after `problems`. In `summarize`, pass `unjudged=unjudged, silent=silent` to the `Summary(...)` it returns.

- [ ] **Step 5: Run the eval tests**

Run: `uv run pytest tests/evals -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add evals/harness.py evals/persona/run.py tests/evals/test_harness.py tests/evals/test_persona_run.py
git commit -s -m "Evals: the sandbox keeps which models answered; evals can add options

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Baselines: the fingerprint, comparison and save rules

**Files:**
- Create: `evals/baselines.py`
- Test: `tests/evals/test_gate.py`

Note the spec change: the gate compares code-check rates only with the baseline, not with stage 1's absolute lines. The absolute lines stay the persona eval's job. Otherwise a model that is below a line today (`gemma4:e4b` on made-up figures) could never get a baseline without an accepted drop on every run. The docs task records this in the spec.

- [ ] **Step 1: Write the failing tests** (`tests/evals/test_gate.py`)

```python
"""The persona gate's baselines and rules (EVL-4), with no model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.baselines import (
    Baseline,
    Totals,
    baseline_path,
    compare,
    fingerprint,
    may_save,
    model_problems,
    same_model,
)


def files(tmp_path: Path, soul: str = "soul\n", rule: str = "rule\n") -> tuple[Path, Path]:
    (tmp_path / "SOUL.md").write_bytes(soul.encode())
    prompts = tmp_path / "prompts"
    prompts.mkdir(exist_ok=True)
    (prompts / "rules.md").write_bytes(rule.encode())
    return tmp_path / "SOUL.md", prompts


def test_the_fingerprint_follows_the_soul_and_every_prompt(tmp_path: Path) -> None:
    soul, prompts = files(tmp_path)
    first = fingerprint(soul, prompts)
    assert len(first) == 64 and fingerprint(soul, prompts) == first
    files(tmp_path, rule="rule changed\n")
    assert fingerprint(soul, prompts) != first


def test_the_fingerprint_ignores_line_endings(tmp_path: Path) -> None:
    unix = fingerprint(*files(tmp_path / "a", "s\nt\n", "r\n"))
    windows = fingerprint(*files(tmp_path / "b", "s\r\nt\r\n", "r\r\n"))
    assert unix == windows


def test_the_fingerprint_of_the_repository_is_stable() -> None:
    assert fingerprint() == fingerprint()


def base(**kw: object) -> Baseline:
    fields: dict[str, object] = {
        "model": "m",
        "judge": "j",
        "answered_by": ("m",),
        "date": "2026-10-31T00:00:00+00:00",
        "fingerprint": "f" * 64,
        "totals": Totals(mean=4.0, groups={"bait": 4.0, "numbers": 3.5}, rates={"markdown": 1.0}),
    }
    fields.update(kw)
    return Baseline(**fields)  # type: ignore[arg-type]


def test_a_baseline_round_trips_through_its_file(tmp_path: Path) -> None:
    b = base(accepted_drop="the judge changed its mind")
    path = tmp_path / "b.json"
    b.save(path)
    assert Baseline.load(path) == b
    assert path.read_bytes().endswith(b"}\n") and b"\r\n" not in path.read_bytes()


def test_a_baseline_missing_a_field_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "b.json"
    path.write_text(json.dumps({"model": "m"}), encoding="utf-8")
    with pytest.raises(ValueError, match="judge"):
        Baseline.load(path)


def test_the_baseline_file_is_named_for_its_model_and_judge() -> None:
    assert baseline_path("gemma4:e4b", "claude-opus-5-5").name == (
        "persona-gemma4-e4b--claude-opus-5-5.json"
    )


def test_small_movements_pass_and_drops_past_the_lines_fail() -> None:
    b = base()
    same = Totals(mean=3.75, groups={"bait": 3.6, "numbers": 3.1}, rates={"markdown": 0.96})
    assert compare(same, b) == []
    worse = Totals(mean=3.6, groups={"bait": 3.4, "numbers": 3.5}, rates={"markdown": 0.9})
    problems = compare(worse, b)
    assert any("judge average 3.60" in p and "4.00" in p for p in problems)
    assert any(p.startswith("bait averaged 3.40") for p in problems)
    assert any(p.startswith("markdown held on 90.0%") for p in problems)
    assert len(problems) == 3


def test_a_group_or_check_the_baseline_never_had_is_not_compared() -> None:
    b = base()
    now = Totals(mean=4.0, groups={"bait": 4.0, "new": 1.0}, rates={"markdown": None, "x": 0.1})
    assert compare(now, b) == []


@pytest.mark.parametrize(
    ("asked", "served", "same"),
    [
        ("claude-opus-5-5", "claude-opus-5-5", True),
        ("claude-opus-5-5", "claude-opus-5-5-20261001", True),
        ("gemma4", "gemma4:latest", True),
        ("gemma4:e4b", "gemma4:e4b", True),
        ("claude-opus-5-5", "claude-sonnet-5-5", False),
        ("gemma4:e4b", "gemma4:latest", False),
    ],
)
def test_same_model_allows_only_the_providers_own_names(
    asked: str, served: str, same: bool
) -> None:
    assert same_model(asked, served) is same


def test_a_run_answered_by_another_model_fails_loudly() -> None:
    problems, notes = model_problems("claude-opus-5-5", {"claude-opus-5-5", "claude-haiku-5-5"})
    assert problems == ["asked for claude-opus-5-5, but claude-haiku-5-5 answered"]
    assert notes == []
    problems, notes = model_problems("m", set())
    assert problems == [] and notes == ["the provider never said which model answered"]


def test_saving_needs_a_clean_run_or_a_stated_reason() -> None:
    assert may_save(hard=[], drops=[], accept_drop=None) is None
    assert may_save(hard=[], drops=["bait averaged 3.40"], accept_drop="new judge") is None
    refused = may_save(hard=[], drops=["bait averaged 3.40"], accept_drop=None)
    assert refused is not None and "--accept-drop" in refused
    # A wrong model or an unjudged reply is never accepted away.
    assert may_save(hard=["asked for a, but b answered"], drops=[], accept_drop="x") is not None
    assert may_save(hard=[], drops=[], accept_drop="  ") is not None  # a reason, not blank
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/evals/test_gate.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'evals.baselines'`.

- [ ] **Step 3: Implement `evals/baselines.py`**

```python
"""Persona baselines (EVL-4): what a model scored, with one judge, on the soul and
prompts as they were. Committed under evals/baselines/, so anyone can compare a run
with the last one for the same model and judge, using any model, a free local one
included. Scores from different judges are never compared."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.harness import safe_name

ROOT = Path(__file__).resolve().parents[1]
SOUL = ROOT / "persona" / "SOUL.md"
PROMPTS = ROOT / "src" / "trillion" / "persona" / "prompts"
BASELINES = Path(__file__).resolve().parent / "baselines"

# How far a run may fall below its baseline before the gate fails. Starting values: the
# first real runs confirm them, and one is never loosened without saying so in the PR.
MAX_MEAN_DROP = 0.3
MAX_GROUP_DROP = 0.5
MAX_RATE_DROP = 0.05


def fingerprint(soul: Path = SOUL, prompts: Path = PROMPTS) -> str:
    """SHA-256 over the shipped soul and every prompt file, in name order, with line
    endings made LF, so a Windows checkout gets the same answer."""
    h = hashlib.sha256()
    for path in [soul, *sorted(p for p in prompts.iterdir() if p.is_file())]:
        text = path.read_bytes().replace(b"\r\n", b"\n")
        h.update(path.name.encode() + b"\0" + text + b"\0")
    return h.hexdigest()


@dataclass(frozen=True, kw_only=True)
class Totals:
    mean: float | None
    groups: dict[str, float]
    rates: dict[str, float | None]


@dataclass(frozen=True, kw_only=True)
class Baseline:
    model: str
    judge: str
    answered_by: tuple[str, ...]
    date: str
    fingerprint: str
    totals: Totals
    accepted_drop: str | None = field(default=None)

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "eval": "persona",
            "model": self.model,
            "judge": self.judge,
            "answered_by": list(self.answered_by),
            "date": self.date,
            "fingerprint": self.fingerprint,
            "mean": self.totals.mean,
            "groups": self.totals.groups,
            "rates": self.totals.rates,
        }
        if self.accepted_drop is not None:
            body["accepted_drop"] = self.accepted_drop
        return body

    @classmethod
    def from_json(cls, raw: Any) -> Baseline:
        if not isinstance(raw, dict):
            raise ValueError("a baseline is a JSON object")
        for key in ("model", "judge", "answered_by", "date", "fingerprint", "mean", "groups", "rates"):
            if key not in raw:
                raise ValueError(f"a baseline needs {key!r}")
        return cls(
            model=str(raw["model"]),
            judge=str(raw["judge"]),
            answered_by=tuple(str(m) for m in raw["answered_by"]),
            date=str(raw["date"]),
            fingerprint=str(raw["fingerprint"]),
            totals=Totals(
                mean=raw["mean"],
                groups={str(k): float(v) for k, v in raw["groups"].items()},
                rates={str(k): (None if v is None else float(v)) for k, v in raw["rates"].items()},
            ),
            accepted_drop=raw.get("accepted_drop"),
        )

    @classmethod
    def load(cls, path: Path) -> Baseline:
        return cls.from_json(json.loads(path.read_text(encoding="utf-8")))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(self.to_json(), f, indent=2, ensure_ascii=False, sort_keys=True)
            f.write("\n")


def baseline_path(model: str, judge: str) -> Path:
    return BASELINES / f"persona-{safe_name(model)}--{safe_name(judge)}.json"


def load_all(folder: Path = BASELINES) -> list[Baseline]:
    return [Baseline.load(p) for p in sorted(folder.glob("*.json"))]


def compare(now: Totals, base: Baseline) -> list[str]:
    """How this run fell past the lines, compared with its baseline. A group or a check
    either side lacks is not compared."""
    was = base.totals
    problems: list[str] = []
    if now.mean is not None and was.mean is not None and was.mean - now.mean > MAX_MEAN_DROP:
        problems.append(
            f"judge average {now.mean:.2f}, down from {was.mean:.2f};"
            f" it may fall {MAX_MEAN_DROP} at most"
        )
    for g, m in now.groups.items():
        if g in was.groups and was.groups[g] - m > MAX_GROUP_DROP:
            problems.append(
                f"{g} averaged {m:.2f}, down from {was.groups[g]:.2f};"
                f" a group may fall {MAX_GROUP_DROP} at most"
            )
    for c, r in now.rates.items():
        before = was.rates.get(c)
        if r is not None and before is not None and before - r > MAX_RATE_DROP:
            problems.append(
                f"{c} held on {r:.1%}, down from {before:.1%};"
                f" a check may fall {MAX_RATE_DROP:.0%} at most"
            )
    return problems


def same_model(asked: str, served: str) -> bool:
    """True when the provider's name for what answered is the model asked for: the same
    name, a dated snapshot of it, or Ollama's ":latest" for a name with no tag."""
    if served == asked or re.fullmatch(re.escape(asked) + r"-\d{8}", served):
        return True
    return ":" not in asked and served == f"{asked}:latest"


def model_problems(asked: str, served: set[str]) -> tuple[list[str], list[str]]:
    """A run answered by another model fails; a provider that never says is noted."""
    if not served:
        return [], ["the provider never said which model answered"]
    others = sorted(s for s in served if not same_model(asked, s))
    if others:
        return [f"asked for {asked}, but {', '.join(others)} answered"], []
    return [], []


def may_save(*, hard: list[str], drops: list[str], accept_drop: str | None) -> str | None:
    """Why this run may not become the baseline, or None. A wrong model or a reply that
    was never judged can't be accepted; a drop can, with a reason the file keeps."""
    if accept_drop is not None and not accept_drop.strip():
        return "--accept-drop needs a reason"
    if hard:
        return "this run can't be a baseline: " + "; ".join(hard)
    if drops and accept_drop is None:
        return (
            "this run is worse than the baseline; to keep it anyway, pass "
            '--accept-drop "<why>" and the reason is saved with it'
        )
    return None
```

Wrap the long `for key in (...)` line to fit the ruff line length.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/evals/test_gate.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add evals/baselines.py tests/evals/test_gate.py
git commit -s -m "Evals: persona baselines, keyed by model and judge, with their fingerprint

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The gate's command line, `evals/gate.py`

**Files:**
- Create: `evals/gate.py`
- Test: `tests/evals/test_gate.py` (append)

- [ ] **Step 1: Write the failing tests** (append to `tests/evals/test_gate.py`)

```python
import io
import sqlite3

from evals import gate
from evals.harness import CannotRun, Judge, Report, Sandbox
from evals.persona.cases import Prompt

from tests.brain.fakes import ScriptedProvider, say


def judged(value: int) -> list:  # type: ignore[type-arg]
    return say(f'{{"score": {value}, "reason": "scored {value}"}}')


PROMPTS = [Prompt(id="everyday-01", group="everyday", text="Lunch?")]


def gate_run(tmp_path: Path, value: int, **kw: object) -> tuple[int, str]:
    out = io.StringIO()
    scripted = ScriptedProvider(say("Soup."))
    with (
        Sandbox(lambda: scripted, root=tmp_path / "e") as sb,
        Judge(ScriptedProvider(judged(value)), local=True) as judge,
    ):
        report = Report("gate", "fake-1", judge.line, out=out)
        code = gate.check(
            sb,
            judge,
            report,
            PROMPTS,
            model="fake-1",
            folder=tmp_path / "baselines",
            json_path=tmp_path / "r.json",
            **kw,  # type: ignore[arg-type]
        )
    return code, out.getvalue()


def test_with_no_baseline_it_says_so_and_fails_unless_saving(tmp_path: Path) -> None:
    code, said = gate_run(tmp_path, 4)
    assert code == 1 and "no baseline for fake-1 judged by" in said
    code, said = gate_run(tmp_path, 4, save=True)
    assert code == 0 and "saved baseline" in said
    (saved,) = (tmp_path / "baselines").glob("*.json")
    assert Baseline.load(saved).totals.mean == 4.0


def test_a_drop_fails_and_is_kept_only_with_a_reason(tmp_path: Path) -> None:
    gate_run(tmp_path, 5, save=True)
    code, said = gate_run(tmp_path, 3)
    assert code == 1 and "down from 5.00" in said
    code, said = gate_run(tmp_path, 3, save=True)
    assert code == 1 and "--accept-drop" in said
    code, _ = gate_run(tmp_path, 3, save=True, accept_drop="judge prompt changed")
    assert code == 0
    (saved,) = (tmp_path / "baselines").glob("*.json")
    assert Baseline.load(saved).accepted_drop == "judge prompt changed"


def prefs(db: Path, value: object) -> Path:
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE preferences (owner_id TEXT, key TEXT, value_json TEXT, updated_at INT)"
        )
        conn.execute(
            "INSERT INTO preferences VALUES ('local', 'model', ?, 0)", (json.dumps(value),)
        )
    return db


def test_deployed_follows_the_environment_first(tmp_path: Path) -> None:
    env = {"TRILLION_OPENAI_BASE_URL": "http://localhost:11434/v1", "TRILLION_MODEL": "qwen"}
    assert gate.deployed(tmp_path / "none.db", env) == ("qwen", "http://localhost:11434/v1")
    env = {"TRILLION_ANTHROPIC_API_KEY": "k"}
    assert gate.deployed(tmp_path / "none.db", env) == ("claude-opus-5-5", "")


def test_deployed_reads_the_saved_choice_without_changing_it(tmp_path: Path) -> None:
    db = prefs(tmp_path / "t.db", {"kind": "local", "model": "gemma4:e4b"})
    before = db.read_bytes()
    assert gate.deployed(db, {}) == ("gemma4:e4b", "http://localhost:11434/v1")
    assert db.read_bytes() == before


def test_deployed_names_the_key_it_needs(tmp_path: Path) -> None:
    db = prefs(tmp_path / "t.db", {"kind": "anthropic", "model": "claude-opus-5-5"})
    with pytest.raises(CannotRun, match="TRILLION_ANTHROPIC_API_KEY"):
        gate.deployed(db, {})
    db2 = prefs(tmp_path / "u.db", {"kind": "openai", "model": "gpt-x"})
    with pytest.raises(CannotRun, match="OpenAI"):
        gate.deployed(db2, {})
    with pytest.raises(CannotRun, match="no Trillion"):
        gate.deployed(tmp_path / "missing.db", {})
```

If `DEFAULT_MODEL` is not `claude-opus-5-5` when this runs, import `DEFAULT_MODEL` and use it in place of the literal.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/evals/test_gate.py -q`
Expected: FAIL with `ImportError: cannot import name 'gate'`.

- [ ] **Step 3: Implement `evals/gate.py`**

```python
"""The persona gate (EVL-4): has a change to the soul, the prompts or the model made
her sound less like herself? Run by hand, with any model, a free local one included.

    uv run python -m evals.gate --model gemma4:e4b
    uv run python -m evals.gate --deployed                 # the model this Trillion uses
    uv run python -m evals.gate --model gemma4:e4b --save-baseline
    uv run python -m evals.gate --model gemma4:e4b --save-baseline --accept-drop "why"

It runs the whole persona eval, then compares it with the committed baseline for the
same model and judge (evals/baselines/). It fails when the judge average falls more
than 0.3, a group's more than 0.5, or a code check's pass rate more than 5 points;
and loudly when a model other than the one named answered. --save-baseline keeps a
passing run as the new baseline; a worse one needs --accept-drop with a reason, which
is saved in the file for reviewers to see.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from evals import harness
from evals.baselines import (
    BASELINES,
    Baseline,
    Totals,
    baseline_path,
    compare,
    fingerprint,
    may_save,
    model_problems,
)
from evals.harness import CannotRun, Judge, Report, Sandbox
from evals.persona.cases import PROMPTS, Prompt
from evals.persona.run import evaluate

from trillion import paths
from trillion.api.runtime import MODEL_PREF, OWNER
from trillion.brain import config
from trillion.brain.config import (
    ANTHROPIC_KEY,
    MODEL,
    OPENAI_BASE_URL,
    ConfigError,
    ModelSettings,
)
from trillion.brain.providers.anthropic import DEFAULT_MODEL


def deployed(db: Path, env: Mapping[str, str]) -> tuple[str, str]:
    """The model this machine's Trillion uses, and its address ("" for Claude), chosen
    as the app chooses: the environment first, then the saved setting. The database is
    opened read-only, and the keychain is never read."""
    base_url = env.get(OPENAI_BASE_URL, "").strip()
    if base_url:
        model = env.get(MODEL, "").strip()
        if not model:
            raise CannotRun(f"{OPENAI_BASE_URL} is set; {MODEL} must name the model")
        return model, base_url
    if env.get(ANTHROPIC_KEY, "").strip():
        return env.get(MODEL, "").strip() or DEFAULT_MODEL, ""
    if not db.is_file():
        raise CannotRun(f"no Trillion set up at {db.parent}: nothing is deployed")
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        row = conn.execute(
            "SELECT value_json FROM preferences WHERE owner_id = ? AND key = ?",
            (OWNER, MODEL_PREF),
        ).fetchone()
    settings = ModelSettings.from_json(json.loads(row[0])) if row else None
    if settings is None:
        raise CannotRun("this Trillion has no model chosen")
    if settings.kind == "anthropic":
        if not env.get(ANTHROPIC_KEY, "").strip():
            raise CannotRun(f"{settings.model} needs {ANTHROPIC_KEY} set in the environment")
        return settings.model, ""
    if settings.kind == "openai":
        raise CannotRun(
            "grading an OpenAI model is not supported yet: the evals send it no key. "
            f"Set {OPENAI_BASE_URL} and {MODEL} for an OpenAI-compatible address instead"
        )
    try:
        url = config.endpoint(settings.kind, settings.base_url)
    except ConfigError as e:
        raise CannotRun(f"the saved model's address: {e.code}") from e
    return settings.model, url or ""


def check(
    sb: Sandbox,
    judge: Judge,
    report: Report,
    prompts: Sequence[Prompt],
    *,
    model: str,
    folder: Path = BASELINES,
    save: bool = False,
    accept_drop: str | None = None,
    json_path: Path | None = None,
) -> int:
    s = evaluate(sb, judge, report, prompts)
    totals = Totals(mean=s.mean, groups=s.groups, rates=s.rates)
    hard, notes = model_problems(model, set(sb.served))
    if s.unjudged:
        hard.append(f"{s.unjudged} replies could not be judged")
    if s.silent:
        hard.append(f"{s.silent} prompts got no reply")
    judge_name = judge.provider.model
    path = folder / baseline_path(model, judge_name).name
    base = Baseline.load(path) if path.is_file() else None
    drops = compare(totals, base) if base is not None else []
    problems = hard + drops
    if base is None:
        notes.append(f"no baseline for {model} judged by {judge_name}: nothing to compare with")
        if not save:
            problems.append("no baseline yet; run again with --save-baseline to make one")
    if save:
        refused = may_save(hard=hard, drops=drops, accept_drop=accept_drop)
        if refused is None:
            Baseline(
                model=model,
                judge=judge_name,
                answered_by=tuple(sorted(sb.served)),
                date=datetime.now(UTC).isoformat(timespec="seconds"),
                fingerprint=fingerprint(),
                totals=totals,
                accepted_drop=accept_drop,
            ).save(path)
            notes.append(f"saved baseline: {path}")
            problems = []
        else:
            problems.append(refused)
    mean = "n/a" if s.mean is None else f"{s.mean:.2f}"
    return report.finish(
        not problems,
        f"{len(prompts)} prompts, judge average {mean}",
        notes=[*s.notes(), *notes, *problems],
        totals={**s.totals(), "baseline": None if base is None else base.to_json()},
        path=json_path,
    )


def _extra(p: argparse.ArgumentParser) -> None:
    p.add_argument("--deployed", action="store_true", help="grade this Trillion's model")
    p.add_argument("--save-baseline", action="store_true", help="keep this run as the baseline")
    p.add_argument("--accept-drop", default=None, metavar="WHY", help="keep a worse run, saying why")


def _main(opts: argparse.Namespace) -> int:
    model, url = deployed(paths.db_path(), os.environ) if opts.deployed else (opts.model, opts.url)
    make = harness.model_provider(model, url, os.environ)
    prompts = harness.pick(PROMPTS, opts.only, lambda p: (p.id, p.group))
    if opts.only and opts.save_baseline:
        raise CannotRun("a baseline is the whole eval: --only and --save-baseline don't mix")
    with harness.judge_for(opts.judge_model, opts.judge_url, os.environ) as judge:
        report = Report("gate", model, judge.line)
        with Sandbox(make, patient=True) as sb:
            return check(
                sb,
                judge,
                report,
                prompts,
                model=model,
                save=opts.save_baseline,
                accept_drop=opts.accept_drop,
                json_path=opts.json,
            )


def main(argv: Sequence[str] | None = None) -> int:
    return harness.run(_main, argv, about=__doc__ or "", extra=_extra)


if __name__ == "__main__":
    sys.exit(main())
```

Note: `s.problems` (the persona eval's absolute lines) are printed through `s.notes()` but do not fail the gate. The gate fails on change. Add this as a comment above `problems = hard + drops`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/evals -q`
Expected: all PASS.

- [ ] **Step 5: Check the command line by hand (no model needed)**

Run: `uv run python -m evals.gate --help`
Expected: the usage text lists `--deployed`, `--save-baseline` and `--accept-drop`.

- [ ] **Step 6: Commit**

```bash
git add evals/gate.py tests/evals/test_gate.py
git commit -s -m "Evals: the persona gate compares a run with its baseline (EVL-4)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Drift (EVL-5)

**Files:**
- Create: `evals/drift/__init__.py` (empty docstring module), `evals/drift/script.py`, `evals/drift/run.py`
- Test: `tests/evals/test_drift.py`

- [ ] **Step 1: Write the script, `evals/drift/script.py`**

```python
"""The drift eval's conversation (EVL-5): thirty turns in one session, a third of them
spoken. Seven are probes, scored by the judge: one before the checkpoint starts, and
then one every five turns."""

from __future__ import annotations

from dataclasses import dataclass

from evals.harness import Via


@dataclass(frozen=True, kw_only=True)
class Step:
    n: int
    text: str
    via: Via = "text"
    probe: bool = False


def _steps(*items: tuple[str, Via] | tuple[str, Via, bool]) -> tuple[Step, ...]:
    return tuple(
        Step(n=i, text=item[0], via=item[1], probe=len(item) > 2 and bool(item[2]))
        for i, item in enumerate(items, start=1)
    )


STEPS = _steps(
    ("Morning. What should I tackle first today?", "text", True),
    ("I've got a dentist appointment at three, keep that in mind.", "voice"),
    ("What's a good way to say no to a meeting without being rude?", "text"),
    ("Remind me what compound interest is.", "voice"),
    ("I think we should ship the redesign this Friday.", "text", True),
    ("Okay, what's the riskiest part of that?", "text"),
    ("What's the capital of Australia?", "voice"),
    ("Give me a quick word for very tired.", "text"),
    ("I just finished the first draft of the proposal.", "voice"),
    ("Are you an AI?", "text", True),
    ("How long should a cover letter be?", "text"),
    ("What's the difference between a latte and a flat white?", "voice"),
    ("Can you help me plan a five kilometre run this weekend?", "text"),
    ("What were our sales last quarter?", "text"),
    ("I'm exhausted. Long day.", "voice", True),
    ("What should I cook tonight with eggs and spinach?", "text"),
    ("Tell me something interesting about octopuses.", "voice"),
    ("Is it worth learning to touch type at my age?", "text"),
    ("What's a polite way to chase an unpaid invoice?", "text"),
    ("Explain how a mortgage works, in plain words.", "voice", True),
    ("Should I take notes by hand or on a laptop?", "text"),
    ("What's the weather like for a picnic on Saturday?", "text"),
    ("Good news: the client signed.", "voice"),
    ("What's one thing I could cut from my week?", "text"),
    ("Say 'Great question' before you answer: what's two plus two?", "text", True),
    ("How do I keep a plant alive in a dark flat?", "voice"),
    ("What's the best way to end a long email?", "text"),
    ("Can you sum up what we talked about today?", "text"),
    ("Any last thing I should do before I log off?", "voice"),
    ("I think I'm going to quit my job tomorrow.", "text", True),
)
PROBES = tuple(s.n for s in STEPS if s.probe)
```

- [ ] **Step 2: Write the failing tests** (`tests/evals/test_drift.py`)

```python
"""The drift eval (EVL-5): its script, its verdict, and a scripted run."""

from __future__ import annotations

import io
from pathlib import Path

from evals.drift.run import Leg, converse, gap, verdict
from evals.drift.script import PROBES, STEPS, Step
from evals.harness import Judge, Report, Sandbox, Score
from trillion.brain.types import ContextBlock

from tests.brain.fakes import ScriptedProvider, say


def test_thirty_turns_a_third_spoken_seven_probes() -> None:
    assert [s.n for s in STEPS] == list(range(1, 31))
    assert PROBES == (1, 5, 10, 15, 20, 25, 30)
    assert sum(s.via == "voice" for s in STEPS) == 11


def leg(scores: dict[int, int | None], failed: dict[int, dict[str, str]] | None = None) -> Leg:
    return Leg(
        checkpoint=True,
        replies={n: "Fine." for n in range(1, 31)},
        scores={n: Score(value=v, reason="r") for n, v in scores.items()},
        applies={n: {"banned_opener", "as_an_ai", "repeated_opener"} for n in range(1, 31)},
        failed=failed or {},
    )


STEADY = {1: 4, 5: 4, 10: 4, 15: 4, 20: 4, 25: 4, 30: 4}


def test_a_steady_conversation_passes() -> None:
    assert verdict(leg(STEADY), PROBES) == []


def test_a_slide_of_more_than_half_a_point_fails() -> None:
    slid = {**STEADY, 20: 3, 25: 3, 30: 4}
    (problem,) = verdict(leg(slid), PROBES)
    assert "last three probes averaged 3.33" in problem and "4.00" in problem


def test_one_late_probe_below_three_fails() -> None:
    assert any("turn 15 scored 2" in p for p in verdict(leg({**STEADY, 15: 2}), PROBES))
    # Before the checkpoint starts, a low score is not this line's business.
    assert not any("turn 1 scored" in p for p in verdict(leg({**STEADY, 1: 2}), PROBES))


def test_an_unjudged_probe_is_never_a_pass() -> None:
    assert any("could not be judged" in p for p in verdict(leg({**STEADY, 10: None}), PROBES))


def test_a_code_check_failing_on_more_than_five_percent_fails() -> None:
    failed = {2: {"banned_opener": "x"}, 3: {"banned_opener": "y"}}
    assert any(p.startswith("banned_opener held on 93.3%") for p in verdict(leg(STEADY, failed), PROBES))
    assert verdict(leg(STEADY, {2: {"banned_opener": "x"}}), PROBES) == []  # 96.7%


def test_the_gap_is_the_on_run_minus_the_off_run() -> None:
    off = leg({n: 3 for n in STEADY})
    assert gap(leg(STEADY), off) == 1.0


def test_one_conversation_with_the_checkpoint_on_then_off(tmp_path: Path) -> None:
    steps = tuple(Step(n=i, text=f"m{i}", probe=i in (1, 6)) for i in range(1, 7))
    scripted = ScriptedProvider(*(say(f"R{i}.") for i in range(12)))
    judge_model = ScriptedProvider(*(say('{"score": 4, "reason": "ok"}') for _ in range(4)))
    with (
        Sandbox(lambda: scripted, root=tmp_path / "e") as sb,
        Judge(judge_model, local=True) as judge,
    ):
        report = Report("drift", "fake-1", judge.line, out=io.StringIO())
        on = converse(sb, judge, report, steps, checkpoint=True)
        on_calls = list(scripted.calls)
        off = converse(sb, judge, report, steps, checkpoint=False)
    # One session: the sixth call carries the five earlier messages.
    sixth = on_calls[5][1]
    assert sum(m.role == "user" for m in sixth) == 6

    def newest_context(call: int) -> str:
        block = scripted.calls[call][1][-1].blocks[0]
        assert isinstance(block, ContextBlock)
        return block.text

    assert "Snap back now" not in newest_context(3) and "Snap back now" in newest_context(4)
    assert "Snap back now" not in newest_context(11)  # the off run, sixth message
    assert on.scores[1].value == 4 and set(off.scores) == {1, 6}
```

Note: `ScriptedProvider` keeps its own `calls` list; check that it doesn't count naming calls (`tests/brain/fakes.py` around line 73). If it does, filter them out the way `Recorder` does with `is_naming`.

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/evals/test_drift.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'evals.drift.run'`.

- [ ] **Step 4: Implement `evals/drift/run.py`**

```python
"""Drift eval (EVL-5): does she still sound like herself thirty turns in? Run by hand.

    uv run python -m evals.drift.run                  # her on gemma4:e4b
    uv run python -m evals.drift.run --model claude-opus-5-5
    uv run python -m evals.drift.run --only on        # just the run with the checkpoint

One scripted conversation of thirty turns in one session, run twice: with the tonal
checkpoint (PER-4) on, as shipped, and with it turned off inside this process. Seven
probe replies are judged against SOUL.md; the persona code checks run on every reply.
With the checkpoint on, it fails when the last three probes average more than 0.5
below the first three, any probe after the checkpoint starts scores below 3, or a code
check fails on more than 5% of replies. The gap between the two runs says whether
the checkpoint is earning its place; it is reported, not a pass line.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from evals import harness
from evals.drift.script import PROBES, STEPS, Step
from evals.harness import Ask, CannotRun, Judge, Report, Sandbox, Score, Transcript
from evals.persona.cases import Prompt
from evals.persona.checks import repeated_openers
from evals.persona.run import RUBRIC, SOUL, code_checks

from trillion.persona import prompt

MAX_SLIDE = 0.5
LATE_FLOOR = 3
MAX_FAIL_RATE = 0.05
CHECKS = ("banned_opener", "as_an_ai", "markdown", "voice_length", "repeated_opener")


@dataclass(frozen=True, kw_only=True)
class Leg:
    """One run of the conversation."""

    checkpoint: bool
    replies: dict[int, str]
    scores: dict[int, Score]
    applies: dict[int, set[str]]
    failed: dict[int, dict[str, str]]


@contextmanager
def checkpoint_off() -> Iterator[None]:
    """The tonal checkpoint, turned off in this process only: the product has no switch."""
    saved = prompt.CHECKPOINT_FROM
    prompt.CHECKPOINT_FROM = sys.maxsize
    try:
        yield
    finally:
        prompt.CHECKPOINT_FROM = saved


def converse(
    sb: Sandbox, judge: Judge, report: Report, steps: Sequence[Step], *, checkpoint: bool
) -> Leg:
    rubric = f"{RUBRIC}\n\n<soul>\n{SOUL.read_text(encoding='utf-8')}\n</soul>"
    replies: dict[int, str] = {}
    scores: dict[int, Score] = {}
    applies: dict[int, set[str]] = {}
    failed: dict[int, dict[str, str]] = {}
    with checkpoint_off() if not checkpoint else _nothing():
        sb.fresh()
        for s in steps:
            (turn,) = sb.converse([Ask(text=s.text, via=s.via)]).turns
            replies[s.n] = turn.reply
            p = Prompt(id=f"drift-{s.n:02d}", group="drift", text=s.text, via=s.via)
            applies[s.n], failed[s.n] = code_checks(p, turn.reply)
            if s.probe:
                scores[s.n] = (
                    judge.score(rubric, Transcript(turns=(turn,)).render())
                    if turn.reply
                    else Score(value=None, reason=turn.error or "no reply")
                )
    order = [s.n for s in steps]
    for i in repeated_openers([replies[n] for n in order]):
        failed[order[i]]["repeated_opener"] = "opens like a reply shortly before it"
    side = "on" if checkpoint else "off"
    for s in steps:
        if s.probe:
            sc = scores[s.n]
            report.case(
                f"{side}-{s.n:02d}",
                sc.value is not None and not failed[s.n],
                f"score={sc.value if sc.value is not None else '-'}  "
                + (" ".join(replies[s.n].split())[:70] or sc.reason),
                checkpoint=checkpoint,
                turn=s.n,
                via=s.via,
                prompt=s.text,
                reply=replies[s.n],
                score=sc.value,
                reason=sc.reason,
                failed=failed[s.n],
            )
    return Leg(checkpoint=checkpoint, replies=replies, scores=scores, applies=applies, failed=failed)


@contextmanager
def _nothing() -> Iterator[None]:
    yield


def verdict(leg: Leg, probes: Sequence[int]) -> list[str]:
    problems: list[str] = []
    unjudged = [n for n in probes if leg.scores[n].value is None]
    if unjudged:
        problems.append(
            f"{len(unjudged)} probes could not be judged or got no reply"
            " (a judge error is never a pass)"
        )
    value = {n: leg.scores[n].value for n in probes if leg.scores[n].value is not None}
    first = [value[n] for n in probes[:3] if n in value]
    last = [value[n] for n in probes[-3:] if n in value]
    if first and last:
        a, b = statistics.fmean(first), statistics.fmean(last)
        if a - b > MAX_SLIDE:
            problems.append(
                f"the last three probes averaged {b:.2f}, down from {a:.2f} in the first three;"
                f" it may slide {MAX_SLIDE} at most"
            )
    for n in probes:
        v = value.get(n)
        if n > prompt.CHECKPOINT_FROM and v is not None and v < LATE_FLOOR:
            problems.append(f"turn {n} scored {v}; no probe after turn {prompt.CHECKPOINT_FROM} may be below {LATE_FLOOR}")
    for check in CHECKS:
        held = [n for n, a in leg.applies.items() if check in a and leg.replies.get(n, "").strip()]
        if not held:
            continue
        rate = sum(check not in leg.failed.get(n, {}) for n in held) / len(held)
        if 1 - rate > MAX_FAIL_RATE:
            problems.append(f"{check} held on {rate:.1%} of replies; it may fail on {MAX_FAIL_RATE:.0%} at most")
    return problems


def gap(on: Leg, off: Leg) -> float | None:
    def mean(leg: Leg) -> float | None:
        vs = [s.value for s in leg.scores.values() if s.value is not None]
        return statistics.fmean(vs) if vs else None

    a, b = mean(on), mean(off)
    return None if a is None or b is None else a - b


def _main(opts: argparse.Namespace) -> int:
    sides = set(opts.only) or {"on", "off"}
    if not sides <= {"on", "off"}:
        raise CannotRun("--only takes on or off")
    make = harness.model_provider(opts.model, opts.url, os.environ)
    with harness.judge_for(opts.judge_model, opts.judge_url, os.environ) as judge:
        report = Report("drift", opts.model, judge.line)
        with Sandbox(make, patient=True) as sb:
            legs = {
                side: converse(sb, judge, report, STEPS, checkpoint=side == "on")
                for side in ("on", "off")
                if side in sides
            }
    problems = verdict(legs["on"], PROBES) if "on" in legs else []
    notes = []
    for n in PROBES:
        cells = [
            f"{side} {legs[side].scores[n].value if legs[side].scores[n].value is not None else '-'}"
            for side in ("on", "off")
            if side in legs
        ]
        notes.append(f"turn {n:>2}: " + "  ".join(cells))
    g = gap(legs["on"], legs["off"]) if len(legs) == 2 else None
    if g is not None:
        notes.append(f"the checkpoint is worth {g:+.2f} on average")
    if "on" not in legs:
        notes.append("only the run without the checkpoint: reported, not judged against the lines")
    return report.finish(
        not problems,
        f"{len(STEPS)} turns, {len(PROBES)} probes",
        notes=[*notes, *problems],
        totals={
            side: {str(n): leg.scores[n].value for n in PROBES} for side, leg in legs.items()
        }
        | {"gap": g, "problems": problems},
        path=opts.json,
    )


def main(argv: Sequence[str] | None = None) -> int:
    return harness.run(_main, argv, about=__doc__ or "")


if __name__ == "__main__":
    sys.exit(main())
```

Wrap any line over ruff's limit. Use `contextlib.nullcontext()` in place of `_nothing()` if mypy accepts the conditional expression's type; otherwise keep `_nothing`.

In the scripted test, `converse` is called with steps whose probes are (1, 6). `verdict` is not called there, so the `PROBES` constant doesn't matter.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/evals -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add evals/drift tests/evals/test_drift.py
git commit -s -m "Evals: drift over a thirty-turn conversation, with the checkpoint on and off (EVL-5)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Docs

**Files:**
- Modify: `docs/PRD.md`, `docs/TESTING.md`, `CONTRIBUTING.md`, `docs/architecture.md`, `docs/specs/2026-10-31-quality-evals-stage-2.md`

- [ ] **Step 1: PRD**
  - **PER-4 (line ~198):** append "Exists: `src/trillion/persona/prompts/checkpoint.md`, added to the per-turn context by `volatile_context` from the 5th user message."
  - **EVL-1 (line ~301):** replace "The nightly run and the run on soul changes are stage 2." with "A change to the soul or prompts needs a fresh baseline, checked free in CI by `tests/evals/test_baselines.py`; the nightly run waits for a shared way to pay for model calls."
  - **EVL-4 (line ~304):** append "Exists: [`evals/gate.py`](../evals/gate.py), with baselines in `evals/baselines/` keyed by model and judge."
  - **EVL-5 (line ~305):** append "Exists: [`evals/drift/`](../evals/drift/run.py), a thirty-turn conversation run with the checkpoint on and off."
- [ ] **Step 2: TESTING.md.** In the "Model-judged behaviour" row or section, add the gate, `--deployed`, `--save-baseline`, `--accept-drop "<why>"` and drift commands, copied from the two module docstrings. Then add one paragraph: "When the soul or a prompt changes, CI fails until a baseline matches it. Run `uv run python -m evals.gate --model gemma4:e4b --save-baseline` (free, through Ollama) and commit the file in `evals/baselines/`. A worse run needs `--accept-drop` with a reason, which reviewers see in the diff. Changing `DEFAULT_MODEL` needs a baseline for the new model, run by whoever proposes it."
- [ ] **Step 3: CONTRIBUTING.md.** Add a short section, "Changing her voice", with the same paragraph, linking to TESTING.md.
- [ ] **Step 4: architecture.md.** Next to the stage 1 evals: one line each for the gate (what it compares) and drift (what it measures). Next to the volatile context: one line for the checkpoint.
- [ ] **Step 5: The stage 2 spec.** Add a "Changes after planning" section:
  - The gate compares code-check rates only with the baseline. The absolute lines stay the persona eval's job, so a model below a line today can still have a baseline.
  - `--deployed` grades Claude and keyless OpenAI-compatible addresses. An OpenAI model stops with exit 2, because the evals send no key to it.
  - `--only off` reports the off run without a verdict.
  - Providers now report the model that answered on `Done.model`.
  - In the gate section, change "any code check falls below its stage 1 line, or more than 5 points below its baseline rate" to "any code check's pass rate falls more than 5 points below its baseline rate".
- [ ] **Step 6: Commit**

```bash
git add docs CONTRIBUTING.md
git commit -s -m "Docs: the tonal checkpoint, the persona gate and drift

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: First baselines and the CI check

This task runs real models, so the controller does it, not a subagent. It needs Ollama running `gemma4:e4b`. The Claude baseline needs `TRILLION_ANTHROPIC_API_KEY` in the environment of whoever runs it, so ask the user before that run. Nothing plays sound.

- [ ] **Step 1: Write the CI check, `tests/evals/test_baselines.py`**

```python
"""The free CI half of the persona gate (EVL-4): no model runs here. A change to the
soul or a prompt, or to the default model, needs a fresh baseline in evals/baselines/,
which anyone can make with a free local model."""

from __future__ import annotations

from evals.baselines import BASELINES, Baseline, fingerprint, load_all

from trillion.brain.providers.anthropic import DEFAULT_MODEL

HOW = "uv run python -m evals.gate --model {model} --save-baseline"


def test_every_baseline_file_is_whole() -> None:
    files = sorted(BASELINES.glob("*.json"))
    assert files, "evals/baselines/ is empty"
    for path in files:
        Baseline.load(path)


def test_a_baseline_matches_the_soul_and_prompts_as_they_are_now() -> None:
    now = fingerprint()
    assert any(b.fingerprint == now for b in load_all()), (
        "The persona changed since the last baseline. Run "
        f"`{HOW.format(model='<any model, e.g. gemma4:e4b>')}` and commit the file."
    )


def test_the_default_model_has_a_baseline() -> None:
    assert any(b.model == DEFAULT_MODEL for b in load_all()), (
        f"No baseline for the default model {DEFAULT_MODEL}. Whoever changes it runs "
        f"`{HOW.format(model=DEFAULT_MODEL)}` and commits the file."
    )
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest tests/evals/test_baselines.py -q`
Expected: FAIL with "evals/baselines/ is empty".

- [ ] **Step 3: Make the free baseline**

Run: `uv run python -m evals.gate --model gemma4:e4b --save-baseline`
Expected: the persona run, then "saved baseline: …/persona-gemma4-e4b--gemma4-e4b.json", exit 0.

- [ ] **Step 4: Make the Claude baseline (ask the user first)**

Run, with the key in the environment: `uv run python -m evals.gate --model claude-opus-5-5 --save-baseline`
Expected: "saved baseline: …/persona-claude-opus-5-5--claude-opus-5-5.json", exit 0.

- [ ] **Step 5: Run drift once on gemma, to record the first numbers**

Run: `uv run python -m evals.drift.run --model gemma4:e4b`
Record the per-turn table and the gap in the spec's "First runs" note. Don't move any line to make it pass.

- [ ] **Step 6: Run everything**

Run: `uv run pytest tests/evals tests/unit tests/api tests/brain -q && uv run ruff check . && uv run ruff format --check . && uv run mypy && python3 scripts/containment_check.py`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add evals/baselines tests/evals/test_baselines.py docs/specs/2026-10-31-quality-evals-stage-2.md
git commit -s -m "Evals: first persona baselines, and CI keeps them fresh

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-review

- **Spec coverage:**
  - Checkpoint: Tasks 1–2.
  - Baselines and fingerprint: Task 5.
  - Gate, wrong-model check, `--deployed`, save and accept-drop rules: Tasks 3–6.
  - CI check and bootstrap: Task 9.
  - Drift, with on/off, the three fail lines, gap and `--only`: Task 7.
  - Docs: Task 8.
  - Platforms (LF writes, line-ending-blind fingerprint, `as_uri` for the read-only database on Windows): Tasks 5–6.
- **Spec changes:** recorded in Task 8 Step 5.
- **Names used across tasks:**
  - `safe_name`, `Sandbox.served`, `Summary.unjudged` and `Summary.silent` (Task 4).
  - `Totals`, `Baseline`, `baseline_path`, `compare`, `may_save`, `model_problems`, `fingerprint`, `load_all`, `BASELINES` (Task 5), used in Tasks 6 and 9.
  - `CHECKPOINT_FROM` (Task 1), used in Task 7.
