"""The drift eval (EVL-5): its script, its verdict, and a scripted run."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from evals.drift import run as drift
from evals.drift.run import Leg, converse, gap, verdict
from evals.drift.script import PROBES, STEPS, Step
from evals.harness import Judge, Report, Sandbox, Score

from evals import harness
from tests.brain.fakes import ScriptedProvider, say
from trillion.brain.types import ContextBlock
from trillion.persona import prompt


def test_thirty_turns_a_third_spoken_seven_probes() -> None:
    assert [s.n for s in STEPS] == list(range(1, 31))
    assert PROBES == (1, 5, 10, 15, 20, 25, 30)
    assert sum(s.via == "voice" for s in STEPS) == 11


def leg(
    scores: dict[int, int | None],
    failed: dict[int, dict[str, str]] | None = None,
    turns: int = 30,
) -> Leg:
    return Leg(
        checkpoint=True,
        replies={n: "Fine." for n in range(1, turns + 1)},
        scores={n: Score(value=v, reason="r") for n, v in scores.items()},
        applies={n: {"banned_opener", "as_an_ai", "repeated_opener"} for n in range(1, turns + 1)},
        failed=failed or {},
    )


STEADY = {1: 4, 5: 4, 10: 4, 15: 4, 20: 4, 25: 4, 30: 4}


def test_a_steady_conversation_passes() -> None:
    assert verdict(leg(STEADY), PROBES) == []


def test_a_slide_of_more_than_half_a_point_fails() -> None:
    slid = {**STEADY, 20: 3, 25: 3, 30: 4}
    (problem,) = verdict(leg(slid), PROBES)
    assert "last three probes averaged 3.33" in problem and "4.00" in problem


def test_a_slide_of_exactly_half_a_point_is_allowed() -> None:
    # Scores are whole numbers, so a slide of exactly 0.5 needs a missing probe: 4.5
    # over the first two judged, 4.0 over the last three.
    problems = verdict(leg({**STEADY, 1: None, 5: 5}), PROBES)
    assert not any("averaged" in p for p in problems)


def test_the_fail_rate_line_is_compared_without_float_noise() -> None:
    # One reply in twenty fails: exactly 5%, which is allowed, though 1 - 0.95 is
    # a hair over 0.05 in floats.
    held = leg({1: 4, 5: 4, 10: 4, 15: 4, 20: 4}, {2: {"banned_opener": "x"}}, turns=20)
    assert verdict(held, (1, 5, 10, 15, 20)) == []


def test_one_late_probe_below_three_fails() -> None:
    assert any("turn 15 scored 2" in p for p in verdict(leg({**STEADY, 15: 2}), PROBES))
    # Before the checkpoint starts, a low score is not this line's business.
    assert not any("turn 1 scored" in p for p in verdict(leg({**STEADY, 1: 2}), PROBES))


def test_an_unjudged_probe_is_never_a_pass() -> None:
    assert any("could not be judged" in p for p in verdict(leg({**STEADY, 10: None}), PROBES))


def test_a_turn_with_no_reply_is_never_a_pass() -> None:
    silent = leg(STEADY)
    silent.replies[7] = ""
    assert any("1 turns got no reply" in p for p in verdict(silent, PROBES))


def test_a_code_check_failing_on_more_than_five_percent_fails() -> None:
    failed = {2: {"banned_opener": "x"}, 3: {"banned_opener": "y"}}
    problems = verdict(leg(STEADY, failed), PROBES)
    assert any(p.startswith("banned_opener held on 93.3%") for p in problems)
    assert verdict(leg(STEADY, {2: {"banned_opener": "x"}}), PROBES) == []  # 96.7%


def test_the_gap_is_the_on_run_minus_the_off_run() -> None:
    off = leg({n: 3 for n in STEADY})
    assert gap(leg(STEADY), off) == 1.0
    assert gap(leg(STEADY), leg(dict.fromkeys(STEADY))) is None


def test_only_takes_on_and_off_before_any_model_is_built(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_model(*args: object) -> None:
        raise AssertionError("a model was built")

    monkeypatch.setattr(harness, "model_provider", no_model)
    assert drift.main(["--only", "sideways"]) == harness.CANNOT_RUN
    assert "--only takes on or off" in capsys.readouterr().err


def test_one_conversation_with_the_checkpoint_on_then_off(tmp_path: Path) -> None:
    steps = tuple(Step(n=i, text=f"m{i}", probe=i in (1, 6)) for i in range(1, 7))
    # ScriptedProvider answers her naming the conversation by itself and keeps it out
    # of `calls`, so the calls here are turns only.
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
    # The off run puts the checkpoint back when it is done.
    assert prompt.CHECKPOINT_FROM == 5
    assert [c["id"] for c in report.cases] == ["on-01", "on-06", "off-01", "off-06"]
