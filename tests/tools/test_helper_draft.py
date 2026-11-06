"""A helper's card is a draft (the your helpers spec, Creating one): it carries notes, the
connections its job would use and the questions it asks first, all kept within their
limits; a new draft in the same conversation replaces the card still waiting; and nothing
is drafted after an outside read."""

from __future__ import annotations

import dataclasses
from typing import Any

from tests.tools.test_helper_tools import CTX, JOB, OWNER, Setup, call, in_chat, it, tap
from trillion.helpers.store import (
    ASK_MAX,
    ASKS_MAX,
    CONNECTOR_TITLES,
    CONNECTORS_MAX,
    NOTES_MAX,
)
from trillion.spine.confirm import Refusal, Status
from trillion.tools.helper import CREATE, UPDATE, card_draft

__all__ = ["it"]

NOTES = "Posts go out on weekdays. The audience is small business owners; keep it friendly."


async def test_the_draft_is_kept_on_create(it: Setup) -> None:
    args = {
        "name": "Socials",
        "job": "I help you grow your channels.",
        "notes": NOTES,
        "connectors": ["canva", "zapier"],
        "asks": ["Which channels matter most?", "How often should we post?"],
    }
    card = await it.router.execute(call(CREATE, args), CTX)
    assert card.status == "confirmation_required"
    assert card.summary == "New helper: Socials. I help you grow your channels."
    done = await it.router.resolve(card.pending_id or "", "confirm", tap())
    assert done.status == "succeeded"
    (h,) = await it.helpers.all(OWNER)
    assert h.notes == NOTES
    assert h.connectors == ("canva", "zapier")
    assert h.asks == ("Which channels matter most?", "How often should we post?")


async def test_unknown_connections_are_dropped_never_refused(it: Setup) -> None:
    args = {
        "name": "Socials",
        "job": JOB,
        "connectors": [
            "instagram",
            "Canva",
            "canva",
            "linkedin",
            "zapier",
            "hubspot",
            "notion",
            "miro",
        ],
    }
    card = await it.router.execute(call(CREATE, args), CTX)
    assert card.status == "confirmation_required"
    draft = card_draft(args)
    assert draft["connectors"] == ["canva", "zapier", "hubspot", "notion"]
    assert len(draft["connectors"]) == CONNECTORS_MAX
    await it.router.resolve(card.pending_id or "", "confirm", tap())
    (h,) = await it.helpers.all(OWNER)
    assert h.connectors == ("canva", "zapier", "hubspot", "notion")


async def test_notes_and_asks_are_cut_to_their_limits(it: Setup) -> None:
    long_notes = "word " * 400
    asks = ["Which one?", "x " * 200, "", "Third?", "Fourth?"]
    args = {"name": "Socials", "job": JOB, "notes": long_notes, "asks": asks}
    draft = card_draft(args)
    assert len(draft["notes"]) <= NOTES_MAX and draft["notes"].endswith("…")
    assert len(draft["asks"]) == ASKS_MAX
    assert all(0 < len(a) <= ASK_MAX for a in draft["asks"])
    assert draft["asks"][0] == "Which one?" and draft["asks"][2] == "Third?"
    card = await it.router.execute(call(CREATE, args), CTX)
    assert card.status == "confirmation_required"
    await it.router.resolve(card.pending_id or "", "confirm", tap())
    (h,) = await it.helpers.all(OWNER)
    assert h.notes == draft["notes"] and list(h.asks) == draft["asks"]


async def test_notes_are_one_plain_line(it: Setup) -> None:
    draft = card_draft({"name": "A", "job": JOB, "notes": "One.\nTwo.​\tThree."})
    assert draft["notes"] == "One. Two. Three."


async def test_nothing_is_drafted_after_an_outside_read(it: Setup) -> None:
    """A page she read must not write a helper's notes or connections: the draft is
    refused whole, as its name and job are."""
    ctx = dataclasses.replace(CTX, read_outside=True)
    args = {"name": "Socials", "job": JOB, "notes": NOTES, "connectors": ["zapier"]}
    r = await it.router.execute(call(CREATE, args), ctx)
    assert r.status == "refused" and r.pending_id is None
    assert not (await it.create.execute(args, ctx)).ok
    assert await it.helpers.all(OWNER) == []
    # Nor changed from its own chat after one.
    h = await it.made()
    up = {"helper": h.id, "notes": NOTES, "connectors": ["zapier"]}
    r = await it.router.execute(call(UPDATE, up), in_chat(h, read_outside=True))
    assert r.status == "refused"
    now = await it.helpers.get(OWNER, h.id)
    assert now is not None and (now.notes, now.connectors) == ("", ())


async def test_a_new_draft_replaces_the_card_still_waiting(it: Setup) -> None:
    first = await it.router.execute(call(CREATE, {"name": "Socials", "job": JOB}), CTX)
    later = dataclasses.replace(CTX, turn=2, call_id="t2")
    args = {"name": "Social Media", "job": JOB, "notes": NOTES}
    second = await it.router.execute(call(CREATE, args), later)
    assert first.pending_id and second.pending_id and first.pending_id != second.pending_id
    old = await it.router._store.get_pending(first.pending_id)
    assert old is not None and old.status is Status.SUPERSEDED
    proposed = [
        a for a in await it.router._store.list_by_status(Status.PROPOSED) if a.tool == CREATE
    ]
    assert [a.id for a in proposed] == [second.pending_id]
    # The old card can't be approved: nothing is made from it.
    after = dataclasses.replace(CTX, via="tap", turn=3)
    stale = await it.router.resolve(first.pending_id, "confirm", after)
    assert stale.reason is Refusal.SUPERSEDED
    assert await it.helpers.all(OWNER) == []
    done = await it.router.resolve(second.pending_id, "confirm", after)
    assert done.status == "succeeded"
    (h,) = await it.helpers.all(OWNER)
    assert (h.name, h.notes) == ("Social Media", NOTES)


async def test_a_new_draft_in_the_same_turn_replaces_it_too(it: Setup) -> None:
    first = await it.router.execute(call(CREATE, {"name": "Socials", "job": JOB}), CTX)
    again = await it.router.execute(call(CREATE, {"name": "Socials", "job": JOB}), CTX)
    # The same call again is the same card, as for any tool.
    assert again.already_asked and again.pending_id == first.pending_id
    other = await it.router.execute(call(CREATE, {"name": "Posts", "job": JOB}), CTX)
    assert other.pending_id != first.pending_id
    old = await it.router._store.get_pending(first.pending_id or "")
    assert old is not None and old.status is Status.SUPERSEDED


async def test_a_draft_in_another_conversation_is_its_own(it: Setup) -> None:
    first = await it.router.execute(call(CREATE, {"name": "Socials", "job": JOB}), CTX)
    elsewhere = dataclasses.replace(CTX, session_id="chat-2")
    await it.router.execute(call(CREATE, {"name": "Posts", "job": JOB}), elsewhere)
    old = await it.router._store.get_pending(first.pending_id or "")
    assert old is not None and old.status is Status.PROPOSED


async def test_its_draft_is_changed_in_its_own_chat(it: Setup) -> None:
    h = await it.made()
    args: dict[str, Any] = {
        "helper": h.id,
        "notes": "Only weekdays.",
        "connectors": ["stripe", "nope"],
        "asks": ["Which account?"],
    }
    card = await it.router.execute(call(UPDATE, args), in_chat(h))
    assert card.status == "confirmation_required"
    assert card.summary == (
        "Change Invoices: notes to Only weekdays., connections to Stripe, "
        "first questions to Which account?"
    )
    done = await it.router.resolve(card.pending_id or "", "confirm", tap(in_chat(h)))
    assert done.status == "succeeded"
    now = await it.helpers.get(OWNER, h.id)
    assert now is not None
    assert (now.notes, now.connectors, now.asks) == (
        "Only weekdays.",
        ("stripe",),
        ("Which account?",),
    )


def test_the_tool_lists_every_connection_it_may_suggest(it: Setup) -> None:
    schema = it.create.definition.input_schema["properties"]
    said = schema["connectors"]["description"]
    for cid, title in CONNECTOR_TITLES.items():
        assert f"{cid} ({title})" in said
    assert "never ask them questions first" in it.create.definition.description
    assert set(it.update.definition.input_schema["properties"]) >= {"notes", "connectors", "asks"}
