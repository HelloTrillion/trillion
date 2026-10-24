"""Early thinking (VOI-12): a draft started on "probably done", used or dropped."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Callable
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_listening import speak
from tests.api.test_session import Gated, Page, idle
from tests.brain.fakes import say, use
from tests.voice.audio import room, tone
from tests.voice.fakes import FakeSTT
from trillion import paths
from trillion.api.runtime import ListenSettings, Runtime, open_runtime
from trillion.api.session import Session
from trillion.brain.types import ProviderError
from trillion.secrets.store import MemoryStore


@pytest.fixture
async def rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    granted = tmp_path / "granted"
    granted.mkdir()
    runtime = await open_runtime(
        granted_roots=(granted,), persona=tmp_path, secrets=MemoryStore(), env={"TZ": "UTC"}
    )
    yield runtime
    await runtime.close()


def judging(rt: Runtime, *replies: Any) -> tuple[FakeSTT, Gated]:
    stt = FakeSTT(turns=True)
    rt.stt = stt
    rt.listening = ListenSettings(kind="deepgram", choice="en")
    provider = Gated(*replies)
    rt.provider = provider
    return stt, provider


async def start(rt: Runtime) -> tuple[Session, Page]:
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "listen.start"})
    await speak(session, room(0.2) + tone(0.6))
    return session, page


async def until(ok: Callable[[], bool]) -> None:
    for _ in range(400):
        if ok():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("never happened")


async def finish(session: Session, stt: FakeSTT, text: str) -> None:
    """The recognizer says the turn is over, and the next audio ends listening."""
    stt.stream.say(text, final=True, turn="done")
    await speak(session, room(0.1))
    await idle(session)


def drafts() -> list[str]:
    with closing(sqlite3.connect(paths.db_path())) as db:
        return [d for (d,) in db.execute("SELECT draft FROM turn_timings ORDER BY id")]


async def test_a_draft_that_matches_is_used_and_the_model_is_called_once(rt: Runtime) -> None:
    stt, provider = judging(rt, say("Sure, the dentist."))
    session, page = await start(rt)
    stt.stream.say("call the dentist", final=False, turn="maybe")
    await until(lambda: len(provider.calls) == 1)
    assert session._draft is not None
    assert not page.all("reply.delta") and session._history == []  # held, unsaved
    await finish(session, stt, "Call the dentist.")
    assert len(provider.calls) == 1  # the draft was the turn's first call
    assert page.all("reply.delta")[0]["text"] == "Sure, the dentist."
    kinds = [k for k, _ in await rt.conversations.history(session.id, "local")]
    assert kinds == ["user", "assistant"]
    assert session._turn == 1
    assert drafts() == ["used"]
    await session.close()


async def test_the_draft_sends_what_the_turn_would_have_sent(rt: Runtime) -> None:
    """The drafted call is marked as spoken, like any spoken message (#40)."""
    stt, provider = judging(rt, say("Sure."))
    session, _ = await start(rt)
    stt.stream.say("call the dentist", final=False, turn="maybe")
    await until(lambda: len(provider.calls) == 1)
    assert session._via == "text"  # nothing changed until the turn is real
    await finish(session, stt, "call the dentist")
    _, sent = provider.calls[0]
    saved = [m for _, m in await rt.conversations.history(session.id, "local")]
    assert sent == saved[:1]
    assert "THIS MESSAGE WAS SPOKEN" in str(sent[0].blocks[0].text)  # type: ignore[union-attr]
    await session.close()


async def test_carrying_on_drops_the_draft_and_nothing_is_saved_or_sent(rt: Runtime) -> None:
    stt, provider = judging(rt, say("Dropped."), say("The real answer."))
    session, page = await start(rt)
    stt.stream.say("I need to remember to", final=False, turn="maybe")
    await until(lambda: len(provider.calls) == 1)
    first = session._draft
    stt.stream.say("I need to remember to", final=False, turn="resumed")
    await until(lambda: session._draft is None)
    assert first is not None and first.finished
    assert not page.all("reply.delta") and session._history == []
    await finish(session, stt, "I need to remember to call the dentist")
    assert len(provider.calls) == 2
    assert [m["text"] for m in page.all("reply.delta")] == ["The real answer."]
    assert drafts() == ["dropped"]
    await session.close()


async def test_different_final_words_drop_the_draft(rt: Runtime) -> None:
    stt, provider = judging(rt, say("Dropped."), say("Fresh."))
    session, page = await start(rt)
    stt.stream.say("call the", final=False, turn="maybe")
    await until(lambda: len(provider.calls) == 1)
    await finish(session, stt, "call the dentist")
    assert [m["text"] for m in page.all("reply.delta")] == ["Fresh."]
    _, sent = provider.calls[1]
    assert sent[-1].blocks[-1].text == "call the dentist"  # type: ignore[union-attr]
    assert drafts() == ["dropped"]
    await session.close()


async def test_a_drafted_tool_call_runs_only_in_the_confirmed_turn(rt: Runtime) -> None:
    stt, provider = judging(rt, use("schedule_list", {}), say("Nothing set."))
    session, page = await start(rt)
    stt.stream.say("what reminders do I have", final=False, turn="maybe")
    await until(lambda: len(provider.calls) == 1)
    await asyncio.sleep(0.01)
    assert not page.all("tool.start")  # held, not run
    await finish(session, stt, "What reminders do I have?")
    starts = page.all("tool.start")
    assert [s["name"] for s in starts] == ["schedule_list"]
    assert session._turn == 1
    await session.close()


async def test_with_the_switch_off_no_draft_starts(rt: Runtime) -> None:
    stt, provider = judging(rt, say("Answer."))
    rt.think_early = False
    session, page = await start(rt)
    stt.stream.say("hello", final=False, turn="maybe")
    await asyncio.sleep(0.01)
    assert session._draft is None and provider.calls == []
    await finish(session, stt, "hello")
    assert [m["text"] for m in page.all("reply.delta")] == ["Answer."]
    assert drafts() == ["none"]
    await session.close()


async def test_a_failed_draft_is_dropped_quietly(rt: Runtime) -> None:
    stt, _provider = judging(rt, ProviderError("unreachable"), say("Recovered."))
    session, page = await start(rt)
    stt.stream.say("hello", final=False, turn="maybe")
    await until(lambda: session._draft is not None and session._draft.finished)
    await finish(session, stt, "hello")
    assert not page.all("error")
    assert [m["text"] for m in page.all("reply.delta")] == ["Recovered."]
    await session.close()


async def test_a_dropped_drafts_usage_is_recorded(rt: Runtime) -> None:
    stt, _provider = judging(rt, say("Dropped."), say("Kept."))
    session, _ = await start(rt)
    stt.stream.say("one", final=False, turn="maybe")
    await until(lambda: session._draft is not None and session._draft.finished)
    stt.stream.say("one", final=False, turn="resumed")
    await until(lambda: session._draft is None)
    await finish(session, stt, "one two")
    rows = await rt.conversations.usage_since("local", 0)
    assert [r["unpriced_turns"] for r in rows] == [2]  # the dropped draft, then the turn
    await session.close()


async def test_a_used_drafts_usage_is_recorded_once(rt: Runtime) -> None:
    stt, _provider = judging(rt, say("Sure."))
    session, _ = await start(rt)
    stt.stream.say("one", final=False, turn="maybe")
    await until(lambda: session._draft is not None and session._draft.finished)
    await finish(session, stt, "one")
    rows = await rt.conversations.usage_since("local", 0)
    assert [r["unpriced_turns"] for r in rows] == [1]
    await session.close()


async def test_stopping_while_she_listens_cancels_the_draft(rt: Runtime) -> None:
    stt, provider = judging(rt, say("Never heard."))
    provider.hold(0)
    session, page = await start(rt)
    stt.stream.say("call the dentist", final=False, turn="maybe")
    await until(lambda: provider.running == 1)
    draft = session._draft
    assert draft is not None
    await session.handle({"type": "interrupt"})
    assert session._draft is None and draft.cancelled and provider.running == 0
    assert not page.all("reply.delta") and session._history == []
    await session.close()


async def test_closing_cancels_the_draft(rt: Runtime) -> None:
    stt, provider = judging(rt, say("Never heard."))
    provider.hold(0)
    session, _ = await start(rt)
    stt.stream.say("call the dentist", final=False, turn="maybe")
    await until(lambda: provider.running == 1)
    draft = session._draft
    assert draft is not None
    await session.close()
    assert session._draft is None and draft.cancelled and provider.running == 0


async def test_a_new_maybe_replaces_the_draft(rt: Runtime) -> None:
    stt, provider = judging(rt, say("Second."))
    gate = provider.hold(0)
    session, page = await start(rt)
    stt.stream.say("call the", final=False, turn="maybe")
    await until(lambda: provider.running == 1)
    first = session._draft
    stt.stream.say("call the dentist", final=False, turn="maybe")
    await until(lambda: session._draft is not first and session._draft is not None)
    assert first is not None and first.cancelled
    gate.set()  # the second gets going; the first was stopped while held
    await finish(session, stt, "call the dentist")
    assert [m["text"] for m in page.all("reply.delta")] == ["Second."]
    assert len(provider.calls) == 1  # the held first call never got going
    await session.close()


async def test_a_draft_held_past_the_end_is_replayed_as_it_arrives(rt: Runtime) -> None:
    stt, provider = judging(rt, say("Late but used."))
    gate = provider.hold(0)
    session, page = await start(rt)
    stt.stream.say("call the dentist", final=False, turn="maybe")
    await until(lambda: provider.running == 1)
    stt.stream.say("call the dentist", final=True, turn="done")
    await speak(session, room(0.1))
    await page.until("transcript.final")
    await asyncio.sleep(0.01)
    gate.set()
    await idle(session)
    assert len(provider.calls) == 1
    assert [m["text"] for m in page.all("reply.delta")] == ["Late but used."]
    await session.close()


async def test_a_typed_turn_never_starts_a_draft(rt: Runtime) -> None:
    _stt, provider = judging(rt, say("Typed answer."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "hello"})
    await idle(session)
    assert session._draft is None and len(provider.calls) == 1
    assert drafts() == ["none"]
    await session.close()
