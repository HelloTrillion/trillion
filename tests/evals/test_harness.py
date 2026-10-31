"""The shared eval runner, with a scripted model and a scripted judge. No real model."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite.core
import httpx
import pytest
from evals.harness import (
    Ask,
    CannotRun,
    Judge,
    Report,
    Sandbox,
    Score,
    options,
    parse_score,
    safe_name,
)

from evals import harness
from tests.brain.fakes import ScriptedProvider, say, use
from tests.unit.test_web import Wire, names, response
from trillion.brain.config import ANTHROPIC_KEY
from trillion.brain.providers.anthropic import DEFAULT_MODEL, AnthropicProvider
from trillion.brain.types import (
    ContextBlock,
    Done,
    ModelEvent,
    Msg,
    SystemBlock,
    TextBlock,
    TextDelta,
    Usage,
)
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


@pytest.mark.parametrize(
    "error",
    [TimeoutError("no frame in 300s"), OSError("disk full"), RuntimeError("session gone")],
)
def test_anything_else_that_stops_a_run_is_could_not_run_too(
    error: Exception, capsys: pytest.CaptureFixture[str]
) -> None:
    # A stalled model, a report that could not be written, a session that broke: the
    # eval never reached a verdict, so it is not a fail.
    def broken(o: object) -> int:
        raise error

    assert harness.run(broken, []) == harness.CANNOT_RUN
    assert f"cannot run: {type(error).__name__}: {error}" in capsys.readouterr().err


def test_an_interrupt_is_not_swallowed() -> None:
    def stopped(o: object) -> int:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        harness.run(stopped, [])


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
        harness.RESULTS / "persona-gemma4-e4b-20261031T120000.000000Z.json"
    )
    # Two runs in the same second never share a file.
    later = AT.replace(microsecond=1)
    assert harness.results_path("persona", "m", later) != harness.results_path("persona", "m", AT)


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


def test_an_unjudged_report_says_reported_and_exits_0(tmp_path: Path) -> None:
    # Measured but held to no line: never PASS, never FAIL.
    out = io.StringIO()
    report = Report("drift", "fake-1", "judge: x", out=out, now=lambda: AT)
    report.case("off-01", None, "score=3", score=3)
    code = report.finish(None, "30 turns", path=tmp_path / "r.json")
    lines = out.getvalue().splitlines()
    assert lines[1].startswith(" -    off-01")
    assert lines[2] == "REPORTED (fake-1): 30 turns"
    assert code == harness.PASS
    body = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert body["verdict"] == "unjudged"
    assert body["cases"] == [{"id": "off-01", "passed": None, "score": 3}]


# ── The judge ────────────────────────────────────────────────────────────

JSON4 = '{"score": 4, "reason": "dry enough"}'


@pytest.mark.parametrize(
    "said",
    [
        JSON4,
        f"```json\n{JSON4}\n```",
        f"```JSON\n{JSON4}\n```",
        f"```\n{JSON4}\n```",
        f"Here is my grade:\n{JSON4}",
        f"Sure.\n```json\n{JSON4}\n```\nHope that helps.",
        '{"score": 4.0, "reason": "dry enough"}',
    ],
)
def test_a_judge_answer_is_read_from_json_even_in_a_fence(said: str) -> None:
    assert parse_score(said) == Score(value=4, reason="dry enough")


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
        '{"score": 4.5, "reason": "x"}',
        '{"score": 4, "reason": "x"} {"score": 2, "reason": "y"}',
        "{not json}",
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
    scripted = ScriptedProvider(say("no"), say("still no"), say(JSON4))
    with Judge(scripted, local=True) as judge:
        s = judge.score("rubric", "transcript")
    assert s.error and s.value is None and s.reason.startswith("judge error")
    assert len(scripted.calls) == 2  # asked once more, never a third time


def test_a_judge_that_fails_outright_is_a_judge_error() -> None:
    # No scripted replies: every call raises ProviderError("script_exhausted").
    scripted = ScriptedProvider()
    with Judge(scripted, local=False) as judge:
        s = judge.score("rubric", "transcript")
    assert s.error and "script_exhausted" in s.reason
    assert len(scripted.calls) == 2


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


def test_an_eval_never_touches_the_keychain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def touched(*args: Any, **kw: Any) -> None:
        raise AssertionError("the keychain was touched")

    for name in ("available", "get", "set", "delete"):
        monkeypatch.setattr(KeychainStore, name, touched)
    harness.model_provider("gemma4:e4b", LOCAL, {}, get=answers)()
    harness.judge_for("gemma4:e4b", LOCAL, {}, get=answers).close()
    harness.judge_for("gemma4:e4b", LOCAL, {ANTHROPIC_KEY: "test"}, get=refused).close()
    # Nor does her app in the sandbox, through a whole turn.
    with Sandbox(lambda: ScriptedProvider(say("Soup.")), root=tmp_path / "evals") as sb:
        assert sb.converse([Ask(text="lunch?")]).replies == ["Soup."]


# ── The sandbox ──────────────────────────────────────────────────────────


def sandbox(tmp_path: Path, scripted: ScriptedProvider) -> Sandbox:
    return Sandbox(lambda: scripted, root=tmp_path / "evals")


def test_a_typed_turn_is_answered_by_nova_for_sam(tmp_path: Path) -> None:
    before = os.environ.get("TRILLION_HOME")
    scripted = ScriptedProvider(say("Soup. It's cold out."))
    with sandbox(tmp_path, scripted) as sb:
        t = sb.converse([Ask(text="What should I have for lunch?")])
    (turn,) = t.turns
    assert (turn.said, turn.via, turn.reply, turn.error) == (
        "What should I have for lunch?", "text", "Soup. It's cold out.", None,
    )  # fmt: skip
    assert turn.tools == () and turn.cards == () and turn.tapped is None
    system = scripted.calls[0][0][0].text
    assert "Nova" in system and "Sam" in system  # her real prompt, with the names set
    assert "Never sound like this" in system  # the shipped SOUL.md
    assert t.render() == "Sam (typed): What should I have for lunch?\nNova: Soup. It's cold out."
    assert os.environ.get("TRILLION_HOME") == before  # put back on the way out


def test_a_card_is_tapped_and_what_she_says_after_is_kept(tmp_path: Path) -> None:
    scripted = ScriptedProvider()
    with sandbox(tmp_path, scripted) as sb:
        note = sb.app.granted / "note.txt"
        scripted._replies += [
            use("file_create", {"path": str(note), "content": "hi"}),
            say("Shall I?"),
            say("Done. It's there."),
        ]
        t = sb.converse([Ask(text="write a note", tap="confirm")])
        assert note.read_text(encoding="utf-8") == "hi"
    (turn,) = t.turns
    assert turn.tapped == "confirm" and "note.txt" in turn.cards[0]
    # The proposal; whether the approved run shows as a second call is the session's business.
    assert (turn.tools[0].name, turn.tools[0].summary) == ("file_create", "confirmation_required")
    assert turn.reply == "Shall I? Done. It's there."
    # What the host told her about the tap is part of what a reply is held to.
    assert '"status": "succeeded"' in t.evidence
    assert "[Sam tapped Approve]" in t.render()
    # The judge sees what the host told her after the tap, not only the proposal.
    (note,) = t.turns[0].notes
    assert note.startswith("[host] The user tapped Approve") and '"status": "succeeded"' in note
    assert "  [host] The user tapped Approve" in t.render()


def test_a_declined_card_runs_nothing(tmp_path: Path) -> None:
    scripted = ScriptedProvider()
    with sandbox(tmp_path, scripted) as sb:
        note = sb.app.granted / "note.txt"
        scripted._replies += [
            use("file_create", {"path": str(note), "content": "hi"}),
            say("Shall I?"),
            say("Left alone."),
        ]
        t = sb.converse([Ask(text="write a note", tap="cancel")])
        assert not note.exists()
    assert t.turns[0].tapped == "cancel" and t.turns[0].reply == "Shall I? Left alone."


def test_a_tool_result_is_what_she_was_given(tmp_path: Path) -> None:
    scripted = ScriptedProvider()
    with sandbox(tmp_path, scripted) as sb:
        (sb.app.granted / "plan.txt").write_text("ship it", encoding="utf-8")
        scripted._replies += [
            use("file_list", {"path": str(sb.app.granted)}),
            say("One file: plan.txt."),
        ]
        t = sb.converse([Ask(text="what's in my folder?")])
    (run,) = t.turns[0].tools
    assert (run.name, run.ok, run.summary) == ("file_list", True, "succeeded")
    assert "plan.txt" in run.result and "plan.txt" in t.evidence
    # What she called it with is kept too: it is how the board's contents are checked.
    assert json.loads(run.args)["path"].endswith("granted")


COFFEE = {"kind": "personal", "text": "I take my coffee black.", "moves": False, "said_by": "user"}


def test_fresh_starts_a_new_conversation_in_a_new_home(tmp_path: Path) -> None:
    made: list[ScriptedProvider] = []
    scripts = [
        ScriptedProvider(use("memory_write", COFFEE), say("Noted.")),
        ScriptedProvider(say("Two.")),
    ]

    def provider() -> ScriptedProvider:
        made.append(scripts[len(made)])
        return made[-1]

    with Sandbox(provider, root=tmp_path / "evals") as sb:
        first_t = sb.converse([Ask(text="I take my coffee black.")])
        (sb.app.granted / "left.txt").write_text("from case one", encoding="utf-8")
        assert first_t.turns[0].tools[0].ok and sb.app.rt.memory.count() == 1
        first, first_home = sb.app, os.environ["TRILLION_HOME"]
        sb.fresh()
        assert sb.app is not first and os.environ["TRILLION_HOME"] != first_home
        # Nothing the first case left reaches the second: no memory, no file.
        assert sb.app.rt.memory.count() == 0
        assert list(sb.app.granted.iterdir()) == []
        t = sb.converse([Ask(text="second")])
    assert t.replies == ["Two."]
    # A model of its own per conversation, and the new one carries no history.
    assert len(made) == 2 and made[0] is not made[1]
    assert len(made[1].calls) == 1 and len(made[1].calls[0][1]) == 1


def test_two_sandboxes_can_share_a_root(tmp_path: Path) -> None:
    for _ in range(2):
        with Sandbox(lambda: ScriptedProvider(say("Hi.")), root=tmp_path / "evals") as sb:
            assert sb.converse([Ask(text="hi")]).replies == ["Hi."]


def test_a_sandbox_never_opened_deletes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "theirs"))
    (tmp_path / "keep.txt").write_text("mine", encoding="utf-8")
    sb = Sandbox(lambda: ScriptedProvider())
    sb.__exit__(None, None, None)
    assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "mine"
    assert os.environ["TRILLION_HOME"] == str(tmp_path / "theirs")


def test_a_sandbox_removes_only_the_home_it_made(tmp_path: Path) -> None:
    with Sandbox(lambda: ScriptedProvider(say("Hi."))) as sb:
        made = sb.root
        assert made.exists() and made.name.startswith("trillion-eval-")
    assert not made.exists()
    sb.__exit__(None, None, None)  # a second close is harmless


def test_no_system_notification_ever_leaves_the_sandbox(tmp_path: Path) -> None:
    with Sandbox(lambda: ScriptedProvider(), root=tmp_path / "evals") as sb:
        door = sb.app.rt.door
        assert door is not None and door.notify("title", "body", silent=True) is False
        sb.fresh()
        door = sb.app.rt.door
        assert door is not None and door.notify("title", "body") is False


def test_a_failed_close_cannot_hang_a_hand_run() -> None:
    # The same patch as tests/conftest.py, for runs outside pytest: aiosqlite's worker
    # thread is a daemon, so a connection left open never keeps the process alive.
    harness.daemon_connection_threads()
    harness.daemon_connection_threads()  # once is enough; twice changes nothing
    init = aiosqlite.core.Connection.__init__
    assert getattr(init, harness.PATCHED, False)
    assert not getattr(getattr(init, "__wrapped__", None), harness.PATCHED, False)


def test_a_spoken_turn_goes_through_the_recognizer_and_is_marked_spoken(tmp_path: Path) -> None:
    scripted = ScriptedProvider(say("Ten past four."))
    with sandbox(tmp_path, scripted) as sb:
        t = sb.converse([Ask(text="what time is it", via="voice")])
    (turn,) = t.turns
    assert (turn.said, turn.via, turn.reply, turn.error) == (
        "what time is it", "voice", "Ten past four.", None,
    )  # fmt: skip
    context = scripted.calls[0][1][-1].blocks[0]
    # voice.md rides on the spoken message: one or two sentences, no markdown, no dashes.
    assert isinstance(context, ContextBlock) and "THIS MESSAGE WAS SPOKEN" in context.text
    assert t.render().startswith("Sam (spoken): what time is it")


def test_typed_and_spoken_turns_mix_in_one_conversation(tmp_path: Path) -> None:
    scripted = ScriptedProvider(say("Hi."), say("Now."), say("Typed again."))
    with sandbox(tmp_path, scripted) as sb:
        t = sb.converse([Ask(text="hi"), Ask(text="and now", via="voice"), Ask(text="and typing")])
    assert t.replies == ["Hi.", "Now.", "Typed again."]
    assert [turn.via for turn in t.turns] == ["text", "voice", "text"]
    spoken = [m.blocks[0] for m in scripted.calls[2][1] if m.role == "user"]
    assert ["THIS MESSAGE WAS SPOKEN" in b.text for b in spoken] == [False, True, False]


def test_a_model_call_from_elsewhere_never_replaces_the_turns_evidence(tmp_path: Path) -> None:
    scripted = ScriptedProvider()
    with sandbox(tmp_path, scripted) as sb:
        (sb.app.granted / "plan.txt").write_text("ship it", encoding="utf-8")
        scripted._replies += [
            use("file_list", {"path": str(sb.app.granted)}),
            say("One file: plan.txt."),
            say("Sunny, 18 degrees."),
        ]
        sb.converse([Ask(text="what's in my folder?")])

        # The attention runner, the scheduler and the widget refresher share her model:
        # a call of theirs is the most recent one, but it is not this conversation.
        async def elsewhere() -> None:
            async for _ in sb.provider.stream(
                system=[SystemBlock(text="Refresh the weather widget.")],
                messages=[Msg(role="user", blocks=(TextBlock(text="weather?"),))],
                tools=[],
                max_tokens=100,
            ):
                pass

        asyncio.run(elsewhere())
        assert "plan.txt" in sb.provider.evidence()
        assert any("plan.txt" in r for r in sb.provider.results().values())


FAST, SLOW = "93.184.216.41", "93.184.216.42"


def test_every_background_read_is_waited_for_however_many_turns_bring_them_in(
    tmp_path: Path,
) -> None:
    async def resolve(host: str, port: int) -> list[str]:
        if host == "slow.example":
            await asyncio.sleep(1.0)  # ends well after the first read has been answered
            return [SLOW]
        return [FAST]

    scripted = ScriptedProvider(
        use("web_fetch", {"url": "https://fast.example/"}, "c1"),
        use("web_fetch", {"url": "https://slow.example/"}, "c2"),
        say("Reading both."),
        say("The fast one is about soup."),
        say("The slow one is about bread."),
    )
    with sandbox(tmp_path, scripted) as sb:
        assert sb.app.rt.web is not None
        wire = Wire(
            response("Soup is good.", kind="text/plain"),
            response("Bread is good.", kind="text/plain"),
        )
        sb.app.rt.web.use(resolve, wire)
        t = sb.converse([Ask(text="Read https://fast.example/ and https://slow.example/")])
    (turn,) = t.turns
    assert [r.summary for r in turn.tools] == ["background", "background"]
    # Two follow-ups, one for each read as it ended: neither is lost.
    assert turn.reply == ("Reading both. The fast one is about soup. The slow one is about bread.")
    assert wire.opened == [FAST, SLOW]
    assert "Soup is good." in t.evidence and "Bread is good." in t.evidence


def test_what_she_says_across_a_tool_call_is_kept_as_separate_sentences(tmp_path: Path) -> None:
    scripted = ScriptedProvider()
    with sandbox(tmp_path, scripted) as sb:
        scripted._replies += [
            use("file_list", {"path": str(sb.app.granted)}, text="Looking now"),
            say("Sent."),
        ]
        t = sb.converse([Ask(text="what's in my folder?")])
    # Never "Looking nowSent." or "Looking now Sent.": two sentences, as she said them.
    assert t.turns[0].reply == "Looking now. Sent."


def test_the_judge_sees_how_a_background_read_ended(tmp_path: Path) -> None:
    scripted = ScriptedProvider(
        use("web_fetch", {"url": "https://fast.example/"}), say("Reading it now."), say("A 404.")
    )
    with sandbox(tmp_path, scripted) as sb:
        assert sb.app.rt.web is not None
        sb.app.rt.web.use(names({"fast.example": [FAST]}), Wire(response("gone", status=404)))
        t = sb.converse([Ask(text="Read https://fast.example/")])
    (note,) = t.turns[0].notes
    assert note.startswith("[host]") and "404" in note
    shown = t.render()
    assert "[tool web_fetch, background]" in shown and "  [host]" in shown
    assert shown.index("  [host]") < shown.index("Nova: Reading it now. A 404.")


def test_a_follow_up_that_never_comes_fails_the_turn_not_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.api import test_server

    async def never(host: str, port: int) -> list[str]:
        await asyncio.sleep(3600)
        return [FAST]

    monkeypatch.setattr(test_server, "RECEIVE_TIMEOUT_S", 1.0)
    scripted = ScriptedProvider(
        use("web_fetch", {"url": "https://fast.example/"}), say("Reading it now."), say("Next.")
    )
    with sandbox(tmp_path, scripted) as sb:
        assert sb.app.rt.web is not None
        sb.app.rt.web.use(never, Wire())
        t = sb.converse([Ask(text="Read https://fast.example/")])
        (turn,) = t.turns
        assert turn.error == "no follow-up" and turn.reply == "Reading it now."
        # The run carries on with the next case.
        sb.fresh()
        assert sb.converse([Ask(text="and now?")]).replies == ["Next."]


def test_settle_waits_for_background_reads_and_gives_up_at_its_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.api import test_server

    async def never(host: str, port: int) -> list[str]:
        await asyncio.sleep(3600)
        return [FAST]

    monkeypatch.setattr(test_server, "RECEIVE_TIMEOUT_S", 1.0)
    scripted = ScriptedProvider(
        use("web_fetch", {"url": "https://fast.example/"}), say("Reading it now.")
    )
    with sandbox(tmp_path, scripted) as sb:
        assert sb.settle(0.1)  # nothing running
        assert sb.app.rt.web is not None
        sb.app.rt.web.use(never, Wire())
        sb.converse([Ask(text="Read https://fast.example/")])
        assert not sb.settle(0.2)  # still reading: bounded, never forever


def test_a_spoken_ask_with_stray_spaces_keeps_its_evidence(tmp_path: Path) -> None:
    scripted = ScriptedProvider()
    with sandbox(tmp_path, scripted) as sb:
        (sb.app.granted / "plan.txt").write_text("ship it", encoding="utf-8")
        scripted._replies += [
            use("file_list", {"path": str(sb.app.granted)}),
            say("One file: plan.txt."),
        ]
        t = sb.converse([Ask(text="  What's in   my folder?  ", via="voice")])
    assert "plan.txt" in t.evidence and "plan.txt" in t.turns[0].tools[0].result


def test_a_late_follow_up_from_the_last_ask_never_stands_in_for_this_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.api import test_server

    async def resolve(host: str, port: int) -> list[str]:
        # The slow read ends after the first ask gave up on it but before the fast one.
        await asyncio.sleep(1.5 if host == "slow.example" else 1.0)
        return [SLOW if host == "slow.example" else FAST]

    monkeypatch.setattr(test_server, "RECEIVE_TIMEOUT_S", 1.0)
    scripted = ScriptedProvider(
        use("web_fetch", {"url": "https://slow.example/"}, "c1"),
        say("Reading the slow one."),
        use("web_fetch", {"url": "https://fast.example/"}, "c2"),
        say("Reading the fast one."),
        say("The slow one is about bread."),
        say("The fast one is about soup."),
    )
    with sandbox(tmp_path, scripted) as sb:
        assert sb.app.rt.web is not None
        wire = Wire(
            response("Bread is good.", kind="text/plain"),
            response("Soup is good.", kind="text/plain"),
        )
        sb.app.rt.web.use(resolve, wire)
        t = sb.converse(
            [
                Ask(text="Read https://slow.example/ and https://fast.example/"),
                Ask(text="And the fast one?"),
            ]
        )
    first, second = t.turns
    assert first.error == "no follow-up"
    # The slow read's late note is not the fast read's: she waits for that one too.
    assert second.reply == (
        "Reading the fast one. The slow one is about bread. The fast one is about soup."
    )
    assert second.error is None


def test_settle_waits_for_a_follow_up_turn_a_read_started(tmp_path: Path) -> None:
    async def resolve(host: str, port: int) -> list[str]:
        return [FAST]

    scripted = ScriptedProvider(
        use("web_fetch", {"url": "https://fast.example/"}), say("Reading it now."), say("Soup.")
    )
    with sandbox(tmp_path, scripted) as sb:
        assert sb.app.rt.web is not None
        sb.app.rt.web.use(resolve, Wire(response("Soup is good.", kind="text/plain")))
        chat = sb._conversation()
        sb.provider.said.append("Read https://fast.example/")
        chat.say("Read https://fast.example/")  # not waiting for the follow-up
        assert sb.settle(10.0)
        # Settled means her follow-up turn is over too, not only the read.
        assert not any(s.busy() for s in sb.app.rt.by_conversation.values())
        assert sb.provider.reads_ended() == 1


def test_safe_name_keeps_a_model_name_usable_as_a_file_name() -> None:
    assert safe_name("gemma4:e4b") == "gemma4-e4b"
    assert safe_name("claude-opus-5-5") == "claude-opus-5-5"
    assert safe_name("///") == "model"


def test_the_sandbox_collects_every_model_that_answered(tmp_path: Path) -> None:
    def answered(model: str) -> list[ModelEvent]:
        return [TextDelta(text="Hi."), Done(stop_reason="end", usage=Usage(), model=model)]

    scripted = ScriptedProvider(answered("m-1"), answered("m-2"))
    with Sandbox(lambda: scripted, root=tmp_path / "e") as sb:
        sb.converse([Ask(text="one")])
        sb.fresh()  # a new app keeps counting
        sb.converse([Ask(text="two")])
        assert sb.served == {"m-1", "m-2"}
        assert sb.unreported == 0


def test_the_sandbox_counts_replies_that_named_no_model(tmp_path: Path) -> None:
    scripted = ScriptedProvider(say("Hi."), say("Hello."))
    with Sandbox(lambda: scripted, root=tmp_path / "e") as sb:
        sb.converse([Ask(text="one")])
        sb.fresh()  # a new app keeps counting
        sb.converse([Ask(text="two")])
        assert sb.served == set() and sb.unreported == 2


def test_the_judge_is_never_counted_as_a_model_that_answered(tmp_path: Path) -> None:
    def answered(model: str, text: str) -> list[ModelEvent]:
        return [TextDelta(text=text), Done(stop_reason="end", usage=Usage(), model=model)]

    scripted = ScriptedProvider(answered("m-1", "Soup."))
    judging = ScriptedProvider(answered("judge-9", '{"score": 4, "reason": "fine"}'))
    with (
        Sandbox(lambda: scripted, root=tmp_path / "e") as sb,
        Judge(judging, local=True) as judge,
    ):
        sb.converse([Ask(text="lunch?")])
        assert judge.score("rubric", "transcript").value == 4
        assert sb.served == {"m-1"} and sb.unreported == 0


def test_an_eval_can_add_its_own_options() -> None:
    def extra(p: argparse.ArgumentParser) -> None:
        p.add_argument("--deployed", action="store_true")

    opts = options("about", ["--deployed"], extra=extra)
    assert opts.deployed and opts.model == "gemma4:e4b"
