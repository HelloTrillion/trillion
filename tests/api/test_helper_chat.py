"""A helper's own chat and her list of helpers (the your helpers spec): its chat
speaks as it and changes only it, her chats list the helpers not paused and may hand
them work, and a confirmed create card tells the page to open the new chat."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.api.test_session import Page, idle
from tests.brain.fakes import say, use
from tests.scheduler.conftest import FakeClock, ms
from tests.scheduler.test_ask import Recording
from trillion.api.runtime import Runtime, open_runtime
from trillion.api.session import Session
from trillion.brain.types import ContextBlock, Msg, TextBlock
from trillion.helpers.store import OwnHelper
from trillion.secrets.store import MemoryStore
from trillion.tools.helper import ASK, CREATE, NARROW, SUGGEST, UPDATE, WIDEN

NOW = ms("2026-11-05T09:00")
DISPATCH = {CREATE, ASK, SUGGEST}
OWN = {UPDATE, NARROW, WIDEN}


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
    yield runtime
    await runtime.close()


async def made(
    rt: Runtime, name: str = "Reports", job: str = "Sums up the weekly reports."
) -> OwnHelper:
    assert rt.helpers is not None
    h = await rt.helpers.create("local", name, job, NOW)
    assert isinstance(h, OwnHelper)
    return h


def context_of(provider: Recording, call: int = 0) -> str:
    """What the newest user message of one model call carried ahead of its words."""
    _system, messages = provider.calls[call]
    last = messages[-1]
    return "\n".join(b.text for b in last.blocks if isinstance(b, ContextBlock))


async def test_a_helpers_chat_speaks_as_it_and_changes_only_it(rt: Runtime) -> None:
    assert rt.helpers is not None
    h = await made(rt)
    await rt.helpers.add_note("local", h.id, "Round to the nearest dollar.")
    provider = Recording(
        use(NARROW, {"helper": h.id, "text": "Don't include refunds."}, "c1"),
        say("Noted."),
    )
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, h.conversation_id)
    assert session.id == h.conversation_id and session.kind == "helper"
    await session.handle({"type": "turn.text", "text": "don't include refunds"})
    await idle(session)
    context = context_of(provider)
    assert "You are Reports, one of Trillion's helpers." in context
    assert "Your job: Sums up the weekly reports." in context
    assert "Follow these notes from the user: Round to the nearest dollar." in context
    assert f'helper: {h.id} "Reports"' in context
    # Her list of helpers is for her own chats, not a helper's.
    assert "untrusted_tool_result" not in context
    offered = set(provider.offered[0])
    assert OWN <= offered and not DISPATCH & offered
    # The narrowing ran with this chat's helper on its context: only it may change it.
    got = await rt.helpers.get("local", h.id)
    assert got is not None and "Don't include refunds." in got.notes
    await session.close()


async def test_a_card_tapped_in_a_helpers_chat_changes_it(rt: Runtime) -> None:
    """The tap is resolved with the helper on its context, as her own call was."""
    h = await made(rt)
    provider = Recording(
        use(WIDEN, {"helper": h.id, "text": "Also cover the monthly reports."}, "c1"),
        say("Tap Approve to add it."),
        say("Added."),
    )
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, h.conversation_id)
    await session.handle({"type": "turn.text", "text": "cover the monthly ones too"})
    await page.until("confirm.request")
    await idle(session)
    card = page.all("confirm.request")[0]["pending_id"]
    # Tapped from a page opened since, before any turn of its own there.
    await session.close()
    page = Page(rt)
    session = await Session.start(rt, page.send, h.conversation_id)
    await session.handle({"type": "confirm", "pending_id": card})
    await page.until("confirm.resolved")
    await idle(session)
    assert page.all("confirm.resolved")[0]["status"] == "succeeded"
    assert rt.helpers is not None
    got = await rt.helpers.get("local", h.id)
    assert got is not None and "Also cover the monthly reports." in got.notes
    await session.close()


async def test_her_chat_lists_the_helpers_not_paused_and_may_hand_off(rt: Runtime) -> None:
    assert rt.helpers is not None
    first = await made(rt)
    second = await made(rt, "Travel", "Keeps the trip plans in order.")
    assert await rt.helpers.pause("local", second.id, NOW)
    provider = Recording(say("Hello."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "hello"})
    await idle(session)
    context = context_of(provider)
    assert '<untrusted_tool_result tool="helpers">' in context
    assert f'{first.id} "Reports": Sums up the weekly reports.' in context
    assert "Travel" not in context
    assert "one of Trillion's helpers" not in context
    offered = set(provider.offered[0])
    assert {CREATE, ASK} <= offered
    # Not yet: Task 6 decides when she offers one.
    assert SUGGEST not in offered
    assert not OWN & offered
    await session.close()


async def test_confirming_the_create_card_opens_the_new_chat(rt: Runtime) -> None:
    provider = Recording(
        use(CREATE, {"name": "Reports", "job": "Sums up the weekly reports."}, "c1"),
        say("Tap Create to make it."),
        say("It's ready."),
    )
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "I want a helper for my reports"})
    await page.until("confirm.request")
    await idle(session)
    assert not page.all("helper.created")
    card = page.all("confirm.request")[0]["pending_id"]
    await session.handle({"type": "confirm", "pending_id": card})
    await page.until("helper.created")
    await idle(session)
    assert rt.helpers is not None
    (h,) = await rt.helpers.all("local")
    (sent,) = page.all("helper.created")
    assert sent == {
        "type": "helper.created",
        "helper_id": h.id,
        "conversation_id": h.conversation_id,
    }
    await session.close()


async def test_confirming_by_words_opens_the_new_chat_too(rt: Runtime) -> None:
    provider = Recording(
        use(CREATE, {"name": "Reports", "job": "Sums up the weekly reports."}, "c1"),
        say("Shall I make it?"),
    )
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "I want a helper for my reports"})
    await page.until("confirm.request")
    await idle(session)
    card = page.all("confirm.request")[0]["pending_id"]
    provider._replies += [use("confirm_pending_action", {"pending_id": card}, "c2"), say("Done.")]
    await session.handle({"type": "turn.text", "text": "yes, make it"})
    await page.until("helper.created")
    await idle(session)
    assert rt.helpers is not None
    (h,) = await rt.helpers.all("local")
    assert page.all("helper.created")[0]["conversation_id"] == h.conversation_id
    await session.close()


async def test_a_page_opens_and_lists_a_helpers_chat_but_never_renames_it(rt: Runtime) -> None:
    h = await made(rt)
    hello = Msg(role="assistant", blocks=(TextBlock(text="Hi, I'm Reports."),))
    await rt.conversations.append(h.conversation_id, "local", "assistant", hello, NOW)
    assert await rt.conversations.exists(h.conversation_id, "local")
    rows, _ = await rt.conversations.list_chats("local")
    assert [(r.id, r.title, r.kind) for r in rows] == [(h.conversation_id, "Reports", "helper")]
    # Titled with its name, as the user's: her naming never replaces it.
    assert not await rt.conversations.set_title(h.conversation_id, "local", "Weekly", "model")
    # Nor does a page: its name changes with the helper's, and it goes with the helper.
    assert not await rt.conversations.set_title(h.conversation_id, "local", "Weekly", "user")
    assert not await rt.conversations.delete(h.conversation_id, "local", NOW)
    page = Page(rt)
    session = await Session.start(rt, page.send, h.conversation_id)
    (opened,) = page.all("session")
    assert (opened["id"], opened["title"]) == (h.conversation_id, "Reports")
    await session.close()


async def test_no_helper_tools_without_the_helpers_store(rt: Runtime) -> None:
    rt.helpers = None
    provider = Recording(say("Hello."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "hello"})
    await idle(session)
    assert not (DISPATCH | OWN) & set(provider.offered[0])
    assert 'tool="helpers"' not in context_of(provider)
    await session.close()
