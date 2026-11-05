"""What each helper is waiting on the user for (the helpers panel spec, Waiting on you):
a card raised in its conversation, or a step only the user can take."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.test_build import (
    NOW,
    OWNER,
    Schedules,
    Sources,
    ask,
    one,
    routine,
    run,
    watcher,
)
from trillion.helpers.build import helpers, snapshot
from trillion.helpers.model import Waiting, to_json
from trillion.spine.confirm import PendingAction, Status
from trillion.spine.contract import Risk
from trillion.spine.store import DecisionIn
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.spine_store import SqliteSpineStore


def card(**kw: Any) -> PendingAction:
    base = PendingAction(
        id="p1",
        owner_id=OWNER,
        tool="file_create",
        args={"path": "/x"},
        args_hash="h",
        summary="Create the revenue sheet",
        target="/x",
        risk=Risk.IRREVERSIBLE,
        recoverable=True,
        session_id="conv-r1",
        created_turn=1,
        status=Status.PROPOSED,
        created_at=NOW - 60_000,
        expires_at=NOW + 600_000,
        represented_from=None,
    )
    return dataclasses.replace(base, **kw)


async def test_a_card_in_a_routines_conversation_waits_on_that_routine() -> None:
    src = Sources(Schedules([routine(), routine(id="r2", conversation_id="conv-r2")]))
    src.open_cards = [card()]
    found = await helpers(src)
    h = one(found, "routine:r1")
    assert h.state == "needs_you"
    assert h.waiting == (
        Waiting(type="approval", id="p1", text="Approve a change", at_ms=NOW - 60_000),
    )
    assert one(found, "routine:r2").state == "idle"
    assert to_json(found)["needs_you"] == 1


async def test_needs_you_counts_helpers_not_cards() -> None:
    src = Sources(Schedules([routine()]))
    src.open_cards = [card(), card(id="p2", created_at=NOW - 1000)]
    found = await helpers(src)
    (h,) = found
    assert [w.id for w in h.waiting] == ["p1", "p2"]
    assert to_json(found)["needs_you"] == 1


async def test_needs_you_outranks_working_paused_and_capped() -> None:
    src = Sources(Schedules([routine(paused_ms=NOW)]), paused_work={"routines"})
    src.open_cards = [card()]
    (h,) = await helpers(src)
    assert h.state == "needs_you"
    busy = Sources(Schedules([routine()], now={"r1": run("r1", "running")}))
    busy.open_cards = [card()]
    (h,) = await helpers(busy)
    assert (h.state, h.doing, h.since_ms) == ("needs_you", None, None)


@pytest.mark.parametrize(
    "gone",
    [
        {"status": Status.APPROVED},
        {"status": Status.REJECTED},
        {"status": Status.EXPIRED},
        {"expires_at": NOW},
        {"owner_id": "someone"},
    ],
)
async def test_an_answered_or_lapsed_card_waits_on_nobody(gone: dict[str, Any]) -> None:
    src = Sources(Schedules([routine()]))
    src.open_cards = [card(**gone)]
    (h,) = await helpers(src)
    assert (h.state, h.waiting) == ("idle", ())


async def test_a_card_from_the_main_conversation_lands_on_no_helper() -> None:
    # A timed ask and a routine built in the day's conversation have no conversation
    # of their own here, so nothing raised elsewhere is theirs.
    src = Sources(
        Schedules([routine(), ask(), routine(id="r3", target="day", conversation_id=None)]),
        watching=[watcher("email")],
    )
    src.open_cards = [card(session_id="main-conv")]
    found = await helpers(src)
    assert all(h.waiting == () and h.state != "needs_you" for h in found)
    assert to_json(found)["needs_you"] == 0


@pytest.mark.parametrize(
    ("name", "connected", "error", "text"),
    [
        ("email", False, None, "Connect Google to use Urgent email"),
        ("payments", False, None, "Connect Stripe to use Payments"),
        ("meetings", True, "signed_out", "Sign in to Google again"),
        ("email", False, "signed_out", "Sign in to Google again"),
        # Stripe has no Google sign-in: signed out means nothing there.
        ("payments", False, "signed_out", "Connect Stripe to use Payments"),
    ],
)
async def test_a_watcher_on_but_not_connected_needs_a_step(
    name: str, connected: bool, error: str | None, text: str
) -> None:
    w = watcher(
        name,
        on=connected,
        connected=connected,
        last_error=error,
        last_error_ms=NOW - 5000 if error else None,
        next_check_ms=NOW + 60_000 if connected else None,
    )
    (h,) = await helpers(Sources(watching=[w]))
    assert h.state == "needs_you"
    signed_out = error == "signed_out" and name != "payments"
    at = NOW - 5000 if signed_out else None
    assert h.waiting == (Waiting(type="step", id=f"connect:{name}", text=text, at_ms=at),)


async def test_a_watcher_switched_off_is_paused_however_it_is_connected() -> None:
    off = watcher("email", on=False, switched_on=False, connected=False, last_error="signed_out")
    (h,) = await helpers(Sources(watching=[off]))
    assert (h.state, h.waiting) == ("paused", ())


async def test_needs_you_comes_first_oldest_first() -> None:
    src = Sources(
        Schedules([routine(), routine(id="r2", conversation_id="conv-r2")]),
        watching=[
            watcher("meetings", checking_since_ms=NOW - 1000),
            watcher("email", last_error="signed_out", last_error_ms=NOW - 30_000),
            watcher("payments", connected=False, on=False),
        ],
    )
    src.open_cards = [
        card(id="p1", created_at=NOW - 10_000),
        card(id="p2", session_id="conv-r2", created_at=NOW - 90_000),
    ]
    found = await helpers(src)
    # When a step began is not always known: those come after the ones that are.
    assert [h.id for h in found] == [
        "routine:r2",
        "watcher:email",
        "routine:r1",
        "watcher:payments",
        "watcher:meetings",
    ]


class Spine:
    """The real spine store, read as Task 3 will: the cards still proposed."""

    def __init__(self, store: SqliteSpineStore) -> None:
        self.store = store

    async def cards(self) -> Sequence[PendingAction]:
        return await self.store.list_by_status(Status.PROPOSED)


@pytest.fixture
async def spine(tmp_path: Path) -> AsyncIterator[Spine]:
    conn = await connect(tmp_path / "t.db")
    try:
        await migrate(conn)
        yield Spine(SqliteSpineStore(conn))
    finally:
        await conn.close()


async def test_answering_the_card_clears_it(spine: Spine) -> None:
    await spine.store.insert_pending(
        card(), DecisionIn(event="proposed", decided_by="conv-r1", via="text")
    )
    src = Sources(Schedules([routine()]))
    src.open_cards = list(await spine.cards())
    (h,) = await helpers(src)
    assert [w.id for w in h.waiting] == ["p1"]
    assert await spine.store.transition(
        "p1",
        Status.PROPOSED,
        Status.APPROVED,
        now_ms=NOW + 1,
        decision=DecisionIn(event="approved", decided_by="conv-r1", via="tap"),
    )
    src.open_cards = list(await spine.cards())
    (h,) = await helpers(src)
    assert (h.state, h.waiting) == ("idle", ())


async def test_meetings_with_heads_ups_off_still_hold_her_voice() -> None:
    # Heads-ups off, the meeting hold on: it still reads the calendar, so it runs.
    holding = watcher("meetings", switched_on=False, on=True)
    (h,) = await helpers(Sources(watching=[holding]))
    assert (h.state, h.job, h.waiting) == ("idle", "Only holds her voice during meetings", ())
    assert h.next_ms == NOW + 60_000
    (h,) = await helpers(
        Sources(watching=[dataclasses.replace(holding, checking_since_ms=NOW - 1000)])
    )
    assert h.state == "working"
    # The hold off too: it doesn't run at all.
    off = dataclasses.replace(holding, on=False, next_check_ms=None)
    (h,) = await helpers(Sources(watching=[off]))
    assert (h.state, h.job) == ("paused", "Tells you before a meeting starts")


async def test_a_card_in_a_watchers_own_conversation_waits_on_it() -> None:
    src = Sources(watching=[watcher("email")], talks={"watcher:email": "conv-w"})
    src.open_cards = [card(session_id="conv-w", summary="Watch mail from Sam")]
    (h,) = await helpers(src)
    assert (h.state, h.conversation_id) == ("needs_you", "conv-w")
    # Fixed words: the list goes to every page, and the card's own words stay in
    # the conversation it was raised in.
    assert [w.text for w in h.waiting] == ["Approve a change"]


async def test_the_list_says_when_the_earliest_open_card_lapses() -> None:
    src = Sources(Schedules([routine()]))
    assert (await snapshot(src))[1] is None
    src.open_cards = [
        card(id="p1", expires_at=NOW + 9000),
        card(id="p2", expires_at=NOW + 4000),
        card(id="p3", expires_at=NOW + 1000, status=Status.APPROVED),
        card(id="p4", session_id="main-conv", expires_at=NOW + 2000),
    ]
    # A card on no helper still lapses: the list may not change, but it is cheap.
    assert (await snapshot(src))[1] == NOW + 2000


async def test_meetings_not_connected_ask_nothing() -> None:
    # On from the start, once Calendar is connected: not connected is no step.
    waiting = watcher("meetings", on=False, connected=False, next_check_ms=None)
    (h,) = await helpers(Sources(watching=[waiting]))
    assert (h.state, h.waiting) == ("paused", ())
