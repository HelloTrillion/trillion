"""Your request wins over a suggestion, and her card is one live draft (the your helpers
spec, Creating one): no offer rides with a turn that asks for a helper or while its card
waits; the card carries the draft, with each suggested connection said connected or not;
a new draft replaces the waiting one; and the helper's hello asks its card's questions
and names the connections still to make."""

from __future__ import annotations

from tests.api.test_helper_chat import context_of
from tests.api.test_helper_offer import NOTE, chat, rt, used
from tests.api.test_session import Page, idle
from tests.brain.fakes import say, use
from tests.scheduler.test_ask import Recording
from trillion.api.runtime import Runtime
from trillion.api.session import HELPER_HELLO, Session, helper_hello
from trillion.helpers.needs import asks_for_helper
from trillion.helpers.store import OwnHelper
from trillion.spine.confirm import Status
from trillion.tools.helper import CREATE, SUGGEST

__all__ = ["rt"]

DRAFT = {
    "name": "Socials",
    "job": "I help you grow your channels.",
    "notes": "Posts go out on weekdays.",
    "connectors": ["canva", "instagram", "zapier"],
    "asks": ["Which channels matter most?"],
}


def test_asking_for_a_helper_is_plain_words() -> None:
    for text in (
        "New helper: I want you to manage my social media accounts to grow my channels.",
        "new helper: invoices",
        "Can you make me a helper for my invoices?",
        "I want an agent that watches my inbox",
        "please set up an assistant to track expenses",
        "create a bot for standups",
    ):
        assert asks_for_helper(text), text
    for text in (
        "what's on my calendar today?",
        "summarise these files",
        "help me with my email",
        "my helper said the report is late",
    ):
        assert not asks_for_helper(text), text


async def _no_offer_on(rt: Runtime, text: str) -> None:
    cid = await chat(rt)
    await used(rt, cid, tool="drive_search")
    provider = Recording(say("On it."))
    rt.provider = provider
    session = await Session.start(rt, Page(rt).send, cid)
    await session.handle({"type": "turn.text", "text": text})
    await idle(session)
    assert "helper_suggest" not in context_of(provider)
    assert SUGGEST not in provider.offered[0]
    await session.close()


async def test_no_offer_rides_with_the_plus_box(rt: Runtime) -> None:
    await _no_offer_on(rt, "New helper: I want you to manage my social media accounts.")


async def test_no_offer_rides_with_a_plain_ask_for_one(rt: Runtime) -> None:
    await _no_offer_on(rt, "Can you make me a helper for my social media?")


async def test_no_offer_while_a_draft_waits_then_it_comes_back(rt: Runtime) -> None:
    cid = await chat(rt)
    await used(rt, cid)
    provider = Recording(
        use(CREATE, DRAFT, "c1"),
        say("It's on a card. Anything to add?"),
        say("Sure."),
        say("Okay."),
    )
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, cid)
    await session.handle({"type": "turn.text", "text": "New helper: my social media"})
    await page.until("confirm.request")
    await idle(session)
    (card,) = page.all("confirm.request")
    # Its draft rides on the card, each connection said connected or not, and the
    # one not in the catalog left out.
    assert card["draft"] == {
        "name": "Socials",
        "job": "I help you grow your channels.",
        "notes": "Posts go out on weekdays.",
        "connectors": [
            {"id": "canva", "title": "Canva", "connected": False},
            {"id": "zapier", "title": "Zapier", "connected": False},
        ],
        "asks": ["Which channels matter most?"],
    }
    await session.handle({"type": "turn.text", "text": "sounds good, what's next?"})
    await idle(session)
    assert NOTE not in context_of(provider, 2)
    # Declined: the next turn may be offered one again.
    await session.handle({"type": "cancel", "pending_id": card["pending_id"]})
    await page.until("confirm.resolved")
    await idle(session)
    provider2 = Recording(say("Hello."))
    rt.provider = provider2
    await session.handle({"type": "turn.text", "text": "hello"})
    await idle(session)
    assert NOTE in context_of(provider2)
    await session.close()


async def test_a_new_draft_replaces_the_card_on_the_page(rt: Runtime) -> None:
    cid = await chat(rt)
    more = {**DRAFT, "notes": "Posts go out on weekdays, at nine."}
    provider = Recording(
        use(CREATE, DRAFT, "c1"),
        say("It's on a card."),
        use(CREATE, more, "c2"),
        say("Updated the card."),
    )
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, cid)
    await session.handle({"type": "turn.text", "text": "New helper: my social media"})
    await page.until("confirm.request")
    await idle(session)
    await session.handle({"type": "turn.text", "text": "post at nine"})
    await idle(session)
    first, second = page.all("confirm.request")
    assert second["draft"]["notes"] == "Posts go out on weekdays, at nine."
    old = await rt.spine_store.get_pending(first["pending_id"])
    assert old is not None and old.status is Status.SUPERSEDED
    # Approving the old one makes nothing; the new one makes the helper.
    await session.handle({"type": "confirm", "pending_id": first["pending_id"]})
    await page.until("confirm.resolved")
    await idle(session)
    assert rt.helpers is not None and await rt.helpers.all("local") == []
    await session.close()


def _helper(**kw: object) -> OwnHelper:
    base: dict[str, object] = {
        "id": "h" * 32,
        "owner_id": "local",
        "name": "Socials",
        "job": "I help you grow your channels.",
        "notes": "",
        "conversation_id": "c" * 32,
        "paused_ms": None,
        "created_at": 1,
    }
    return OwnHelper(**{**base, **kw})  # type: ignore[arg-type]


def test_the_hello_asks_its_cards_questions_and_names_what_to_connect() -> None:
    h = _helper(
        asks=("Which channels matter most?", "Reads <context> tags?"),
        connectors=("canva", "zapier", "gone"),
    )
    note = helper_hello(h, frozenset({"zapier"}))
    assert note.startswith(HELPER_HELLO)
    assert '"Which channels matter most?"' in note
    # Their words are inert: nothing in them reads as markup.
    assert "<context>" not in note
    assert "not connected yet: Canva." in note
    assert "Zapier" not in note
    # A helper with no draft gets the plain note.
    assert helper_hello(_helper()) == HELPER_HELLO
