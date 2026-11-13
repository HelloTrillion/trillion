"""The Seamless measures through the app: recorded as turns happen, read by
`GET /api/seamless` behind the session token, and never holding anything anyone said.
"""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

from typing import Any

import pytest

from tests.api.test_server import App, home, make, only, propose  # noqa: F401
from tests.brain.fakes import say, use
from trillion.api import seamless, trace
from trillion.api.trace import Trace

PATH = "/api/seamless"
ASKED = "/api/seamless/asked"
# A real moment, 2027: a time near 1970 is not one a clock gives, and Windows refuses
# to turn one into a local date.
NOW = 1_800_000_000_000


def settle(app: App) -> None:
    """Recording goes on beside a turn: wait for it before reading what was kept."""
    portal = app.client.portal
    assert portal is not None
    portal.call(seamless.settle)


def get(app: App) -> dict[str, Any]:
    settle(app)
    r = app.client.get(PATH, headers=app.auth)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def test_it_needs_the_session_token(make: Any) -> None:
    app: App = make()
    wrong = {"authorization": "Bearer nope"}
    for headers in ({}, wrong):
        assert app.client.get(PATH, headers=headers).status_code == 401
        assert app.client.post(ASKED, json={}, headers=headers).status_code == 401
    assert app.rows("SELECT count(*) FROM seamless_first_run") == [(0,)]


def test_with_nothing_recorded_it_says_so(make: Any) -> None:
    app: App = make()
    body = get(app)
    assert body["first_run"] is None and body["tool_turns"] == {
        "days": 28,
        "turns": 0,
        "in_time": 0,
    }
    assert body["asks"] == [
        {"days": 7, "shown": 0, "quick": 0},
        {"days": 28, "shown": 0, "quick": 0},
    ]


def test_the_first_run_is_timed_from_opening_to_her_first_reply(make: Any) -> None:
    app: App = make(say("Hello."), say("Again."))
    now = [NOW]
    app.rt.clock = lambda: now[0]
    with app.connect() as chat:
        # No persona yet, so onboarding is due: opening the page begins the first run.
        assert chat.session["onboarding"] == "due"
        # The page reports two onboarding steps that asked something.
        assert app.client.post(ASKED, json={}, headers=app.auth).status_code == 204
        assert app.client.post(ASKED, json={}, headers=app.auth).status_code == 204
        now[0] = NOW + 25_000
        chat.say("hi")
        now[0] = NOW + 90_000
        chat.say("and again")
        # Asked after her first reply: not a question before it.
        assert app.client.post(ASKED, json={}, headers=app.auth).status_code == 204
    settle(app)
    assert app.rows(
        "SELECT opened_at, model_ready, questions, first_reply_at FROM seamless_first_run"
    ) == [(NOW, 1, 2, NOW + 25_000)]
    first = get(app)["first_run"]
    assert first["questions"] == 2 and first["model_ready"] is True and first["seconds"] == 25.0


def test_with_no_model_the_first_run_says_none_was_ready(make: Any) -> None:
    app: App = make(model=False)
    with app.connect():
        pass
    settle(app)
    assert app.rows("SELECT model_ready, first_reply_at FROM seamless_first_run") == [(0, None)]


def test_asks_come_from_the_decision_record(make: Any) -> None:
    app: App = make()
    assert app.provider is not None
    app.provider._replies += [propose(app), say("Shall I?"), say("Done.")]
    with app.connect() as chat:
        card = only(chat.say("write a note"), "confirm.request")
        assert chat.tap("confirm", card["pending_id"])["status"] == "succeeded"
        chat.until("turn.complete")
    week, month = get(app)["asks"]
    # Shown, then tapped within moments: one ask, approved quickly with nothing changed.
    assert week == {"days": 7, "shown": 1, "quick": 1}
    assert month == {"days": 28, "shown": 1, "quick": 1}


def test_a_slow_tool_turn_is_kept_as_numbers_and_never_as_words(
    make: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every tool counts as slow here, so a quick read stands in for a slow one.
    monkeypatch.setattr(trace, "SLOW_TOOL_S", -1.0)
    app: App = make()
    target = app.granted / "pineapple.txt"
    target.write_text("marmalade", encoding="utf-8")
    assert app.provider is not None
    app.provider._replies += [
        use("file_read", {"path": str(target)}),
        say("The jar says marmalade, Bartholomew."),
    ]
    with app.connect() as chat:
        chat.say("Bartholomew here: what does pineapple.txt say?")
    settle(app)
    rows = app.rows("SELECT * FROM seamless_tool_turns")
    ((_, owner, at, voiced, in_time),) = rows
    # Her voice was off: kept, but not one of the turns the figure counts.
    assert owner == "local" and isinstance(at, int) and voiced == 0 and in_time == 0
    assert get(app)["tool_turns"]["turns"] == 0
    # Nothing anyone said, no tool, no argument and no name is in either table.
    kept = rows + app.rows("SELECT * FROM seamless_first_run")
    assert kept and all(v is None or isinstance(v, int) for r in kept for v in r[2:])
    for word in ("pineapple", "marmalade", "Bartholomew", "file_read", "jar"):
        assert word not in repr(kept)


def test_a_quick_tool_turn_is_not_kept(make: Any) -> None:
    app: App = make()
    target = app.granted / "note.txt"
    target.write_text("hi", encoding="utf-8")
    assert app.provider is not None
    app.provider._replies += [use("file_read", {"path": str(target)}), say("It says hi.")]
    with app.connect() as chat:
        chat.say("read it")
    settle(app)
    assert app.rows("SELECT count(*) FROM seamless_tool_turns") == [(0,)]


def test_a_failure_to_record_never_fails_the_turn(make: Any) -> None:
    app: App = make(say("Still here."))

    class Broken:
        async def opened(self, *_: Any, **__: Any) -> None:
            raise RuntimeError("disk on fire")

        replied = asked = tool_turn = opened

    app.rt.measures = Broken()  # type: ignore[assignment]
    with app.connect() as chat:
        msgs = chat.say("hello")
    assert only(msgs, "turn.complete")["stop"] == "end"
    settle(app)
    assert app.client.post(ASKED, json={}, headers=app.auth).status_code == 204


# ── Whether she was heard in time, with fixed clocks ──────────────────────


def test_words_before_two_seconds_into_a_slow_tool_are_in_time() -> None:
    t = Trace(via="voice", t0=100.0)
    t.mark("played", 101.0)
    t.tool_started("a", 100.5)
    t.tool_ended("a", 103.0)
    assert t.spoke_in_time() is True


def test_silence_through_the_first_two_seconds_is_not() -> None:
    t = Trace(via="voice", t0=100.0)
    t.tool_started("a", 100.0)
    t.tool_ended("a", 104.0)
    # First heard at 2.5 s into the tool, after it had run past the mark.
    t.mark("played", 102.5)
    assert t.spoke_in_time() is False
    # Nothing heard at all.
    quiet = Trace(via="voice", t0=0.0)
    quiet.tool_started("a", 0.0)
    quiet.tool_ended("a", 3.0)
    assert quiet.spoke_in_time() is False


def test_the_first_audio_stands_in_when_the_page_never_said() -> None:
    t = Trace(via="voice", t0=0.0)
    t.mark("first_audio", 1.0)
    t.tool_started("a", 0.0)
    t.tool_ended("a", 5.0)
    assert t.spoke_in_time() is True


def test_the_earliest_slow_tool_counts_and_quick_ones_none() -> None:
    t = Trace(via="voice", t0=0.0)
    t.tool_started("quick", 0.0)
    t.tool_ended("quick", 2.0)  # exactly two seconds is not over two
    assert t.spoke_in_time() is None
    t.tool_started("slow", 10.0)
    t.tool_started("later", 20.0)
    t.tool_ended("later", 30.0)
    t.tool_ended("slow", 12.5)
    # The earliest to start of those over two seconds is the one measured.
    assert t.slow_tool == 10.0
    t.tool_ended("never-started", 40.0)
    assert t.slow_tool == 10.0
