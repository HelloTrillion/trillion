"""A helper's first hello (the your helpers spec, Its first hello): when its chat first
opens it takes one turn of its own, once ever, in its own voice; a routine it offers is
its own on a card; a model that fails leaves its fixed first line. And the create card
shows the picture its tap makes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.api.test_session import Page, idle
from tests.brain.fakes import say, use
from tests.scheduler.conftest import FakeClock, ms, schedule
from tests.scheduler.test_ask import Recording
from trillion.api.runtime import Runtime, open_runtime
from trillion.api.session import HELPER_HELLO, Session
from trillion.brain.types import ContextBlock, ProviderError, TextBlock
from trillion.helpers.store import OwnHelper
from trillion.scheduler.when import Repeat
from trillion.secrets.store import MemoryStore
from trillion.spine.contract import ToolContext
from trillion.tools.helper import CREATE, make
from trillion.tools.routine import NARROW as ROUTINE_NARROW
from trillion.tools.routine import WIDEN as ROUTINE_WIDEN

NOW = ms("2026-11-05T09:00")
JOB = "Chases overdue invoices."


@pytest.fixture
async def rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    granted = tmp_path / "granted"
    granted.mkdir()
    runtime = await open_runtime(
        share_rules=lambda _p: None,
        granted_roots=(granted,),
        persona=tmp_path,
        secrets=MemoryStore(),
        env={"TZ": "UTC"},
    )
    runtime.clock = FakeClock(NOW)
    await runtime.stop_background()
    yield runtime
    await runtime.close()


async def made(rt: Runtime) -> OwnHelper:
    assert rt.helpers is not None
    h = await make(rt.helpers, rt.conversations, "local", "Invoices", JOB, NOW)
    assert isinstance(h, OwnHelper)
    return h


def texts(rt_history: list[tuple[str, object]]) -> list[str]:
    out = []
    for _, msg in rt_history:
        blocks = getattr(msg, "blocks", ())
        out.extend(b.text for b in blocks if isinstance(b, TextBlock))
    return out


async def test_the_first_hello_runs_one_turn_once_in_its_voice(rt: Runtime) -> None:
    h = await made(rt)
    provider = Recording(say("First I'll look for anything overdue."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, h.conversation_id)
    await session.handle({"type": "helper.hello"})
    await idle(session)
    assert len(provider.calls) == 1
    # Conversation spend: the user is looking at its chat.
    assert provider.callers == ["conversation"]
    _system, messages = provider.calls[0]
    last = messages[-1]
    context = "\n".join(b.text for b in last.blocks if isinstance(b, ContextBlock))
    assert "You are Invoices, one of Trillion's helpers." in context
    assert any(isinstance(b, TextBlock) and b.text == HELPER_HELLO for b in last.blocks)
    history = await rt.conversations.history(h.conversation_id, "local")
    assert [k for k, _ in history] == ["assistant", "tap", "assistant"]
    said = texts(history)
    assert said[0].startswith("Hi, I'm Invoices.")
    assert said[-1] == "First I'll look for anything overdue."
    assert rt.helpers is not None
    got = await rt.helpers.get("local", h.id)
    assert got is not None and got.hello_ms == NOW
    # Never again, on this page or another.
    await session.handle({"type": "helper.hello"})
    await idle(session)
    await session.close()
    page = Page(rt)
    session = await Session.start(rt, page.send, h.conversation_id)
    await session.handle({"type": "helper.hello"})
    await idle(session)
    assert len(provider.calls) == 1
    await session.close()


async def test_no_hello_outside_a_helpers_chat(rt: Runtime) -> None:
    await made(rt)
    provider = Recording(say("Hello."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "helper.hello"})
    await idle(session)
    assert provider.calls == []
    await session.close()


async def test_no_hello_over_a_running_turn(rt: Runtime) -> None:
    h = await made(rt)
    provider = Recording(say("Sure."), say("Hello."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, h.conversation_id)
    await session.handle({"type": "turn.text", "text": "skip March"})
    await session.handle({"type": "helper.hello"})
    await idle(session)
    assert len(provider.calls) == 1
    # Not spent: it may still say hello once that turn is over.
    assert rt.helpers is not None
    got = await rt.helpers.get("local", h.id)
    assert got is not None and got.hello_ms is None
    await session.close()


async def test_a_routine_it_offers_is_its_own_on_a_card(rt: Runtime) -> None:
    h = await made(rt)
    offer = {
        "when": "2026-11-09T09:00",
        "ask": "Check for overdue invoices.",
        "title": "Overdue check",
        "repeat": {"every": "week", "days": ["mon"]},
        "routine": "own",
    }
    provider = Recording(
        use("ask_schedule", offer, "c1"),
        say("Want me to check every Monday at 9?"),
        say("Done."),
    )
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, h.conversation_id)
    await session.handle({"type": "helper.hello"})
    await page.until("confirm.request")
    await idle(session)
    card = page.all("confirm.request")[0]
    # A routine, said so, and where it runs: in this helper's chat.
    assert card["tool_label"] == "Set up a routine"
    assert card["summary"].endswith('(a routine, "Overdue check", in Invoices\'s chat)')
    assert rt.schedules is not None
    assert await rt.schedules.active("local") == []
    # Nothing starts without the tap.
    await session.handle({"type": "confirm", "pending_id": card["pending_id"]})
    await page.until("confirm.resolved")
    await idle(session)
    (s,) = await rt.schedules.active("local")
    assert (s.target, s.helper_id, s.conversation_id) == ("own", h.id, h.conversation_id)
    await session.close()


def test_the_hello_never_says_a_routine_is_set_up() -> None:
    assert "on a card" in HELPER_HELLO
    assert "Never say it is set up" in HELPER_HELLO
    assert "starts once they tap Approve" in HELPER_HELLO


async def test_a_plain_timed_ask_keeps_its_label(rt: Runtime) -> None:
    offer = {"when": "2026-11-09T09:00", "ask": "Check the news.", "title": "News"}
    rt.provider = Recording(use("ask_schedule", offer, "c1"), say("Tap Approve."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "check the news on monday"})
    await page.until("confirm.request")
    await idle(session)
    assert page.all("confirm.request")[0]["tool_label"] == "Schedule a timed ask"
    await session.close()


async def test_a_routine_in_her_chat_says_its_own_conversation(rt: Runtime) -> None:
    offer = {
        "when": "2026-11-09T09:00",
        "ask": "Check the news.",
        "title": "News",
        "routine": "own",
    }
    rt.provider = Recording(use("ask_schedule", offer, "c1"), say("Tap Approve."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "check the news every monday"})
    await page.until("confirm.request")
    await idle(session)
    card = page.all("confirm.request")[0]
    assert card["tool_label"] == "Set up a routine"
    assert card["summary"].endswith("in its own conversation)")
    await session.close()


async def test_a_model_that_fails_leaves_the_fixed_line(rt: Runtime) -> None:
    h = await made(rt)
    rt.provider = Recording(ProviderError("unreachable"))
    page = Page(rt)
    session = await Session.start(rt, page.send, h.conversation_id)
    await session.handle({"type": "helper.hello"})
    await idle(session)
    history = await rt.conversations.history(h.conversation_id, "local")
    said = [t for k, m in history if k == "assistant" for t in texts([(k, m)])]
    assert said == [f"Hi, I'm Invoices. {JOB} Tell me anything I should know to start."]
    # Once ever: a failed hello is not tried again.
    assert rt.helpers is not None
    got = await rt.helpers.get("local", h.id)
    assert got is not None and got.hello_ms is not None
    await session.close()


async def test_the_create_card_shows_the_picture_its_tap_makes(rt: Runtime) -> None:
    args = {"name": "Invoices", "job": JOB, "icon": "leaf", "color": "rose"}
    provider = Recording(use(CREATE, args, "c1"), say("Tap Approve to make it."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "make me an invoices helper"})
    await page.until("confirm.request")
    await idle(session)
    card = page.all("confirm.request")[0]
    assert card["look"] == {"icon": "leaf", "color": "rose"}
    await session.close()


async def test_only_a_create_card_carries_a_look(rt: Runtime) -> None:
    offer = {"when": "2026-11-09T09:00", "ask": "Check the news.", "title": "News"}
    provider = Recording(use("ask_schedule", offer, "c1"), say("Tap Approve."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "check the news on monday"})
    await page.until("confirm.request")
    await idle(session)
    assert "look" not in page.all("confirm.request")[0]
    await session.close()


async def test_no_hello_turn_once_the_chat_has_more_than_its_first_line(rt: Runtime) -> None:
    h = await made(rt)
    provider = Recording(say("Noted."), say("Hello."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, h.conversation_id)
    await session.handle({"type": "turn.text", "text": "skip March"})
    await idle(session)
    await session.handle({"type": "helper.hello"})
    await idle(session)
    assert len(provider.calls) == 1
    # Spent: it is already talking.
    assert rt.helpers is not None
    got = await rt.helpers.get("local", h.id)
    assert got is not None and got.hello_ms is not None
    await session.close()


async def test_a_paused_helper_says_hello_only_once_resumed(rt: Runtime) -> None:
    h = await made(rt)
    assert rt.helpers is not None
    assert await rt.helpers.pause("local", h.id, NOW)
    provider = Recording(say("Hello."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, h.conversation_id)
    await session.handle({"type": "helper.hello"})
    await idle(session)
    assert provider.calls == []
    got = await rt.helpers.get("local", h.id)
    assert got is not None and got.hello_ms is None
    assert await rt.helpers.resume("local", h.id)
    await session.handle({"type": "helper.hello"})
    await idle(session)
    assert len(provider.calls) == 1
    await session.close()


async def test_a_helpers_chat_has_no_routine_tools_or_line_for_its_routines(rt: Runtime) -> None:
    h = await made(rt)
    assert rt.schedules is not None
    for sid in ("r0000001", "r0000002"):
        await rt.schedules.create(
            schedule(
                id=sid,
                kind="ask",
                title=f"Check {sid}",
                body="Check what's overdue.",
                target="own",
                at_local="2026-11-09T09:00",
                next_due_ms=ms("2026-11-09T09:00"),
                repeat=Repeat(every="day"),
                helper_id=h.id,
                conversation_id=h.conversation_id,
            )
        )
    provider = Recording(say("Sure."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, h.conversation_id)
    await session.handle({"type": "turn.text", "text": "anything overdue?"})
    await idle(session)
    assert not {ROUTINE_NARROW, ROUTINE_WIDEN} & set(provider.offered[0])
    _system, messages = provider.calls[0]
    context = "\n".join(b.text for b in messages[-1].blocks if isinstance(b, ContextBlock))
    assert "routine: " not in context
    await session.close()


async def test_a_reminder_or_timed_ask_in_a_helpers_chat_is_not_the_helpers(
    rt: Runtime,
) -> None:
    h = await made(rt)
    ctx = ToolContext(
        owner_id="local",
        session_id=h.conversation_id,
        turn=1,
        call_id="t1",
        via="text",
        helper_id=h.id,
    )
    when = {"when": "2026-11-09T09:00", "title": "Plain"}
    reminder, ask = rt.registry.get("reminder_set"), rt.registry.get("ask_schedule")
    assert reminder is not None and ask is not None
    assert (await reminder.execute({**when, "say": "Call Sam."}, ctx)).ok
    assert (await ask.execute({**when, "title": "Asked", "ask": "Check the news."}, ctx)).ok
    assert rt.schedules is not None
    made_here = await rt.schedules.active("local")
    assert len(made_here) == 2
    assert all(s.helper_id is None and s.conversation_id is None for s in made_here)
