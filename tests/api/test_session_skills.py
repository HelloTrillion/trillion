"""A turn lists the skills that are on in her instructions, the same every turn (CON-6)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.api.test_session import Page, idle
from tests.brain.fakes import ScriptedProvider, say
from trillion.api.runtime import Runtime, open_runtime
from trillion.api.session import Session
from trillion.secrets.store import MemoryStore
from trillion.skills.library import Library


@pytest.fixture
async def rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    granted = tmp_path / "granted"
    granted.mkdir()
    runtime = await open_runtime(
        granted_roots=(granted,), persona=tmp_path, secrets=MemoryStore(), env={}
    )
    assert runtime.skills is not None
    runtime.skills.turn_on("curated:weekly-review")
    yield runtime
    await runtime.close()


async def two_turns(rt: Runtime) -> list[str]:
    provider = ScriptedProvider(say("One."), say("Two."))
    rt.provider = provider
    session = await Session.start(rt, Page(rt).send, None)
    for text in ("first", "second"):
        await session.handle({"type": "turn.text", "text": text})
        await idle(session)
    await session.close()
    return ["".join(b.text for b in system) for system, _ in provider.calls]


async def test_a_turn_lists_the_skills_that_are_on_the_same_each_turn(rt: Runtime) -> None:
    first, second = await two_turns(rt)
    assert "- weekly-review: Run a weekly review" in first and "skill_open" in first
    assert first == second  # byte for byte, so the prompt cache holds
    assert "meeting-prep" not in first  # off


async def test_describe_self_uses_the_skills_this_turn_read(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    await two_turns(rt)

    def no_disk(self: Library) -> None:
        raise AssertionError("read the skills again on the event loop")

    monkeypatch.setattr(Library, "enabled", no_disk)
    assert "weekly-review" in rt.facts()["skills"]


async def test_a_channel_answer_lists_no_skills(rt: Runtime) -> None:
    assert rt.channels is not None
    text = "".join(b.text for b in rt.channels._answerer.system())
    assert "skill_open" not in text and "weekly-review" not in text


async def test_skills_that_cannot_be_read_leave_the_turn_without_them(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(self: Library) -> None:
        raise RuntimeError("/Users/someone/secret-path")

    monkeypatch.setattr(Library, "enabled", broken)
    with caplog.at_level(logging.WARNING):
        first, _ = await two_turns(rt)
    assert "weekly-review" not in first and "skill_open" not in first
    assert "RuntimeError" in caplog.text and "secret-path" not in caplog.text
