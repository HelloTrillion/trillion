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
    load_all,
    may_save,
    model_problems,
    same_model,
)


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
