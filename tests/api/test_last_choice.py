"""Switching her voice or ears off keeps what was chosen, so switching back on is one step."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_server import SAMPLE_KEY, App, home, listening, make, speech  # noqa: F401
from trillion.api.runtime import Runtime, open_runtime
from trillion.voice.providers.deepgram import Deepgram
from trillion.voice.types import Choice


def _speech(app: App) -> dict[str, Any]:
    return app.client.get("/api/settings/speech", headers=app.auth).json()


def _listening(app: App) -> dict[str, Any]:
    return app.client.get("/api/settings/listening", headers=app.auth).json()


def test_turning_her_voice_off_keeps_the_voice_that_was_chosen(make: Any, home: Path) -> None:
    app = make(model=False)
    assert _speech(app)["last"] is None
    chosen = {"kind": "system", "voice": "Samantha", "base_url": None}
    assert speech(app, kind="system", voice="Samantha").json()["last"] == chosen
    off = speech(app, kind=None).json()
    assert off["saved"] is None and off["active"] is False  # she is silent
    assert off["last"] == chosen  # and the choice is still there to switch back to

    again: Runtime = asyncio.run(open_runtime(secrets=app.secrets, env={}, persona=home))
    try:
        assert again.speech is None and again.tts is None  # off stays off across a restart
        assert again.speech_last is not None and again.speech_last.voice == "Samantha"
    finally:
        asyncio.run(again.close())


def test_a_voice_chosen_before_this_was_remembered_is_kept_when_switched_off(make: Any) -> None:
    app = make(model=False)
    speech(app, kind="system", voice="Samantha")
    app.rt.speech_last = None  # as an install from before the last choice was stored
    assert speech(app, kind=None).json()["last"]["voice"] == "Samantha"


def test_turning_her_ears_off_keeps_what_was_chosen(
    make: Any, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def choices(self: Deepgram) -> list[Choice]:
        return [Choice(id="en", name="English")]

    monkeypatch.setattr(Deepgram, "choices", choices)
    app = make(model=False)
    assert _listening(app)["last"] is None
    saved = listening(app, kind="deepgram", choice="en", api_key=SAMPLE_KEY)
    assert saved.status_code == 200
    off = listening(app, kind=None).json()
    assert off["saved"] is None and off["active"] is False
    assert off["last"] == {"kind": "deepgram", "choice": "en"}
    assert off["options"][1]["key"]["saved"] is True  # the key was never touched

    again: Runtime = asyncio.run(open_runtime(secrets=app.secrets, env={}, persona=home))
    try:
        assert again.listening is None and again.listening_last is not None
        assert again.listening_last.choice == "en"
    finally:
        asyncio.run(again.close())
