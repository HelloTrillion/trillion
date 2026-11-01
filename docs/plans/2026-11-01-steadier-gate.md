# A Steadier Persona Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The persona gate pools several runs (three with a local judge, one with Claude) and re-asks a prompt that got no reply, so run-to-run noise on a small local model stops tripping its limits.

**Architecture:** `evals/persona/run.py` splits `evaluate` into `ask` (one run: ask, re-ask if silent, judge, repeated-opener check) and `report_rows` (print). `evals/baselines.py` gains a `runs` field. `evals/gate.py` loops `ask` N times, pools the rows into one `summarize`, records `runs`, and notes a run-count mismatch with its baseline.

**Tech Stack:** Python 3.13, pytest, `uv run`. Spec: [docs/specs/2026-11-01-steadier-gate.md](../specs/2026-11-01-steadier-gate.md).

**House rules:** commits use `git commit -s` and end with a blank line then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Generic names only (`scripts/containment_check.py` must pass). Never run a real model in tests. Before each commit: `uv run ruff check . && uv run ruff format --check . && uv run mypy src evals`.

---

## File map

- Modify `evals/persona/run.py`: `Row.run`, `Row.retried`, `Row.label`; `ask`, `report_rows`; `evaluate` uses them; `Summary.retried`.
- Modify `evals/baselines.py`: `Baseline.runs`.
- Modify `evals/gate.py`: `--runs`, `LOCAL_RUNS`, pooling in `check`, the runs note, JSON `runs`, docstring.
- Tests: `tests/evals/test_persona_run.py`, `tests/evals/test_gate.py`.
- Docs: `docs/TESTING.md`, `CONTRIBUTING.md`.
- Data (controller, Task 4): `evals/baselines/persona-gemma4-e4b--gemma4-e4b.json`.

---

### Task 1: The persona eval asks one run at a time, and re-asks a silent prompt

**Files:**
- Modify: `evals/persona/run.py` (`Row`, `Summary`, `summarize`, `evaluate`)
- Test: `tests/evals/test_persona_run.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/evals/test_persona_run.py`; add `ask` and `report_rows` to the `evals.persona.run` import)

```python
def test_a_silent_prompt_is_asked_once_more_in_a_new_conversation(tmp_path: Path) -> None:
    prompts = [Prompt(id="everyday-01", group="everyday", text="Lunch?")]
    scripted = ScriptedProvider(say(""), say("Soup."))
    with (
        Sandbox(lambda: scripted, root=tmp_path / "evals") as sb,
        Judge(ScriptedProvider(judged(4)), local=True) as judge,
    ):
        (r,) = ask(sb, judge, prompts)
    assert r.reply == "Soup." and r.retried and r.error is None and r.score.value == 4
    # The second ask is a new conversation: it carries only the prompt, not the silence.
    assert len(scripted.calls[1][1]) == 1


def test_silent_twice_is_silent(tmp_path: Path) -> None:
    prompts = [Prompt(id="everyday-01", group="everyday", text="Lunch?")]
    with (
        Sandbox(lambda: ScriptedProvider(say(""), say("")), root=tmp_path / "evals") as sb,
        Judge(ScriptedProvider(), local=True) as judge,
    ):
        (r,) = ask(sb, judge, prompts)
    assert r.retried and r.error is not None and r.score.value is None
    assert summarize([r]).silent == 1


def test_a_reply_first_time_is_asked_once(tmp_path: Path) -> None:
    prompts = [Prompt(id="everyday-01", group="everyday", text="Lunch?")]
    scripted = ScriptedProvider(say("Soup."))
    with (
        Sandbox(lambda: scripted, root=tmp_path / "evals") as sb,
        Judge(ScriptedProvider(judged(4)), local=True) as judge,
    ):
        (r,) = ask(sb, judge, prompts, run=2)
    assert not r.retried and len(scripted.calls) == 1
    assert r.run == 2 and r.label == "everyday-01 #2"


def test_lines_say_the_run_from_the_second_on_and_who_was_asked_twice() -> None:
    first, second = row(), row()
    second.run, second.retried = 2, True
    out = io.StringIO()
    report = Report("persona", "fake-1", "judge", out=out)
    report_rows(report, [first, second])
    assert [c["id"] for c in report.cases] == ["everyday-x", "everyday-x #2"]
    assert report.cases[1]["retried"] is True and report.cases[1]["run"] == 2
    assert "asked twice" in out.getvalue().splitlines()[-1]


def test_the_summary_counts_prompts_asked_twice() -> None:
    again = row()
    again.retried = True
    s = summarize([row(), again])
    assert s.retried == 1 and s.totals()["retried"] == 1
    assert not any("asked" in p for p in s.problems)  # a note, never a problem


def test_the_lowest_list_names_the_run() -> None:
    low = row(value=1)
    low.run = 3
    assert any("everyday-x #3" in line for line in summarize([row(), low]).measures())
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/evals/test_persona_run.py -q`
Expected: ImportError for `ask` / `report_rows`.

- [ ] **Step 3: Implement** in `evals/persona/run.py`

Add to `Row` (after `board`):

```python
    # Which run of the eval this reply came from, counting from 1.
    run: int = 1
    # Whether the prompt got no reply at first and was asked again.
    retried: bool = False

    @property
    def label(self) -> str:
        """The prompt's id, with its run from the second on: "bait-06 #2"."""
        return self.prompt.id if self.run == 1 else f"{self.prompt.id} #{self.run}"
```

Add to `Summary` (after `silent`): `retried: int = 0`. In `Summary.totals()` add `"retried": self.retried`. In `Summary.measures()` use `r.label` in place of `r.prompt.id` on the lowest-scored lines. In `summarize`, pass `retried=sum(1 for r in rows if r.retried)`.

Replace `evaluate` with:

```python
def ask(sb: Sandbox, judge: Judge, prompts: Sequence[Prompt], *, run: int = 1) -> list[Row]:
    """One run: each prompt in a new conversation, scored by code and by the judge. A
    prompt with no reply is asked once more, in a new conversation, so one hiccup from
    a local model isn't counted as silence."""
    rubric = f"{RUBRIC}\n\n<soul>\n{SOUL.read_text(encoding='utf-8')}\n</soul>"
    rows: list[Row] = []
    for p in prompts:
        sb.fresh()
        t = sb.converse([Ask(text=p.text, via=p.via)])
        retried = not t.turns[0].reply
        if retried:
            sb.fresh()
            t = sb.converse([Ask(text=p.text, via=p.via)])
        (turn,) = t.turns
        applies, failed = code_checks(p, turn.reply)
        if p.group == "numbers":
            applies.add("invented_number")
            figure = made_up_figure(p, turn)
            if figure is not None:
                failed["invented_number"] = f"{figure} came from no tool and not from the user"
        r = Row(
            prompt=p,
            reply=turn.reply,
            applies=applies,
            failed=failed,
            error=None if turn.reply else (turn.error or "no reply"),
            board=tuple(t.args for t in turn.tools if t.name.startswith(BOARD)),
            run=run,
            retried=retried,
        )
        if r.error is None:
            r.score = judge.score(rubric, t.render())
        rows.append(r)
    # Within this run only: a reply is compared with the ones just before it.
    for i in repeated_openers([r.reply for r in rows]):
        rows[i].failed["repeated_opener"] = "opens like a reply shortly before it"
    return rows


def report_rows(report: Report, rows: Sequence[Row]) -> None:
    for r in rows:
        what = (
            "; ".join(f"{k}: {v}" for k, v in r.failed.items())
            or r.error
            or " ".join(r.reply.split())[:70]
        )
        if r.retried:
            what = f"(asked twice) {what}"
        report.case(
            r.label,
            not r.failed and r.error is None and r.score.value is not None,
            f"score={r.score.value if r.score.value is not None else '-'}  {what}",
            group=r.prompt.group,
            via=r.prompt.via,
            prompt=r.prompt.text,
            reply=r.reply,
            failed=r.failed,
            score=r.score.value,
            reason=r.score.reason,
            board=list(r.board),
            run=r.run,
            retried=r.retried,
        )


def evaluate(sb: Sandbox, judge: Judge, report: Report, prompts: Sequence[Prompt]) -> Summary:
    rows = ask(sb, judge, prompts)
    report_rows(report, rows)
    return summarize(rows)
```

The silent-twice test checks the judge's provider was never called (`jp.calls == []`); `ask` must not judge a silent row (it doesn't: `r.error` is set).

- [ ] **Step 4: Run the persona and gate tests**

Run: `uv run pytest tests/evals -q`
Expected: all pass except, until a baseline is re-saved, nothing else changes. `test_a_silent_prompt_fails_the_gate` still passes: the fake's script runs out on the re-ask, which is silent too.

- [ ] **Step 5: Commit**

```bash
git add evals/persona/run.py tests/evals/test_persona_run.py
git commit -s -m "Evals: the persona eval asks a silent prompt once more, one run at a time

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: A baseline says how many runs it pooled

**Files:**
- Modify: `evals/baselines.py` (`Baseline`)
- Test: `tests/evals/test_gate.py`

- [ ] **Step 1: Write the failing tests** (append near the other baseline tests in `tests/evals/test_gate.py`)

```python
def test_a_baseline_keeps_its_run_count(tmp_path: Path) -> None:
    b = Baseline(
        model="m",
        judge="j",
        answered_by=("m",),
        date="2026-11-01T00:00:00+00:00",
        fingerprint="f",
        totals=Totals(mean=4.0, groups={"bait": 4.0}, rates={"markdown": 1.0}),
        runs=3,
    )
    b.save(tmp_path / "b.json")
    assert Baseline.load(tmp_path / "b.json").runs == 3
    assert json.loads((tmp_path / "b.json").read_text(encoding="utf-8"))["runs"] == 3


def test_a_baseline_from_before_runs_counts_as_one(tmp_path: Path) -> None:
    raw = {
        "model": "m", "judge": "j", "answered_by": ["m"], "date": "d", "fingerprint": "f",
        "mean": 4.0, "groups": {}, "rates": {},
    }
    assert Baseline.from_json(raw).runs == 1
    with pytest.raises(ValueError, match="runs"):
        Baseline.from_json({**raw, "runs": 0})
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/evals/test_gate.py -q -k runs`
Expected: TypeError: unexpected keyword argument 'runs'.

- [ ] **Step 3: Implement** in `evals/baselines.py`

In `Baseline`, after `accepted_drop`:

```python
    # How many runs of the persona eval it pooled. A file from before this field: 1.
    runs: int = 1
```

In `to_json`, add `"runs": self.runs,` to `body`. In `from_json`, before `return`:

```python
        runs = int(raw.get("runs", 1))
        if runs < 1:
            raise ValueError("a baseline's runs must be 1 or more")
```

and pass `runs=runs` to `cls(...)`.

- [ ] **Step 4: Run the eval tests**

Run: `uv run pytest tests/evals -q`
Expected: all pass. The committed baselines have no `runs` and load as 1.

- [ ] **Step 5: Commit**

```bash
git add evals/baselines.py tests/evals/test_gate.py
git commit -s -m "Evals: a baseline says how many runs it pooled

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The gate pools several runs

**Files:**
- Modify: `evals/gate.py` (docstring, `check`, `_extra`, `_main`)
- Test: `tests/evals/test_gate.py`

- [ ] **Step 1: Write the failing tests**

In `tests/evals/test_gate.py`, give `gate_run` a `runs: int = 1` parameter. Build the model's script with one reply per run, `ScriptedProvider(*[reply if reply is not None else say("Soup.")] * runs)`, and the judge's with `judging or ScriptedProvider(*[judged(value)] * runs)`, and pass `runs=runs` to `gate.check`. Then append:

```python
def test_runs_are_pooled_into_one_summary(tmp_path: Path) -> None:
    judging = ScriptedProvider(judged(5), judged(3), judged(4))
    code, said = gate_run(tmp_path, 0, judging=judging, runs=3, save=True)
    assert code == 0
    assert "3 runs × 1 prompts, judge average 4.00" in said
    assert [ln.split()[1:3] for ln in said.splitlines() if ln.startswith("pass")] == [
        ["everyday-01", "score=5"], ["everyday-01", "#2"], ["everyday-01", "#3"],
    ]
    saved = Baseline.load(saved_file(tmp_path))
    assert saved.runs == 3 and saved.totals.mean == 4.0
    assert results(tmp_path)["runs"] == 3


def test_one_run_reads_as_before(tmp_path: Path) -> None:
    code, said = gate_run(tmp_path, 4, save=True)
    assert code == 0 and "1 prompts, judge average 4.00" in said and "runs ×" not in said
    assert Baseline.load(saved_file(tmp_path)).runs == 1


def test_a_baseline_from_a_different_run_count_is_noted_not_failed(tmp_path: Path) -> None:
    gate_run(tmp_path, 4, save=True)
    code, said = gate_run(tmp_path, 4, runs=3)
    assert code == 0
    assert (
        "the baseline is from 1 run, this from 3; re-save it with --runs 3"
        " for a steadier yardstick" in said
    )
    gate_run(tmp_path, 4, runs=3, save=True)
    code, said = gate_run(tmp_path, 4)
    assert code == 0 and "the baseline is from 3 runs, this from 1" in said
    assert "re-save" not in said

```

And a prompt asked twice, with its own sandbox because the model's script differs between the two asks:

```python
def test_a_prompt_asked_twice_is_noted(tmp_path: Path) -> None:
    out = io.StringIO()
    scripted = ScriptedProvider(say(""), say("Soup."))
    with (
        Sandbox(lambda: scripted, root=tmp_path / "e") as sb,
        Judge(ScriptedProvider(judged(4)), local=True) as judge,
    ):
        report = Report("gate", "fake-1", judge.line, out=out)
        code = gate.check(
            sb, judge, report, PROMPTS, model="fake-1",
            folder=tmp_path / "baselines", json_path=tmp_path / "r.json", save=True,
        )
    said = out.getvalue()
    assert code == 0
    assert "1 prompts got no reply the first time and were asked again" in said
    assert results(tmp_path)["gate"]["hard"] == []


def test_the_default_run_count_follows_the_judge() -> None:
    assert gate.default_runs(local=True) == gate.LOCAL_RUNS == 3
    assert gate.default_runs(local=False) == 1
```

Add to the `test_contradictory_options_stop_before_any_model_runs` parameters:

```python
        (["--runs", "0"], "--runs"),
        (["--runs", "two"], "--runs"),
```

and in `test_the_command_line_offers_the_gates_options` include `"--runs"` in the options tuple.

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/evals/test_gate.py -q`
Expected: failures on `runs` (unexpected keyword) and `default_runs` (missing attribute).

- [ ] **Step 3: Implement** in `evals/gate.py`

Imports: `from evals.persona.run import Row, ask, report_rows, summarize` (drop `evaluate`).

After the imports:

```python
# Runs pooled when nobody passes --runs. A local judge (and usually a local model) is
# noisy run to run and costs only time; a Claude judge is steadier, and costs money.
LOCAL_RUNS = 3


def default_runs(*, local: bool) -> int:
    return LOCAL_RUNS if local else 1


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"
```

In `check`, add the keyword `runs: int = 1`, and replace `s = evaluate(sb, judge, report, prompts)` with:

```python
    rows: list[Row] = []
    for n in range(1, runs + 1):
        batch = ask(sb, judge, prompts, run=n)
        # Printed as each run ends, so a long local run shows it is moving.
        report_rows(report, batch)
        rows += batch
    s = summarize(rows)
```

After `hard, notes = model_problems(...)` and the `unjudged`/`silent` lines, add:

```python
    if s.retried:
        notes.append(
            f"{s.retried} prompts got no reply the first time and were asked again"
        )
    if base is not None and base.runs != runs:
        mismatch = f"the baseline is from {_plural(base.runs, 'run')}, this from {runs}"
        if base.runs < runs:
            mismatch += f"; re-save it with --runs {runs} for a steadier yardstick"
        notes.append(mismatch)
```

Pass `runs=runs` to the saved `Baseline(...)`. Replace the summary string with:

```python
    size = f"{len(prompts)} prompts" if runs == 1 else f"{runs} runs × {len(prompts)} prompts"
```

and use `f"{size}, judge average {mean}"`. Add `"runs": runs,` to the `totals` dict passed to `report.finish`.

In `_extra`:

```python
    p.add_argument(
        "--runs",
        type=_runs,
        default=None,
        metavar="N",
        help=f"runs to pool (default {LOCAL_RUNS} with a local judge, 1 with Claude)",
    )
```

with, above `_extra`:

```python
def _runs(text: str) -> int:
    try:
        n = int(text)
    except ValueError:
        n = 0
    if n < 1:
        raise argparse.ArgumentTypeError("--runs takes a whole number, 1 or more")
    return n
```

Check that `harness.run` turns an argparse error into exit 2 with the message on stderr (argparse's own `error()` exits 2 and writes `usage: … error: argument --runs: --runs takes a whole number, 1 or more`, which contains `--runs`). If `harness.run` catches `SystemExit` differently, follow what it does for `--only`.

In `_main`, inside the `judge_for` block, before `report = ...`:

```python
        runs = opts.runs or default_runs(local=judge.local)
```

and pass `runs=runs` to `check`.

Docstring: add after the usage lines

```
    uv run python -m evals.gate --model gemma4:e4b --runs 5
```

and to the prose: "With a local judge it pools three runs of the eval (one with Claude, which is steadier); --runs sets another number. A prompt with no reply is asked once more before it counts as silent."

- [ ] **Step 4: Run all eval tests, lint and types**

Run: `uv run pytest tests/evals -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src evals`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add evals/gate.py tests/evals/test_gate.py
git commit -s -m "Evals: the gate pools three runs with a local judge (--runs)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Docs, and a three-run gemma baseline

**Files:**
- Modify: `docs/TESTING.md`, `CONTRIBUTING.md`
- Data: `evals/baselines/persona-gemma4-e4b--gemma4-e4b.json` (controller, by hand)

- [ ] **Step 1: TESTING.md.** In the gate section, after its commands, add:

```markdown
With a local judge the gate pools three runs of the eval, because one run of a small model swings by more than the gate's limits; on `gemma4:e4b` that takes about an hour. With Claude as the judge it runs once. `--runs N` sets another number. A baseline records how many runs it pooled, and the gate notes when a run and its baseline differ. A prompt that gets no reply is asked once more before it counts as silent.
```

- [ ] **Step 2: CONTRIBUTING.md, "Changing her voice".** After the sentence that gives the gate command, add:

```markdown
With a local judge it runs the eval three times and averages them: one run of a small model is mostly noise. Expect about an hour on `gemma4:e4b`.
```

- [ ] **Step 3: Commit the docs**

```bash
git add docs/TESTING.md CONTRIBUTING.md
git commit -s -m "Docs: the gate pools three runs with a local judge

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 4 (controller): re-save the gemma baseline from three runs**

```bash
env -u TRILLION_ANTHROPIC_API_KEY uv run python -m evals.gate --model gemma4:e4b --save-baseline --accept-drop "Re-saved from three pooled runs in place of one; a single run of gemma judging itself is noise, so movement against the one-run file is not a change in her."
```

Expected: `PASS`, the note "the baseline is from 1 run, this from 3; re-save it…", and `saved baseline`. The reason is written only if there were drops. If a hard problem (a silent prompt after its re-ask, an unjudged reply) refuses the save, run it again.

- [ ] **Step 5 (controller): commit the baseline and record the run in the spec**

Add a "First run" section to the spec with the pooled scores and how long it took, then:

```bash
git add evals/baselines/persona-gemma4-e4b--gemma4-e4b.json docs/specs/2026-11-01-steadier-gate.md
git commit -s -m "Evals: the gemma baseline, pooled from three runs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
