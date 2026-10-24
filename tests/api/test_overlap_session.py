"""Talking over her (VOI-9): duck, decide, then carry on or stop."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_drafts import judging
from tests.api.test_listening import hearing, speak
from tests.api.test_session import Gated, Page, idle
from tests.api.test_session_notices import voiced
from tests.brain.fakes import say, use
from tests.voice.audio import room, tone
from tests.voice.fakes import FakeSTT
from trillion.api.runtime import Runtime, open_runtime
from trillion.api.session import Session
from trillion.brain.loop import INTERRUPTED
from trillion.brain.types import TextBlock
from trillion.secrets.store import MemoryStore
from trillion.spine.contract import (
    JSON,
    BaseTool,
    Risk,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from trillion.voice.types import VoiceError

ANSWER = "First thing to know. Second thing to know. Third thing to know."


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


async def until(ok: Callable[[], bool]) -> None:
    for _ in range(400):
        if ok():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("never happened")


async def asked(rt: Runtime, stt: FakeSTT) -> tuple[Page, Session]:
    """A spoken question; the turn starts."""
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "listen.start"})
    await speak(session, room(0.2) + tone(0.6))
    stt.stream.say("tell me the plan", final=True, turn="done")
    await speak(session, room(0.1))
    await until(lambda: session._task is not None)
    return page, session


async def spoken_reply(
    rt: Runtime, *replies: Any
) -> tuple[FakeSTT, Gated, asyncio.Event, Page, Session]:
    """A spoken turn whose reply is held before the model's first word."""
    stt, provider = judging(rt, *replies)
    voiced(rt)  # a core voice, so the turn is spoken
    held = provider.hold(0)
    page, session = await asked(rt, stt)
    await until(lambda: provider.running == 1)  # she is answering
    return stt, provider, held, page, session


async def answered(rt: Runtime) -> tuple[FakeSTT, Page, Session]:
    """A spoken turn answered in full and over, while her reply still plays."""
    stt, _ = judging(rt, say(ANSWER), say("Of course."))
    voiced(rt)
    page, session = await asked(rt, stt)
    await page.until("turn.complete")
    await idle(session)
    assert session._tail is not None
    return stt, page, session


async def played(session: Session, page: Page, *seqs: int) -> None:
    turn = page.all("reply.sentence")[0]["turn"]
    for seq in seqs:
        await session.handle({"type": "playback.sentence", "turn_id": turn, "seq": seq})


async def test_an_acknowledgement_lets_her_carry_on(rt: Runtime) -> None:
    stt, _, held, page, session = await spoken_reply(rt, say("We leave at six. Then dinner."))
    partials = len(page.all("transcript.partial"))
    await session.handle({"type": "overlap"})
    assert session._task is not None and session._overlap is not None
    assert session.facts().listening  # nothing is delivered over them
    stt.stream.say("yeah", final=True, turn="done")
    await speak(session, tone(0.3) + room(0.1))
    await page.until("overlap.over")
    # Words over her are not shown unless they become a turn.
    assert not page.all("interrupted") and len(page.all("transcript.partial")) == partials
    assert session._overlap is None and session._task is not None
    assert session._turn == 1 and session._listener is None
    held.set()
    await idle(session)
    kinds = [k for k, _ in await rt.conversations.history(session.id, "local")]
    assert kinds == ["user", "assistant"]
    await session.close()


async def test_a_real_interruption_stops_her_and_becomes_the_turn(rt: Runtime) -> None:
    stt, _, held, page, session = await spoken_reply(
        rt, say("We leave at six. Then dinner."), say("Sure, seven then.")
    )
    await session.handle({"type": "overlap"})
    stt.stream.say("wait, make it seven", final=False)
    await speak(session, tone(0.3))
    await page.until("interrupted")
    await until(lambda: session._listener is not None)
    assert session._overlap is None and not page.all("overlap.over")
    assert page.all("mic.start")[-1]["follow_up"] is False
    assert page.all("transcript.partial")[-1]["text"] == "wait, make it seven"
    held.set()
    stt.stream.say("wait, make it seven", final=True, turn="done")
    await speak(session, room(0.1))
    await page.until("transcript.final", 2)
    await idle(session)
    assert page.all("transcript.final")[-1]["text"] == "wait, make it seven"
    assert session._turn == 2
    await session.close()


async def test_an_interruption_already_finished_is_the_turn_at_once(rt: Runtime) -> None:
    stt, _, _, page, session = await spoken_reply(rt, say("We leave at six."), say("Seven it is."))
    await session.handle({"type": "overlap"})
    stt.stream.say("no, seven", final=True, turn="done")
    await speak(session, tone(0.3))
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == "no, seven"
    await session.close()


async def test_a_cough_carries_on_after_the_limit(rt: Runtime) -> None:
    _, _, held, page, session = await spoken_reply(rt, say("A long answer."))
    await session.handle({"type": "overlap"})
    await speak(session, room(1.6))
    await page.until("overlap.over")
    assert not page.all("interrupted") and session._task is not None
    held.set()
    await session.close()


async def test_without_turn_judging_hearing_an_overlap_is_a_barge(rt: Runtime) -> None:
    hearing(rt)
    provider = Gated(say("A long answer that never gets to finish."))
    provider.hold(0)
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "tell me everything"})
    await until(lambda: provider.running == 1)  # she is answering
    await session.handle({"type": "overlap"})
    assert page.all("interrupted")
    assert session._overlap is None and session._listener is not None
    assert page.all("mic.start")[-1]["follow_up"] is False
    await session.close()


async def test_an_interruption_while_her_finished_reply_plays_keeps_what_was_heard(
    rt: Runtime,
) -> None:
    stt, page, session = await answered(rt)
    await played(session, page, 1)
    await session.handle({"type": "overlap"})
    assert session._overlap is not None and session._tail is not None
    stt.stream.say("no, the other one", final=False)
    await speak(session, tone(0.2))
    await page.until("interrupted")
    assert page.all("interrupted")[-1]["kept"] == "First thing to know.…"
    history = await rt.conversations.history(session.id, "local")
    assert history[-1][1].blocks == (TextBlock(text="First thing to know.…" + INTERRUPTED),)
    await until(lambda: session._listener is not None)
    assert page.all("mic.start")[-1]["follow_up"] is False
    await session.close()


async def test_an_acknowledgement_while_her_finished_reply_plays_changes_nothing(
    rt: Runtime,
) -> None:
    stt, page, session = await answered(rt)
    await played(session, page, 1)
    mics = len(page.all("mic.start"))
    await session.handle({"type": "overlap"})
    # She finishes being heard while they are deciding: the microphone stays as it is.
    turn = page.all("reply.sentence")[0]["turn"]
    await session.handle({"type": "playback.ended", "turn_id": turn})
    assert len(page.all("mic.start")) == mics and session._listener is None
    stt.stream.say("mm-hm", final=True, turn="done")
    await speak(session, tone(0.2) + room(0.1))
    await page.until("overlap.over")
    assert not page.all("interrupted")
    history = await rt.conversations.history(session.id, "local")
    assert history[-1][1].blocks == (TextBlock(text=ANSWER),)
    # The follow-up window her end would have opened opens now.
    await until(lambda: len(page.all("mic.start")) == mics + 1)
    assert page.all("mic.start")[-1]["follow_up"] is True
    await session.close()


class Slow(BaseTool):
    """A read that waits for the test to let it finish."""

    definition = ToolDefinition(
        name="slow_read",
        description="Wait, then read something (test).",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk=Risk.READ,
        category="test",
        timeout_s=5.0,
    )

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        self.started.set()
        await self.release.wait()
        return ToolResult(ok=True, content="read")


async def test_talking_over_her_while_a_tool_runs(rt: Runtime) -> None:
    tool = Slow()
    rt.registry.register(tool, module="tests.api")
    stt, _ = judging(rt, use("slow_read", {}), say("Here it is."))
    voiced(rt)
    page, session = await asked(rt, stt)
    await asyncio.wait_for(tool.started.wait(), 5)
    await session.handle({"type": "overlap"})
    stt.stream.say("okay", final=True, turn="done")
    await speak(session, tone(0.2) + room(0.1))
    await page.until("overlap.over")
    tool.release.set()
    await page.until("turn.complete")
    assert [m["text"] for m in page.all("reply.delta")] == ["Here it is."]

    # And over a tool, a real interruption stops her once the tool is done (VOI-15c).
    tool.started.clear()
    tool.release.clear()
    rt.provider = Gated(use("slow_read", {}), say("Never said."))
    await session.handle({"type": "playback.ended", "turn_id": page.all("audio.end")[-1]["turn"]})
    await until(lambda: session._listener is not None)
    await speak(session, room(0.1) + tone(0.3))
    stt.stream.say("what is there", final=True, turn="done")
    await speak(session, room(0.1))
    await asyncio.wait_for(tool.started.wait(), 5)
    await session.handle({"type": "overlap"})
    stt.stream.say("stop", final=False)
    await speak(session, tone(0.2))
    await asyncio.sleep(0.05)
    assert not page.all("interrupted")  # the read finishes first
    tool.release.set()
    await page.until("interrupted")
    await until(lambda: session._listener is not None)
    assert "Never said." not in [m["text"] for m in page.all("reply.delta")]
    await session.close()


async def test_stop_while_deciding_stops_everything(rt: Runtime) -> None:
    stt, _, _, page, session = await spoken_reply(rt, say("A long answer."))
    await session.handle({"type": "overlap"})
    stream = stt.stream
    await session.handle({"type": "interrupt"})
    assert session._overlap is None and stream.closed
    await page.until("interrupted")
    await idle(session)
    assert not page.all("overlap.over") and session._listener is None
    await session.close()


async def test_closing_while_deciding_lets_the_listener_go(rt: Runtime) -> None:
    stt, _, _, _, session = await spoken_reply(rt, say("A long answer."))
    await session.handle({"type": "overlap"})
    stream = stt.stream
    await session.close()
    assert session._overlap is None and stream.closed
    assert session._task is None or session._task.done()


async def test_no_draft_starts_while_deciding(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    calls = len(rt.provider.calls)  # type: ignore[union-attr]
    await played(session, page, 1)
    await session.handle({"type": "overlap"})
    # Still deciding: an acknowledgement waits for its moment.
    stt.stream.say("yeah", final=False, turn="maybe")
    await asyncio.sleep(0.02)
    await session._start_draft("yeah")
    assert session._overlap is not None
    assert session._draft is None and session._tail is not None
    assert len(rt.provider.calls) == calls  # type: ignore[union-attr]
    await session.close()


async def test_hearing_that_fails_while_deciding_lets_her_carry_on(rt: Runtime) -> None:
    _, _, held, page, session = await spoken_reply(rt, say("A long answer."))
    await session.handle({"type": "overlap"})
    stream = session._overlap.listener._stream  # type: ignore[union-attr]
    assert stream is not None

    async def broken(pcm: bytes) -> None:
        raise VoiceError("unreachable")

    stream.send = broken  # type: ignore[method-assign]
    await speak(session, tone(0.2))
    await page.until("overlap.over")
    assert not page.all("interrupted") and not page.all("error")
    assert session._overlap is None and session._task is not None
    held.set()
    await idle(session)
    await session.close()
