"""She offers a helper once when a need repeats (the your helpers spec, Suggestions):
the host note rides only on the turn it is due, helper_suggest is offered only then, and
the page's Create or No thanks is taken only for the offer that is open."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_helper_chat import context_of, made
from tests.api.test_session import Page, idle
from tests.brain.fakes import say, use
from tests.scheduler.conftest import FakeClock, ms
from tests.scheduler.test_ask import Recording
from trillion.api.runtime import Runtime, open_runtime
from trillion.api.session import Session
from trillion.helpers.store import MAX_HELPERS
from trillion.secrets.store import MemoryStore
from trillion.spine.audit import AuditRecord
from trillion.tools.helper import DECLINED_PREF, OFFER_PREF, SUGGEST, first_line

NOW = ms("2026-11-05T09:00")
DAY = 24 * 3600 * 1000
NOTE = "After answering, offer the user a helper for email with helper_suggest"
OFFER = {"area": "email", "name": "Inbox", "job": "Keeps your inbox in order."}


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


async def used(
    rt: Runtime, conversation_id: str, tool: str = "gmail_search", days: int = 3
) -> None:
    """The tool ran in this conversation once on each of the last `days` days."""
    for d in range(days):
        await rt.spine_store.append_audit(
            AuditRecord(
                owner_id="local",
                call_id=f"{tool}-{d}",
                tool=tool,
                args_hash="h",
                risk="read",
                decision="executed",
                outcome="succeeded",
                duration_ms=1,
                session_id=conversation_id,
                at=NOW - (days - d) * DAY,
            )
        )


async def chat(rt: Runtime, name: str = "c" * 32) -> str:
    await rt.conversations.create(name, "local", NOW - 30 * DAY)
    return name


async def offered_turn(rt: Runtime) -> tuple[Session, Page, Recording]:
    """A chat where email repeated, and her turn there that offers a helper for it."""
    cid = await chat(rt)
    await used(rt, cid)
    provider = Recording(use(SUGGEST, OFFER, "c1"), say("Done."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, cid)
    await session.handle({"type": "turn.text", "text": "anything new in my inbox?"})
    await page.until("helper.offer")
    await idle(session)
    return session, page, provider


async def test_a_repeated_need_gets_one_offer(rt: Runtime) -> None:
    session, page, provider = await offered_turn(rt)
    assert NOTE in context_of(provider)
    assert SUGGEST in provider.offered[0]
    assert page.all("helper.offer") == [{"type": "helper.offer", **OFFER}]
    saved = await rt.preferences.get("local", OFFER_PREF)
    assert saved == {**OFFER, "at_ms": NOW}
    # The next turn, the same day: no note, and no offering it.
    provider2 = Recording(say("Sure."))
    rt.provider = provider2
    await session.handle({"type": "turn.text", "text": "thanks"})
    await idle(session)
    assert "helper_suggest" not in context_of(provider2)
    assert SUGGEST not in provider2.offered[0]
    await session.close()


async def test_no_note_without_a_repeated_need(rt: Runtime) -> None:
    cid = await chat(rt)
    await used(rt, cid, days=2)
    provider = Recording(use(SUGGEST, OFFER, "c1"), say("Hello."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, cid)
    await session.handle({"type": "turn.text", "text": "hello"})
    await idle(session)
    assert "helper_suggest" not in context_of(provider)
    assert SUGGEST not in provider.offered[0]
    # Called anyway, it is refused, and nothing is offered.
    assert not page.all("helper.offer")
    assert await rt.preferences.get("local", OFFER_PREF) is None
    await session.close()


@pytest.mark.parametrize("stop", ["covered", "declined", "today"])
async def test_covered_declined_and_once_a_day_stop_the_note(rt: Runtime, stop: str) -> None:
    cid = await chat(rt)
    await used(rt, cid)
    if stop == "covered":
        await made(rt, "Mail", "Sorts the user's email.")
    elif stop == "declined":
        await rt.preferences.put("local", DECLINED_PREF, ["email"], NOW)
    else:
        await rt.preferences.put("local", OFFER_PREF, {"area": "web", "at_ms": NOW - 3600}, NOW)
    provider = Recording(say("Hello."))
    rt.provider = provider
    session = await Session.start(rt, Page(rt).send, cid)
    await session.handle({"type": "turn.text", "text": "hello"})
    await idle(session)
    assert "helper_suggest" not in context_of(provider)
    assert SUGGEST not in provider.offered[0]
    await session.close()


async def test_a_helpers_chat_gets_no_note(rt: Runtime) -> None:
    h = await made(rt)
    await used(rt, h.conversation_id)
    cid = await chat(rt)
    await used(rt, cid, days=2)
    provider = Recording(say("Hello."), say("Hello."))
    rt.provider = provider
    # Its own chat counts for nothing, and gets no note of its own either.
    for where in (cid, h.conversation_id):
        session = await Session.start(rt, Page(rt).send, where)
        await session.handle({"type": "turn.text", "text": "hello"})
        await idle(session)
        await session.close()
    assert "helper_suggest" not in context_of(provider, 0)
    assert "helper_suggest" not in context_of(provider, 1)


async def test_a_failed_look_at_the_audit_leaves_the_turn_alone(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid = await chat(rt)

    async def broken(*_a: Any) -> list[Any]:
        raise RuntimeError("disk")

    monkeypatch.setattr(rt.spine_store, "recent_tools", broken)
    provider = Recording(say("Hello."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, cid)
    await session.handle({"type": "turn.text", "text": "hello"})
    await idle(session)
    assert page.all("reply.delta")
    assert "helper_suggest" not in context_of(provider)
    await session.close()


async def test_create_from_the_page_makes_the_helper(rt: Runtime) -> None:
    session, page, _ = await offered_turn(rt)
    await session.handle({"type": "helper.offer", "area": "email", "choice": "create"})
    await page.until("helper.created")
    assert rt.helpers is not None
    (h,) = await rt.helpers.all("local")
    assert (h.name, h.job) == ("Inbox", "Keeps your inbox in order.")
    assert page.all("helper.created") == [
        {"type": "helper.created", "helper_id": h.id, "conversation_id": h.conversation_id}
    ]
    # Its first line is in its chat, as a card's would be.
    history = await rt.conversations.history(h.conversation_id, "local")
    assert first_line("Inbox", "Keeps your inbox in order.") in str(history)
    # Answered: the once-a-day mark stays, the offer itself does not.
    assert await rt.preferences.get("local", OFFER_PREF) == {"area": "email", "at_ms": NOW}
    # A second tap is stale: nothing more is made.
    await session.handle({"type": "helper.offer", "area": "email", "choice": "create"})
    assert len(await rt.helpers.all("local")) == 1
    assert len(page.all("helper.created")) == 1
    await session.close()


@pytest.mark.parametrize(
    "answer",
    [
        {"area": "web", "choice": "create"},
        {"area": "email", "choice": "yes"},
        {"area": 3, "choice": "create"},
        {"choice": "create"},
    ],
)
async def test_a_forged_answer_does_nothing(rt: Runtime, answer: dict[str, Any]) -> None:
    session, _page, _ = await offered_turn(rt)
    await session.handle({"type": "helper.offer", **answer})
    assert rt.helpers is not None
    assert await rt.helpers.all("local") == []
    assert await rt.preferences.get("local", DECLINED_PREF) is None
    assert await rt.preferences.get("local", OFFER_PREF) == {**OFFER, "at_ms": NOW}
    await session.close()


async def test_an_answer_with_no_offer_does_nothing(rt: Runtime) -> None:
    session = await Session.start(rt, Page(rt).send, await chat(rt))
    await session.handle({"type": "helper.offer", "area": "email", "choice": "create"})
    await session.handle({"type": "helper.offer", "area": "email", "choice": "decline"})
    assert rt.helpers is not None
    assert await rt.helpers.all("local") == []
    assert await rt.preferences.get("local", DECLINED_PREF) is None
    await session.close()


async def test_decline_is_kept(rt: Runtime) -> None:
    session, _page, _ = await offered_turn(rt)
    await session.handle({"type": "helper.offer", "area": "email", "choice": "decline"})
    assert await rt.preferences.get("local", DECLINED_PREF) == ["email"]
    assert await rt.preferences.get("local", OFFER_PREF) == {"area": "email", "at_ms": NOW}
    assert rt.helpers is not None
    assert await rt.helpers.all("local") == []
    # A day later, email still repeats: it is never offered again.
    assert isinstance(rt.clock, FakeClock)
    rt.clock.now = NOW + 2 * DAY
    provider = Recording(say("Hello."))
    rt.provider = provider
    await session.handle({"type": "turn.text", "text": "hello"})
    await idle(session)
    assert "helper_suggest" not in context_of(provider)
    await session.close()


@pytest.mark.parametrize("why", ["full", "taken"])
async def test_a_create_that_cannot_be_made_says_why(rt: Runtime, why: str) -> None:
    session, page, _ = await offered_turn(rt)
    if why == "taken":
        await made(rt, "Inbox", "Something else entirely.")
    else:
        for n in range(MAX_HELPERS):
            await made(rt, f"Helper {n}", "Does one thing.")
    assert rt.helpers is not None
    before = len(await rt.helpers.all("local"))
    await session.handle({"type": "helper.offer", "area": "email", "choice": "create"})
    assert page.all("helper.offer.failed") == [{"type": "helper.offer.failed", "reason": why}]
    assert len(await rt.helpers.all("local")) == before
    assert not page.all("helper.created")
    assert await rt.preferences.get("local", DECLINED_PREF) is None
    await session.close()
