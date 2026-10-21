"""Every turn is timed as it happens, and the timing is kept for `doctor --trace` (ONB-11)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_listening import hearing, speak
from tests.api.test_session import Gated, Page, idle
from tests.brain.fakes import ScriptedProvider, say, use
from tests.voice.audio import room, tone
from tests.voice.test_speaker import FakeVoice
from trillion import paths
from trillion.api.runtime import Runtime, SpeechSettings, open_runtime
from trillion.api.session import Session
from trillion.api.trace import MAX_AHEAD_MS, ahead_ms
from trillion.secrets.store import MemoryStore
from trillion.storage import inspect


@pytest.fixture
async def rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    granted = tmp_path / "granted"
    granted.mkdir()
    runtime = await open_runtime(
        granted_roots=(granted,), persona=tmp_path, secrets=MemoryStore(), env={}
    )
    yield runtime
    await runtime.close()


def timings() -> list[dict[str, Any]]:
    return inspect.timings(paths.db_path(), "local", 50)


def voiced(rt: Runtime) -> None:
    rt.tts, rt.speech = FakeVoice(), SpeechSettings(kind="elevenlabs", voice="v1")


async def test_a_typed_turn_is_timed_from_the_moment_it_arrived(rt: Runtime) -> None:
    rt.provider = ScriptedProvider(say("Hello there."), say("Again."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "hi"})
    await idle(session)
    await session.handle({"type": "turn.text", "text": "and again"})
    await idle(session)
    await session.close()

    first, second = timings()
    assert first["via"] == "text" and first["stop"] == "end" and first["tools"] == 0
    assert first["first_in_session"] == 1 and second["first_in_session"] == 0
    assert first["provider"] == rt.provider.name and first["voice"] is None
    assert first["heard_ms"] is None and first["endpoint_ms"] is None  # nothing to hear
    assert 0 <= first["first_text_ms"] <= first["saved_ms"]
    assert first["first_audio_ms"] is None and first["played_ms"] is None  # no voice
    (call,) = first["calls"]
    assert call["input"] == 10 and call["output"] == 5
    assert call["start_ms"] <= call["first_token_ms"] <= call["end_ms"] <= first["saved_ms"]


async def test_nothing_anyone_said_is_kept_in_a_timing(rt: Runtime) -> None:
    rt.provider = ScriptedProvider(say("The secret word is marmalade."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "remember pineapple"})
    await idle(session)
    await session.close()
    (row,) = timings()
    assert "marmalade" not in str(row) and "pineapple" not in str(row)


async def test_a_spoken_turn_starts_when_they_stopped_talking(rt: Runtime) -> None:
    stt = hearing(rt, say("I heard you."))
    voiced(rt)
    page = Page(rt)
    audio: list[bytes] = []

    async def send_bytes(data: bytes) -> None:
        audio.append(data)

    session = await Session.start(rt, page.send, None, send_bytes)
    await session.handle({"type": "listen.start"})
    await speak(session, room(0.2) + tone(0.6))
    stt.stream.say("can you hear me", final=True)
    await speak(session, room(0.6))
    await idle(session)
    await session.close()

    (row,) = timings()
    assert row["via"] == "voice" and row["ears"] == "deepgram" and row["voice"] == "elevenlabs"
    # The quiet waited for before deciding they were done is counted, not hidden.
    assert 300 <= row["endpoint_ms"] <= 1000
    assert row["heard_ms"] >= row["endpoint_ms"]
    assert row["heard_ms"] <= row["first_text_ms"] <= row["first_sentence_ms"]
    assert row["first_sentence_ms"] <= row["first_audio_ms"] <= row["saved_ms"]
    assert audio


async def test_the_page_says_when_her_voice_was_heard(rt: Runtime) -> None:
    rt.provider = ScriptedProvider(say("One."), say("Two."))
    voiced(rt)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)

    await session.handle({"type": "turn.text", "text": "first"})
    await page.until("reply.sentence")
    turn = page.all("reply.sentence")[0]["turn"]
    # Heard while the turn was still running...
    await session.handle({"type": "playback.started", "turn_id": turn, "in_ms": 80})
    await idle(session)
    # ...and only the first report counts.
    await session.handle({"type": "playback.started", "turn_id": turn, "in_ms": 4000})

    await session.handle({"type": "turn.text", "text": "second"})
    await idle(session)
    second_turn = page.all("reply.sentence")[-1]["turn"]
    # Heard after the turn was saved: the row is completed afterwards.
    await session.handle({"type": "playback.started", "turn_id": second_turn, "in_ms": 50})
    await session.close()

    first, second = timings()
    assert first["played_ms"] is not None and first["played_ms"] < 4000
    assert first["played_ms"] >= 80
    assert second["played_ms"] is not None and second["played_ms"] >= 50


async def test_a_report_for_another_turn_or_of_nonsense_is_ignored(rt: Runtime) -> None:
    rt.provider = ScriptedProvider(say("One."))
    voiced(rt)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "first"})
    await idle(session)
    for bad in (99, "1", True, None):
        await session.handle({"type": "playback.started", "turn_id": bad, "in_ms": 10})
    assert not page.all("error")
    await session.close()
    (row,) = timings()
    assert row["played_ms"] is None


def test_how_far_ahead_is_made_safe() -> None:
    assert ahead_ms(120) == 120
    assert ahead_ms(-5) == 0 and ahead_ms(10**9) == MAX_AHEAD_MS
    for nonsense in (float("nan"), float("inf"), "80", True, None):
        assert ahead_ms(nonsense) == 0


async def test_tools_and_each_model_call_are_counted(rt: Runtime) -> None:
    target = rt.granted_roots[0] / "note.txt"
    target.write_text("hi", encoding="utf-8")
    rt.provider = ScriptedProvider(use("file_read", {"path": str(target)}), say("It says hi."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "read it"})
    await idle(session)
    await session.close()
    (row,) = timings()
    assert row["tools"] == 1 and len(row["calls"]) == 2
    first, second = row["calls"]
    assert first["end_ms"] <= second["start_ms"]


async def test_an_interrupted_turn_is_kept_as_interrupted(rt: Runtime) -> None:
    provider = Gated(say("never finished"))
    held = provider.hold(0)
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "go"})
    await asyncio.sleep(0.05)
    await session.handle({"type": "interrupt"})
    await idle(session)
    held.set()
    await session.close()
    (row,) = timings()
    assert row["stop"] == "interrupted" and row["first_text_ms"] is None
    assert row["saved_ms"] is not None


async def test_a_turn_a_tap_starts_is_timed_from_the_tap(rt: Runtime) -> None:
    path = str(rt.granted_roots[0] / "a.txt")
    rt.provider = Gated(
        use("file_create", {"path": path, "content": "x"}, "c1"), say("Shall I?"), say("Done.")
    )
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "write it"})
    await page.until("confirm.request")
    await idle(session)
    await session.handle(
        {"type": "confirm", "pending_id": page.all("confirm.request")[0]["pending_id"]}
    )
    await idle(session)
    await session.close()
    typed, tapped = timings()
    assert typed["via"] == "text" and tapped["via"] == "tap"
    assert tapped["first_text_ms"] is not None
