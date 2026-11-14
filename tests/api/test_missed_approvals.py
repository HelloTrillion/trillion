"""Missed approvals under Needs you (SPN-17, SPN-2b, SUR-7): a card that expired before
the user answered it is sent to the page on its hello, one line each, with Ask again
and Dismiss. The lines are the app's: nothing reaches her, nothing is spoken or saved
as hers, and she never raises one again on her own."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_session import Page, idle
from tests.brain.fakes import ScriptedProvider, say, use
from trillion.api import missed
from trillion.api.runtime import Runtime, open_runtime
from trillion.api.session import Session
from trillion.secrets.store import MemoryStore
from trillion.spine.confirm import EXPIRY_MS, Status
from trillion.spine.contract import ToolCall, ToolContext
from trillion.spine.router import MISSED_MS
from trillion.spine.store import DISMISSED


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
        env={},
    )
    yield runtime
    await runtime.close()


class Clock:
    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


def router_clock(rt: Runtime) -> Clock:
    """The router's clock, moved by the test: cards expire without waiting."""
    clock = Clock(rt.clock())
    rt.router._clock = clock  # type: ignore[attr-defined]
    return clock


async def ask_card(rt: Runtime, name: str) -> tuple[str, str]:
    """She asks to create a file and the card is shown. (conversation id, pending id)"""
    path = str(rt.granted_roots[0] / name)
    rt.provider = ScriptedProvider(
        use("file_create", {"path": path, "content": "x"}, "c1"), say("Shall I?")
    )
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "make it"})
    await page.until("confirm.request")
    await idle(session)
    await session.close()
    (card,) = page.all("confirm.request")
    return session.id, str(card["pending_id"])


async def open_page(rt: Runtime, resume: str | None = None) -> tuple[Session, Page]:
    page = Page(rt)
    session = await Session.start(rt, page.send, resume)
    return session, page


def listed(page: Page) -> list[dict[str, Any]]:
    (msg,) = page.all("unseen")
    return list(msg["items"])


async def done(page: Page) -> dict[str, Any]:
    await page.until("unseen.done")
    return page.all("unseen.done")[-1]


async def test_the_page_is_sent_each_missed_approval_on_its_hello(rt: Runtime) -> None:
    clock = router_clock(rt)
    chat, pid = await ask_card(rt, "late.txt")
    session, page = await open_page(rt)
    # Still waiting: a card, not a missed approval.
    assert listed(page) == []
    await session.close()
    clock.now += EXPIRY_MS + 1
    provider = ScriptedProvider()
    rt.provider = provider
    session, page = await open_page(rt)
    (item,) = listed(page)
    a = await rt.spine_store.get_pending(pid)
    assert a is not None
    assert item == {
        "pending_id": pid,
        "tool_label": "Create a file",
        "summary": a.summary,
        "expired_at": a.expires_at,
        "shown": True,
        "conversation_id": chat,
        "why_not": None,
    }
    await idle(session)
    # The app's line: she was asked nothing, and nothing was saved in any conversation.
    assert provider.calls == []
    assert page.all("audio.start") == [] and page.all("turn.start") == []
    history = await rt.conversations.history(session.id, "local")
    assert history == []
    await session.close()


async def test_one_from_more_than_7_days_ago_is_not_listed(rt: Runtime) -> None:
    clock = router_clock(rt)
    await ask_card(rt, "old.txt")
    clock.now += EXPIRY_MS + MISSED_MS + 1
    session, page = await open_page(rt)
    assert listed(page) == []
    await session.close()


async def test_ask_again_raises_a_fresh_card_in_its_own_conversation(rt: Runtime) -> None:
    clock = router_clock(rt)
    chat, pid = await ask_card(rt, "again.txt")
    clock.now += EXPIRY_MS + 1
    provider = ScriptedProvider()
    rt.provider = provider
    session, page = await open_page(rt, resume=chat)
    assert [i["pending_id"] for i in listed(page)] == [pid]
    await session.handle({"type": "unseen.ask", "pending_id": pid})
    assert await done(page) == {
        "type": "unseen.done",
        "pending_id": pid,
        "outcome": "asked",
        "text": None,
    }
    (card,) = [c for c in page.all("confirm.request") if c["pending_id"] != pid]
    fresh = await rt.spine_store.get_pending(str(card["pending_id"]))
    assert fresh is not None and fresh.status is Status.PROPOSED
    assert fresh.represented_from == pid and fresh.session_id == chat
    await idle(session)
    # The user's tap on the page: she is told nothing and asked nothing.
    assert provider.calls == []
    await session.close()
    # Asked again: it never shows again.
    session, page = await open_page(rt)
    assert listed(page) == []
    await session.close()


async def test_ask_again_from_another_conversation_raises_nothing(rt: Runtime) -> None:
    clock = router_clock(rt)
    _chat, pid = await ask_card(rt, "elsewhere.txt")
    clock.now += EXPIRY_MS + 1
    session, page = await open_page(rt)
    await session.handle({"type": "unseen.ask", "pending_id": pid})
    got = await done(page)
    assert got["outcome"] == "refused" and "Open the conversation" in str(got["text"])
    assert [c for c in page.all("confirm.request") if c["pending_id"] != pid] == []
    await session.close()
    # Not asked again: still listed.
    session, page = await open_page(rt)
    assert [i["pending_id"] for i in listed(page)] == [pid]
    await session.close()


async def test_dismiss_is_final(rt: Runtime) -> None:
    clock = router_clock(rt)
    _chat, pid = await ask_card(rt, "dismiss.txt")
    clock.now += EXPIRY_MS + 1
    session, page = await open_page(rt)
    await session.handle({"type": "unseen.dismiss", "pending_id": pid})
    assert (await done(page))["outcome"] == "dismissed"
    events = [d.event for d in await rt.spine_store.list_decisions(pid)]
    assert events[-1] == DISMISSED
    # Neither again, nor Ask again after it.
    await session.handle({"type": "unseen.dismiss", "pending_id": pid})
    await page.until("unseen.done", 2)
    assert page.all("unseen.done")[-1]["outcome"] == "gone"
    await session.handle({"type": "unseen.ask", "pending_id": pid})
    await page.until("unseen.done", 3)
    assert page.all("unseen.done")[-1] == {
        "type": "unseen.done",
        "pending_id": pid,
        "outcome": "gone",
        "text": missed.GONE,
    }
    await session.close()
    session, page = await open_page(rt)
    assert listed(page) == []
    await session.close()


async def test_her_browsers_card_offers_only_dismiss_with_the_reason(rt: Runtime) -> None:
    clock = router_clock(rt)
    ctx = ToolContext(
        owner_id="local",
        session_id="browse:task1",
        turn=1,
        call_id="b1",
        via="text",
        granted_roots=rt.granted_roots,
    )
    path = str(rt.granted_roots[0] / "form.txt")
    r = await rt.router.dispatch(
        ToolCall(name="file_create", args={"path": path, "content": "x"}), ctx
    )
    assert r.status == "confirmation_required" and r.pending_id is not None
    clock.now += EXPIRY_MS + 1
    session, page = await open_page(rt)
    (item,) = listed(page)
    assert item["pending_id"] == r.pending_id and item["shown"] is False
    assert item["conversation_id"] is None and item["why_not"] == missed.BROWSER_ENDED
    await session.handle({"type": "unseen.ask", "pending_id": r.pending_id})
    got = await done(page)
    assert got == {
        "type": "unseen.done",
        "pending_id": r.pending_id,
        "outcome": "refused",
        "text": missed.BROWSER_ENDED,
    }
    await session.handle({"type": "unseen.dismiss", "pending_id": r.pending_id})
    await page.until("unseen.done", 2)
    assert page.all("unseen.done")[-1]["outcome"] == "dismissed"
    await session.close()


async def test_a_deleted_conversations_card_cannot_be_asked_again(rt: Runtime) -> None:
    clock = router_clock(rt)
    chat, _pid = await ask_card(rt, "deleted.txt")
    await rt.conversations.delete(chat, "local", rt.clock())
    clock.now += EXPIRY_MS + 1
    session, page = await open_page(rt)
    (item,) = listed(page)
    assert item["why_not"] == missed.CHAT_GONE and item["conversation_id"] is None
    await session.close()


async def test_a_bad_message_is_refused(rt: Runtime) -> None:
    session, page = await open_page(rt)
    await session.handle({"type": "unseen.ask", "pending_id": 5})
    await page.until("error")
    assert page.all("unseen.done") == []
    await session.close()


async def test_a_list_that_cannot_be_read_never_stops_the_page(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(_rt: Runtime) -> list[dict[str, Any]]:
        raise RuntimeError("no")

    monkeypatch.setattr(missed, "items", broken)
    session, page = await open_page(rt)
    assert listed(page) == []
    await session.close()


def test_refused_words_are_plain() -> None:
    from trillion.spine.confirm import Refusal
    from trillion.spine.router import RouterResponse

    def words(reason: Refusal | None, detail: str = "") -> str:
        return missed.refused_words(RouterResponse(status="refused", reason=reason, detail=detail))

    assert (
        words(Refusal.PRECONDITION_FAILED, "it exists") == "It can't be done as it was: it exists"
    )
    assert words(Refusal.PRECONDITION_FAILED) == missed.COULD_NOT
    assert words(Refusal.UNKNOWN_TOOL) == missed.TOOL_GONE
    assert words(Refusal.LOOP_GUARD) == missed.COULD_NOT
    assert words(None) == missed.COULD_NOT
