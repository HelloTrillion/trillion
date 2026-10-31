"""The persona gate's baselines and rules (EVL-4), with no model."""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

import pytest
from evals.baselines import (
    Baseline,
    Totals,
    baseline_path,
    compare,
    fingerprint,
    load_all,
    may_save,
    model_problems,
    same_model,
)
from evals.harness import CannotRun, Judge, Report, Sandbox
from evals.persona.cases import Prompt

from evals import gate
from tests.brain.fakes import USAGE, ScriptedProvider, say
from trillion.brain.providers.anthropic import DEFAULT_MODEL
from trillion.brain.types import Done, ModelEvent, TextDelta


def files(tmp_path: Path, soul: str = "soul\n", rule: str = "rule\n") -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
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
    assert load_all(tmp_path) == [b]


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


def test_replies_that_named_no_model_are_noted() -> None:
    problems, notes = model_problems("m", {"m"}, unreported=2)
    assert problems == [] and notes == ["2 replies didn't say which model answered"]
    # When none said, the one note covers it.
    problems, notes = model_problems("m", set(), unreported=3)
    assert problems == [] and notes == ["the provider never said which model answered"]


def test_saving_needs_a_clean_run_or_a_stated_reason() -> None:
    assert may_save(hard=[], drops=[], accept_drop=None) is None
    assert may_save(hard=[], drops=["bait averaged 3.40"], accept_drop="new judge") is None
    refused = may_save(hard=[], drops=["bait averaged 3.40"], accept_drop=None)
    assert refused is not None and "--accept-drop" in refused
    # A wrong model or an unjudged reply is never accepted away.
    assert may_save(hard=["asked for a, but b answered"], drops=[], accept_drop="x") is not None
    assert may_save(hard=[], drops=[], accept_drop="  ") is not None  # a reason, not blank


# ── The gate ─────────────────────────────────────────────────────────────


def judged(value: int) -> list[ModelEvent]:
    return say(f'{{"score": {value}, "reason": "scored {value}"}}')


def answered(model: str) -> list[ModelEvent]:
    return [TextDelta(text="Soup."), Done(stop_reason="end", usage=USAGE, model=model)]


PROMPTS = [Prompt(id="everyday-01", group="everyday", text="Lunch?")]


def gate_run(
    tmp_path: Path, value: int, reply: list[ModelEvent] | None = None, **kw: object
) -> tuple[int, str]:
    out = io.StringIO()
    scripted = ScriptedProvider(reply if reply is not None else say("Soup."))
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
    assert "the provider never said which model answered" in said
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


def test_a_low_score_that_held_its_baseline_passes(tmp_path: Path) -> None:
    # The persona eval's own lines are printed, but the gate fails on change.
    gate_run(tmp_path, 2, save=True)
    code, said = gate_run(tmp_path, 2)
    assert code == 0 and "the line is" in said


def test_a_reply_from_another_model_fails_and_is_never_saved(tmp_path: Path) -> None:
    code, said = gate_run(tmp_path, 4, reply=answered("fake-1"), save=True)
    assert code == 0
    (saved,) = (tmp_path / "baselines").glob("*.json")
    assert Baseline.load(saved).answered_by == ("fake-1",)
    code, said = gate_run(tmp_path, 4, reply=answered("other-2"), save=True)
    assert code == 1 and "asked for fake-1, but other-2 answered" in said
    assert Baseline.load(saved).answered_by == ("fake-1",)


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
    assert gate.deployed(tmp_path / "none.db", env) == (DEFAULT_MODEL, "")


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


def test_the_command_line_offers_the_gates_options(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        gate.main(["--help"])
    usage = capsys.readouterr().out
    assert all(o in usage for o in ("--deployed", "--save-baseline", "--accept-drop"))
