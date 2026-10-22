"""Voice barge-in (VOI-9): the user talks over her, she stops and listens."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.api.test_listening import hearing, speak
from tests.api.test_session import Gated, Page, idle
from tests.brain.fakes import say
from tests.voice.audio import room, tone
from trillion.api.runtime import Runtime, open_runtime
from trillion.api.session import Session
from trillion.secrets.store import MemoryStore


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


async def test_talking_over_her_stops_the_reply_and_she_listens(rt: Runtime) -> None:
    stt = hearing(rt)
    provider = Gated(say("A long answer that never gets to finish."), say("You said stop."))
    held = provider.hold(0)
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "tell me everything"})
    await asyncio.sleep(0.05)
    assert session._task is not None  # she is mid-reply

    await session.handle({"type": "barge"})
    assert page.all("interrupted")  # her reply stopped
    assert page.all("mic.start")[-1]["follow_up"] is False  # and she listens
    assert session._listener is not None and session._task is None

    # What the user says over her is a turn like any other.
    held.set()
    await speak(session, room(0.2) + tone(0.6))
    stt.stream.say("stop, just the short version", final=True)
    await speak(session, room(0.6))
    await idle(session)
    assert page.all("transcript.final")[-1]["text"] == "stop, just the short version"
    await session.close()


async def test_a_barge_when_she_has_already_finished_just_listens(rt: Runtime) -> None:
    hearing(rt, say("Done."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "barge"})
    assert not page.all("interrupted") and not page.all("error")
    assert page.all("mic.start") and session._listener is not None
    await session.close()


async def test_without_ears_a_barge_says_so(rt: Runtime) -> None:
    rt.stt = None
    rt.listening = None
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "barge"})
    assert page.all("error")[-1]["code"] == "no_listening"
    await session.close()
