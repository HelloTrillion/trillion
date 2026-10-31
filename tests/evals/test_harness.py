"""The shared eval runner, with a scripted model and a scripted judge. No real model."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from evals.harness import CannotRun, Judge, Report, Score, parse_score

from evals import harness
from tests.brain.fakes import ScriptedProvider, say
from trillion.brain.config import ANTHROPIC_KEY
from trillion.brain.providers.anthropic import DEFAULT_MODEL, AnthropicProvider
from trillion.secrets.store import KeychainStore

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


# ── The judge ────────────────────────────────────────────────────────────

JSON4 = '{"score": 4, "reason": "dry enough"}'


def test_a_judge_answer_is_read_from_json_even_in_a_fence() -> None:
    assert parse_score(JSON4) == Score(value=4, reason="dry enough")
    assert parse_score(f"```json\n{JSON4}\n```") == Score(value=4, reason="dry enough")


@pytest.mark.parametrize(
    "bad",
    [
        "4",
        "not json",
        "[]",
        '{"score": 7, "reason": "x"}',
        '{"score": 0, "reason": "x"}',
        '{"score": "4", "reason": "x"}',
        '{"score": true, "reason": "x"}',
        '{"score": 3}',
    ],
)
def test_anything_else_is_not_a_score(bad: str) -> None:
    assert parse_score(bad) is None


def test_a_judge_that_answers_badly_is_asked_once_more() -> None:
    scripted = ScriptedProvider(say("Sure! I'd give it a 4."), say(JSON4))
    with Judge(scripted, local=True) as judge:
        assert judge.score("Is it dry?", "Sam (typed): hi\nNova: Hi.") == Score(
            value=4, reason="dry enough"
        )
    assert len(scripted.calls) == 2
    system, messages = scripted.calls[0]
    assert "JSON only" in system[0].text
    asked = messages[0].blocks[0].text
    assert (
        asked.startswith("Is it dry?")
        and "<transcript>\nSam (typed): hi\nNova: Hi.\n</transcript>" in asked
    )


def test_two_bad_answers_are_a_judge_error_and_never_a_pass() -> None:
    with Judge(ScriptedProvider(say("no"), say("still no")), local=True) as judge:
        s = judge.score("rubric", "transcript")
    assert s.error and s.value is None and s.reason.startswith("judge error")


def test_a_judge_that_fails_outright_is_a_judge_error() -> None:
    # No scripted replies: every call raises ProviderError("script_exhausted").
    with Judge(ScriptedProvider(), local=False) as judge:
        s = judge.score("rubric", "transcript")
    assert s.error and "script_exhausted" in s.reason


def test_the_judge_line_says_when_scores_are_less_reliable() -> None:
    with Judge(ScriptedProvider(), local=True) as judge:
        assert judge.line == "judge: fake-1 (local judge: scores are less reliable)"
    with Judge(ScriptedProvider(), local=False) as judge:
        assert judge.line == "judge: fake-1"


# ── The model under test and the judge ───────────────────────────────────

LOCAL = "http://localhost:11434/v1"


def refused(url: str, **kw: Any) -> httpx.Response:
    raise httpx.ConnectError("refused")


def answers(url: str, **kw: Any) -> httpx.Response:
    assert url == f"{LOCAL}/models"
    return httpx.Response(200, json={"data": []})


def test_a_local_model_must_answer_before_the_eval_starts() -> None:
    with pytest.raises(CannotRun, match="nothing answered"):
        harness.model_provider("gemma4:e4b", LOCAL, {}, get=refused)
    make = harness.model_provider("gemma4:e4b", LOCAL, {}, get=answers)
    first = make()
    # One per conversation: a provider's connections belong to one event loop.
    assert first.model == "gemma4:e4b" and make() is not first


def test_a_claude_model_needs_the_key_in_the_environment() -> None:
    with pytest.raises(CannotRun, match=ANTHROPIC_KEY):
        harness.model_provider("claude-opus-5-5", LOCAL, {}, get=refused)
    made = harness.model_provider("claude-opus-5-5", LOCAL, {ANTHROPIC_KEY: "test"}, get=refused)()
    assert isinstance(made, AnthropicProvider) and made.model == "claude-opus-5-5"


def test_the_judge_is_claude_with_a_key_and_the_local_model_without() -> None:
    with harness.judge_for("gemma4:e4b", LOCAL, {ANTHROPIC_KEY: "test"}, get=refused) as judge:
        assert not judge.local and judge.line == f"judge: {DEFAULT_MODEL}"
    with harness.judge_for("gemma4:e4b", LOCAL, {}, get=answers) as judge:
        assert judge.local
        assert judge.line == "judge: gemma4:e4b (local judge: scores are less reliable)"
    with pytest.raises(CannotRun, match="nothing answered"):
        harness.judge_for("gemma4:e4b", LOCAL, {}, get=refused)


def test_an_eval_never_touches_the_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    def touched(*args: Any, **kw: Any) -> None:
        raise AssertionError("the keychain was touched")

    for name in ("available", "get", "set", "delete"):
        monkeypatch.setattr(KeychainStore, name, touched)
    harness.model_provider("gemma4:e4b", LOCAL, {}, get=answers)()
    harness.judge_for("gemma4:e4b", LOCAL, {}, get=answers).close()
    harness.judge_for("gemma4:e4b", LOCAL, {ANTHROPIC_KEY: "test"}, get=refused).close()
