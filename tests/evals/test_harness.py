"""The shared eval runner, with a scripted model and a scripted judge. No real model."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from evals.harness import CannotRun, Report

from evals import harness

AT = datetime(2026, 10, 31, 12, 0, 0, tzinfo=UTC)

# ── Options, exit codes, picking cases ───────────────────────────────────


def test_every_eval_takes_the_same_options() -> None:
    o = harness.options("about", [])
    assert (o.model, o.url, o.judge_model, o.judge_url, o.only, o.json) == (
        "gemma4:e4b",
        "http://localhost:11434/v1",
        "gemma4:e4b",
        "http://localhost:11434/v1",
        [],
        None,
    )
    o = harness.options(
        "about", ["--model", "claude-opus-5-5", "--only", "a", "--only", "b", "--json", "o.json"]
    )
    assert (o.model, o.only, o.json) == ("claude-opus-5-5", ["a", "b"], Path("o.json"))


def test_pass_fail_and_could_not_run_are_0_1_and_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert harness.run(lambda o: harness.PASS, []) == 0
    assert harness.run(lambda o: harness.FAIL, []) == 1

    def broken(o: object) -> int:
        raise CannotRun("nothing answered at http://localhost:11434/v1")

    assert harness.run(broken, []) == 2
    assert "cannot run: nothing answered" in capsys.readouterr().err


def test_only_picks_cases_by_any_of_their_names() -> None:
    cases = [("a", "g1"), ("b", "g2"), ("c", "g1")]
    assert harness.pick(cases, [], lambda c: c) == cases
    assert harness.pick(cases, ["g1"], lambda c: c) == [("a", "g1"), ("c", "g1")]
    assert harness.pick(cases, ["b"], lambda c: c) == [("b", "g2")]
    with pytest.raises(CannotRun, match="no case named zz"):
        harness.pick(cases, ["zz"], lambda c: c)


# ── The report ───────────────────────────────────────────────────────────


def test_results_are_named_by_eval_model_and_utc_time() -> None:
    assert harness.results_path("persona", "gemma4:e4b", AT) == (
        harness.RESULTS / "persona-gemma4-e4b-20261031T120000Z.json"
    )


def test_the_report_names_the_judge_first_then_cases_then_the_verdict(tmp_path: Path) -> None:
    out = io.StringIO()
    judge = "judge: gemma4:e4b (local judge: scores are less reliable)"
    report = Report("persona", "gemma4:e4b", judge, out=out, now=lambda: AT)
    report.case("everyday-01", True, "score=4", score=4)
    report.case("bait-01", False, "as_an_ai: i'm an ai", score=2)
    code = report.finish(
        False,
        "1 of 2 failed",
        notes=["as_an_ai 50%"],
        totals={"cases": 2},
        path=tmp_path / "r.json",
    )
    lines = out.getvalue().splitlines()
    assert lines[0] == judge
    assert lines[1].startswith("pass  everyday-01") and lines[2].startswith("FAIL  bait-01")
    assert lines[3] == "as_an_ai 50%"
    assert lines[4] == "FAIL (gemma4:e4b): 1 of 2 failed"
    assert lines[5] == f"results: {tmp_path / 'r.json'}"
    assert code == harness.FAIL
    raw = (tmp_path / "r.json").read_bytes()
    assert b"\r\n" not in raw
    body = json.loads(raw.decode("utf-8"))
    assert set(body) == {
        "eval", "model", "judge", "started", "finished", "verdict", "summary", "totals", "cases",
    }  # fmt: skip
    assert (body["eval"], body["model"], body["judge"], body["verdict"]) == (
        "persona", "gemma4:e4b", judge, "fail",
    )  # fmt: skip
    assert body["started"] == "2026-10-31T12:00:00+00:00"
    assert body["totals"] == {"cases": 2}
    assert body["cases"] == [
        {"id": "everyday-01", "passed": True, "score": 4},
        {"id": "bait-01", "passed": False, "score": 2},
    ]


def test_a_passing_report_exits_0_and_makes_its_folder(tmp_path: Path) -> None:
    report = Report("injection", "fake-1", "judge: none (scored by code)", out=io.StringIO())
    assert report.finish(True, "0 actions", path=tmp_path / "deep" / "r.json") == harness.PASS
    assert (
        json.loads((tmp_path / "deep" / "r.json").read_text(encoding="utf-8"))["verdict"] == "pass"
    )
