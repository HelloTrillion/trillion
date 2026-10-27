"""A page and the routines (the routines spec): the ready line spoken like a reminder,
a routine's own tools never offered to a page, the host line on reload, and a page
open on the routine's conversation that sees the run."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_session import Gated, Page, idle
from tests.brain.fakes import say, use
from tests.scheduler.conftest import FakeClock, ms, schedule, written
from tests.scheduler.test_ask import Recording
from tests.voice.test_speaker import FakeVoice
from trillion.api.runtime import Runtime, SpeechSettings, open_runtime
from trillion.api.session import OUT_OF_TURN, Session
from trillion.brain.types import ContextBlock, Msg, TextBlock
from trillion.persona.prompt import system_blocks
from trillion.scheduler.ask import ROUTINE_RUN, Asker, RoutineDeps
from trillion.scheduler.runner import Busy
from trillion.secrets.store import MemoryStore

DUE = ms("2026-10-26T08:00")
NY = "America/New_York"
NOTE = {"type": "note", "text": "Revenue is up."}


class Shown:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, title: str, body: str) -> bool:
        self.calls.append((title, body))
        return True


@pytest.fixture
async def rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    granted = tmp_path / "granted"
    granted.mkdir()
    runtime = await open_runtime(
        granted_roots=(granted,), persona=tmp_path, secrets=MemoryStore(), env={"TZ": "UTC"}
    )
    runtime.clock = FakeClock(DUE)
    assert runtime.door is not None
    runtime.door.notify = Shown()  # never the real notification in a test
    yield runtime
    await runtime.close()


def asker(rt: Runtime, provider: Recording | None) -> Asker:
    """The runtime's own wiring, with a model of the test's."""
    return Asker(
        provider=lambda: provider,
        router=rt.router,
        registry=rt.registry,
        conversations=rt.conversations,
        system=lambda: system_blocks(rt.identity, rt.soul, rt.granted_roots),
        identity=lambda: rt.identity,
        granted_roots=lambda: rt.granted_roots,
        zone=lambda: NY,
        clock=rt.clock,
        owner="local",
        routines=RoutineDeps(
            boards=lambda: rt.boards,
            reads=rt.reads,
            busy=rt.page_writing,
            ran=rt.routine_ran,
            holds=rt.routine_holds,
        ),
    )


def request(title: str) -> Msg:
    return Msg(
        role="user",
        blocks=(
            ContextBlock(text="<context>now</context>"),
            ContextBlock(text=ROUTINE_RUN),
            TextBlock(text=f"Routine: {title}\n\nShow me last week's revenue."),
        ),
    )


async def test_the_ready_line_is_spoken_once_and_kept(rt: Runtime) -> None:
    assert rt.schedules is not None and rt.door is not None
    rt.tts, rt.speech = FakeVoice(), SpeechSettings(kind="elevenlabs", voice="v1")
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    line = "Your Monday revenue is ready."
    n = await written(
        rt.schedules, kind="ready", text=line, title=line[:-1], due_ms=DUE, now_ms=DUE
    )
    await rt.door.added(n)
    await idle(session)
    (shown,) = page.all("notice")
    assert (shown["kind"], shown["text"]) == ("ready", line)
    (said,) = page.all("reply.sentence")
    assert said["text"] == line and said["turn"] == OUT_OF_TURN + 1
    history = await rt.conversations.history(session.id, "local")
    assert history[-1][1].blocks == (TextBlock(text=line),)
    await session.close()


async def test_a_pages_turn_is_never_offered_a_routines_own_tools(rt: Runtime) -> None:
    provider = Recording(say("Hello."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "hello"})
    await idle(session)
    offered = provider.offered[0]  # the turn's; naming may follow it
    assert "routine_nothing" not in offered
    assert "workspace_show" in offered and "confirm_pending_action" in offered
    await session.close()


async def test_a_routine_request_shows_as_a_host_line(rt: Runtime) -> None:
    cid = await rt.conversations.routine_conversation("local", "Monday revenue", 1)
    await rt.conversations.append(cid, "local", "tap", request("Monday revenue"), 2)
    reply = Msg(role="assistant", blocks=(TextBlock(text="Revenue is up."),))
    await rt.conversations.append(cid, "local", "assistant", reply, 3)
    page = Page(rt)
    session = await Session.start(rt, page.send, cid)
    (history,) = page.all("history")
    assert history["messages"] == [
        {"role": "host", "text": "Routine: Monday revenue"},
        {"role": "assistant", "text": "Revenue is up."},
    ]
    assert session._turn == 0  # the host's, never a turn of the user's
    await session.close()


async def test_an_idle_page_open_there_sees_the_run(rt: Runtime) -> None:
    cid = await rt.conversations.routine_conversation("local", "Monday revenue", 1)
    page = Page(rt)
    session = await Session.start(rt, page.send, cid)
    a = asker(
        rt,
        Recording(use("workspace_show", {"widgets": [NOTE]}), say("Revenue is up this week.")),
    )

    s = schedule(kind="ask", title="Monday revenue", target="own", conversation_id=cid)
    assert await a.place(s) == cid
    await a.build(s, cid)
    first, second = page.all("history")
    assert first["messages"] == []
    assert second["messages"] == [
        {"role": "host", "text": "Routine: Monday revenue"},
        {"role": "assistant", "text": "Revenue is up this week."},
    ]
    boards = page.all("board")
    assert boards[-1]["board"] is not None and boards[-1]["board"]["widgets"]
    # It knows the history now: her next turn is sent it.
    assert [m.role for m in session._history][-1] == "assistant"
    # Nothing delivered: notices reach the user only through the door.
    assert page.all("notice") == [] and page.all("reply.sentence") == []
    await session.close()


async def test_a_turn_running_there_holds_the_run(rt: Runtime) -> None:
    cid = await rt.conversations.routine_conversation("local", "Monday revenue", 1)
    provider = Gated(say("Working on it."))
    rt.provider = provider
    held = provider.hold(0)
    page = Page(rt)
    session = await Session.start(rt, page.send, cid)
    await session.handle({"type": "turn.text", "text": "how is revenue?"})

    s = schedule(kind="ask", title="Monday revenue", target="own", conversation_id=cid)
    with pytest.raises(Busy):
        await asker(rt, None).place(s)
    with pytest.raises(Busy):
        await asker(rt, None).build(s, cid)
    assert cid not in rt.routine_holds  # waiting takes nothing
    # A reload never lands in the middle of a turn: the page reads the store on its own
    # when the turn is over. (A routine holds its conversation, so none of its runs
    # ever ends while a turn runs here.)
    assert not await session.reloaded()
    held.set()
    await idle(session)
    assert await asker(rt, None).place(s) == cid
    await session.close()


async def test_a_turn_tried_while_a_routine_builds_is_refused(rt: Runtime) -> None:
    cid = await rt.conversations.routine_conversation("local", "Monday revenue", 1)
    rt.provider = Recording(say("Revenue looks fine."))
    page = Page(rt)
    session = await Session.start(rt, page.send, cid)
    model = Gated(use("workspace_show", {"widgets": [NOTE]}), say("Revenue is up."))
    gate = model.hold(1)
    s = schedule(kind="ask", title="Monday revenue", target="own", conversation_id=cid)
    building = asyncio.create_task(asker(rt, model).build(s, cid))  # type: ignore[arg-type]
    for _ in range(400):
        if await rt.boards.current(cid) is not None:  # type: ignore[union-attr]
            break
        await asyncio.sleep(0.005)
    assert cid in rt.routine_holds
    await session.handle({"type": "turn.text", "text": "how is revenue?"})
    await idle(session)
    assert [e["code"] for e in page.all("error")] == ["routine_building"]
    gate.set()
    await building
    assert cid not in rt.routine_holds
    kinds = [k for k, _ in await rt.conversations.history(cid, "local")]
    # The run's messages, whole and in order, and nothing of the page's between them.
    assert kinds == ["tap", "assistant", "tool_results", "assistant"]
    await session.handle({"type": "turn.text", "text": "how is revenue?"})
    await idle(session)
    kinds = [k for k, _ in await rt.conversations.history(cid, "local")]
    assert kinds[4:] == ["user", "assistant"]
    await session.close()


async def test_a_reload_that_raced_a_save_is_dropped(rt: Runtime) -> None:
    cid = await rt.conversations.routine_conversation("local", "Monday revenue", 1)
    page = Page(rt)
    session = await Session.start(rt, page.send, cid)
    await rt.conversations.append(cid, "local", "tap", request("Monday revenue"), 2)
    read = session._read

    async def racing() -> Any:
        loaded = await read()
        session._saves += 1  # a message saved here while the store was read
        return loaded

    session._read = racing  # type: ignore[method-assign]
    assert not await session.reloaded()
    assert session._history == [] and len(page.all("history")) == 1
    session._read = read  # type: ignore[method-assign]
    assert await session.reloaded()
    assert len(session._history) == 1 and len(page.all("history")) == 2
    await session.close()
