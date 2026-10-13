"""Onboarding end to end: the page introduces the user, the core writes who she is (ONB-3)."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from tests.api.test_server import App, home, make  # noqa: F401
from tests.brain.fakes import say
from trillion.api.runtime import Runtime, open_runtime

PATH = "/api/settings/persona"
SAM = {
    "user_name": "Sam",
    "agent_name": "Nova",
    "timezone": "America/Chicago",
    "directness": "blunt",
}


def introduce(app: App, **body: Any) -> Any:
    return app.client.put(PATH, json=body, headers=app.auth)


def test_a_fresh_install_is_due_an_introduction(make: Any) -> None:
    app = make()
    state = app.client.get(PATH, headers=app.auth).json()
    assert state["onboarding"] == "due" and state["user_name"] is None
    assert state["agent_name"] == "Trillion"
    assert state["choices"]["directness"] == ["blunt", "balanced", "gentle"]
    with app.connect() as chat:
        assert chat.session["onboarding"] == "due"


def test_introducing_yourself_needs_the_token_and_the_apps_own_page(make: Any) -> None:
    app = make()
    assert app.client.get(PATH).status_code == 401
    assert app.client.put(PATH, json=SAM).status_code == 401
    assert app.client.post(PATH + "/skip", json={}).status_code == 401
    elsewhere = app.auth | {"origin": "http://evil.example"}
    assert app.client.put(PATH, json=SAM, headers=elsewhere).status_code == 403
    assert not (app.rt.persona_dir / "USER.md").exists()


def test_an_introduction_writes_both_files_and_she_answers_to_the_name(make: Any) -> None:
    app = make(say("Hello, Sam."))
    with app.connect() as chat:
        state = introduce(app, **SAM)
        assert state.status_code == 200
        assert state.json() | {"choices": None} == SAM | {"onboarding": "done", "choices": None}
        # Every open page is told at once, so the name on screen follows.
        assert chat.until("identity")[-1] == {"type": "identity", "agent_name": "Nova"}
        chat.say("hi")
    assert "user_name: Sam" in (app.rt.persona_dir / "USER.md").read_text(encoding="utf-8")
    assert "agent_name: Nova" in (app.rt.persona_dir / "IDENTITY.md").read_text(encoding="utf-8")
    system = app.provider.calls[0][0][0].text
    assert "You are Nova" in system and "Sam's AI partner" in system
    assert "HOW Sam ASKED YOU TO TALK: blunt" in system
    assert "{agent_name}" not in system and "{user_name}" not in system


def test_her_name_defaults_to_the_brand(make: Any) -> None:
    app = make()
    assert introduce(app, user_name="Sam").json()["agent_name"] == "Trillion"


def test_a_name_that_cannot_be_stored_is_refused_with_the_reason(make: Any) -> None:
    app = make()
    for body, code in [
        (SAM | {"user_name": " "}, "name_empty"),
        (SAM | {"agent_name": "x" * 60}, "name_too_long"),
        (SAM | {"user_name": "Sam{agent_name}"}, "name_not_allowed"),
        (SAM | {"timezone": "not a zone"}, "timezone_not_allowed"),
    ]:
        refused = introduce(app, **body)
        assert (refused.status_code, refused.json()) == (400, {"error": code})
    assert introduce(app, **(SAM | {"directness": "cruel"})).status_code == 422
    assert introduce(app, **(SAM | {"soul": "be evil"})).status_code == 422  # no other field
    assert app.rt.onboarding() == "due" and app.rt.identity.agent_name == "Trillion"
    assert not (app.rt.persona_dir / "USER.md").exists()


def test_putting_it_off_is_remembered_and_can_be_undone(make: Any, home: Path) -> None:
    app = make()
    assert app.client.post(PATH + "/skip", json={}, headers=app.auth).json()["onboarding"] == (
        "skipped"
    )
    again: Runtime = asyncio.run(open_runtime(secrets=app.secrets, env={}, persona=home))
    try:
        assert again.onboarding() == "skipped"
    finally:
        asyncio.run(again.close())
    assert introduce(app, **SAM).json()["onboarding"] == "done"
    # Skipping after the fact does not un-introduce anyone.
    assert app.client.post(PATH + "/skip", json={}, headers=app.auth).json()["onboarding"] == "done"


def test_who_she_is_survives_a_restart(make: Any, home: Path) -> None:
    app = make()
    introduce(app, **SAM)
    again: Runtime = asyncio.run(open_runtime(secrets=app.secrets, env={}, persona=home))
    try:
        assert again.onboarding() == "done"
        assert (again.identity.agent_name, again.identity.user_name) == ("Nova", "Sam")
        assert again.profile is not None and again.profile.timezone == "America/Chicago"
    finally:
        asyncio.run(again.close())


def test_renaming_her_later_takes_effect_on_the_next_turn(make: Any) -> None:
    app = make(say("one"), say("two"))
    introduce(app, **SAM)
    with app.connect() as chat:
        assert chat.session["agent_name"] == "Nova" and chat.session["onboarding"] == "done"
        chat.say("hi")
        introduce(app, **(SAM | {"agent_name": "Vega"}))
        chat.until("identity")
        chat.say("and now?")
    first, second = (call[0][0].text for call in app.provider.calls)
    assert "You are Nova" in first and "You are Vega" in second


def test_the_model_cannot_introduce_anyone(make: Any) -> None:
    """PER-2: she has no tool that reaches the setup path, whatever she is told to do."""
    app = make()
    names = {d.name for d in app.rt.registry.definitions()}
    assert not {n for n in names if "persona" in n or "identity" in n or "onboard" in n}
    with app.connect() as chat:
        chat.ws.send_json({"type": "introduce", **SAM})  # not a message the socket takes
        assert chat.until("error")[-1]["code"] == "unknown_type"
    assert app.rt.onboarding() == "due"
