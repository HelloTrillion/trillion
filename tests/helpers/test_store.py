"""The helpers you make (the your helpers spec): the store, with its own conversation
per helper, the limit, unique names, notes, pause and delete with its schedules."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.scheduler.conftest import ms, runs, schedule
from trillion.helpers.pausing import pause_helper, resume_helper
from trillion.helpers.store import MAX_HELPERS, OwnHelper
from trillion.scheduler import runner
from trillion.scheduler.store import NOTES_MAX, Schedule
from trillion.scheduler.when import Repeat
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.conversation_store import SqliteConversationStore
from trillion.storage.sqlite.helper_store import SqliteHelperStore
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore

OWNER = "local"
NOW = ms("2026-11-05T09:00")
JOB = "Keeps track of who owes you and chases late payments."


@dataclass
class Stores:
    helpers: SqliteHelperStore
    schedules: SqliteScheduleStore
    conversations: SqliteConversationStore
    told: list[str]
    gone: list[str]


@pytest.fixture
async def stores(tmp_path: Path) -> AsyncIterator[Stores]:
    path = tmp_path / "t.db"
    conns = [await connect(path) for _ in range(3)]
    try:
        await migrate(conns[0])
        told: list[str] = []
        gone: list[str] = []

        async def cancelled(schedule_id: str) -> None:
            gone.append(schedule_id)

        helpers = SqliteHelperStore(
            conns[0], changed=lambda: told.append("changed"), cancelled=cancelled
        )
        yield Stores(
            helpers,
            SqliteScheduleStore(conns[1]),
            SqliteConversationStore(conns[2]),
            told,
            gone,
        )
    finally:
        for c in conns:
            await c.close()


async def made(s: Stores, name: str = "Invoices", job: str = JOB) -> OwnHelper:
    h = await s.helpers.create(OWNER, name, job, NOW)
    assert isinstance(h, OwnHelper)
    return h


def handoff(helper_id: str, **kw: Any) -> Schedule:
    """A one-off ask for a helper, due now."""
    base: dict[str, Any] = {
        "id": "h1",
        "kind": "ask",
        "title": "Invoices",
        "body": "Which invoices are overdue?",
        "at_local": "2026-11-05T09:00",
        "next_due_ms": NOW,
        "helper_id": helper_id,
    }
    return schedule(**(base | kw))


def its_routine(helper_id: str, **kw: Any) -> Schedule:
    base: dict[str, Any] = {
        "id": "r1",
        "kind": "ask",
        "title": "Overdue on Monday",
        "body": "Check what's overdue.",
        "at_local": "2026-11-09T08:00",
        "repeat": Repeat(every="week", days=("mon",)),
        "next_due_ms": ms("2026-11-09T08:00"),
        "target": "own",
        "helper_id": helper_id,
    }
    return schedule(**(base | kw))


async def test_create_makes_the_helper_and_its_conversation(stores: Stores) -> None:
    h = await made(stores)
    assert (h.owner_id, h.name, h.job, h.notes, h.paused_ms, h.created_at) == (
        OWNER,
        "Invoices",
        JOB,
        "",
        None,
        NOW,
    )
    assert await stores.helpers.get(OWNER, h.id) == h
    assert await stores.helpers.get("someone", h.id) is None
    assert await stores.helpers.by_conversation(OWNER, h.conversation_id) == h
    assert await stores.conversations.kind_of(h.conversation_id, OWNER) == ("helper", None)
    # Titled with its name, as the user's, so her naming never replaces it.
    assert await stores.conversations.title_of(h.conversation_id, OWNER) == ("Invoices", "user")
    assert stores.told


async def test_all_lists_oldest_first(stores: Stores) -> None:
    a = await made(stores, "Invoices")
    b = await stores.helpers.create(OWNER, "Inbox", "Sorts the inbox.", NOW + 1)
    assert isinstance(b, OwnHelper)
    assert [h.id for h in await stores.helpers.all(OWNER)] == [a.id, b.id]
    assert await stores.helpers.all("someone") == []


async def test_the_21st_is_refused_as_full(stores: Stores) -> None:
    for i in range(MAX_HELPERS):
        await made(stores, f"Helper {i}")
    assert await stores.helpers.create(OWNER, "One more", JOB, NOW) == "full"
    assert len(await stores.helpers.all(OWNER)) == MAX_HELPERS


async def test_a_name_is_taken_in_any_case(stores: Stores) -> None:
    await made(stores, "Invoices")
    assert await stores.helpers.create(OWNER, "INVOICES", JOB, NOW) == "taken"
    assert await stores.helpers.create(OWNER, "invoices", JOB, NOW) == "taken"
    # Refused without leaving a conversation behind.
    async with stores.helpers._conn.execute(
        "SELECT count(*) FROM conversations WHERE kind = 'helper'"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] == 1


async def test_rename_renames_its_conversation(stores: Stores) -> None:
    h = await made(stores)
    other = await made(stores, "Inbox")
    assert await stores.helpers.update(OWNER, h.id, name="inbox") == "taken"
    renamed = await stores.helpers.update(OWNER, h.id, name="Billing")
    assert isinstance(renamed, OwnHelper)
    assert (renamed.name, renamed.job) == ("Billing", JOB)
    assert await stores.conversations.title_of(h.conversation_id, OWNER) == ("Billing", "user")
    # Its own name in another case is not taken by itself.
    again = await stores.helpers.update(OWNER, h.id, name="BILLING", job="Sends bills.")
    assert isinstance(again, OwnHelper) and (again.name, again.job) == ("BILLING", "Sends bills.")
    # A new job alone leaves the conversation's title.
    await stores.helpers.update(OWNER, other.id, job="Sorts the inbox.")
    assert await stores.conversations.title_of(other.conversation_id, OWNER) == ("Inbox", "user")
    assert await stores.helpers.update(OWNER, "nope", name="X") is None
    assert await stores.helpers.update("someone", h.id, name="X") is None


async def test_notes_grow_a_line_at_a_time_until_full(stores: Stores) -> None:
    h = await made(stores)
    assert await stores.helpers.add_note(OWNER, h.id, "Leave out refunds.")
    assert await stores.helpers.add_note(OWNER, h.id, "Only the business account.")
    got = await stores.helpers.get(OWNER, h.id)
    assert got is not None and got.notes == "Leave out refunds.\nOnly the business account."
    long = "x" * (NOTES_MAX - len(got.notes) - 1)
    assert await stores.helpers.add_note(OWNER, h.id, long)
    assert not await stores.helpers.add_note(OWNER, h.id, "y")
    assert not await stores.helpers.add_note(OWNER, "nope", "y")


async def test_pause_and_resume_the_row(stores: Stores) -> None:
    h = await made(stores)
    assert await stores.helpers.pause(OWNER, h.id, NOW)
    assert not await stores.helpers.pause(OWNER, h.id, NOW + 1)  # already
    got = await stores.helpers.get(OWNER, h.id)
    assert got is not None and got.paused_ms == NOW
    assert await stores.helpers.resume(OWNER, h.id)
    assert not await stores.helpers.resume(OWNER, h.id)
    got = await stores.helpers.get(OWNER, h.id)
    assert got is not None and got.paused_ms is None


async def test_a_schedule_carries_its_helper(stores: Stores) -> None:
    h = await made(stores)
    await stores.schedules.create(handoff(h.id))
    await stores.schedules.create(schedule(id="plain"))
    s = await stores.schedules.get("h1")
    assert s is not None and s.helper_id == h.id
    plain = await stores.schedules.get("plain")
    assert plain is not None and plain.helper_id is None
    assert [x.id for x in await stores.schedules.for_helper(OWNER, h.id)] == ["h1"]
    assert await stores.schedules.for_helper("someone", h.id) == []


async def test_cancel_for_helper(stores: Stores) -> None:
    h = await made(stores)
    await stores.schedules.create(handoff(h.id))
    await stores.schedules.create(its_routine(h.id))
    await stores.schedules.create(schedule(id="plain"))
    assert sorted(await stores.schedules.cancel_for_helper(OWNER, h.id, NOW)) == ["h1", "r1"]
    for sid in ("h1", "r1"):
        s = await stores.schedules.get(sid)
        assert s is not None and s.status == "cancelled"
    plain = await stores.schedules.get("plain")
    assert plain is not None and plain.status == "active"


async def test_pausing_a_helper_pauses_its_routines_and_cancels_waiting_asks(
    stores: Stores,
) -> None:
    h = await made(stores)
    await stores.schedules.create(its_routine(h.id))
    await stores.schedules.create(handoff(h.id))
    # A handoff already queued, not yet run, is cancelled too.
    await stores.schedules.create(handoff(h.id, id="h2"))
    assert await stores.schedules.enqueue("h2", NOW, None, 60.0, NOW)
    assert await pause_helper(stores.helpers, stores.schedules, OWNER, h.id, NOW)
    got = await stores.helpers.get(OWNER, h.id)
    assert got is not None and got.paused_ms == NOW
    r = await stores.schedules.get("r1")
    assert r is not None and r.paused_ms == NOW and r.status == "active"
    for sid in ("h1", "h2"):
        s = await stores.schedules.get(sid)
        assert s is not None and s.status == "cancelled"
    assert [x.status for x in await runs(stores.schedules, "h2")] == ["cancelled"]
    # Already paused: nothing more happens.
    assert not await pause_helper(stores.helpers, stores.schedules, OWNER, h.id, NOW)


async def test_resuming_a_helper_resumes_its_routines_from_now(stores: Stores) -> None:
    h = await made(stores)
    await stores.schedules.create(its_routine(h.id))
    await pause_helper(stores.helpers, stores.schedules, OWNER, h.id, NOW)
    # Resumed weeks later: the Mondays it was paused through are never replayed.
    later = ms("2026-11-25T12:00")
    assert await resume_helper(stores.helpers, stores.schedules, runner, OWNER, h.id, later)
    got = await stores.helpers.get(OWNER, h.id)
    assert got is not None and got.paused_ms is None
    r = await stores.schedules.get("r1")
    assert r is not None and r.paused_ms is None
    assert r.next_due_ms == ms("2026-11-30T08:00") and r.next_due_ms > later
    assert not await resume_helper(stores.helpers, stores.schedules, runner, OWNER, h.id, later)


async def test_delete_cancels_its_schedules_and_keeps_its_chat(stores: Stores) -> None:
    h = await made(stores)
    await stores.schedules.create(its_routine(h.id))
    await stores.schedules.create(handoff(h.id))
    stores.told.clear()
    assert await stores.helpers.delete(OWNER, h.id, NOW)
    assert stores.told
    assert await stores.helpers.get(OWNER, h.id) is None
    assert await stores.helpers.all(OWNER) == []
    assert await stores.helpers.by_conversation(OWNER, h.conversation_id) is None
    for sid in ("h1", "r1"):
        s = await stores.schedules.get(sid)
        # Cancelled, and still its, for the runs' history: the row is kept (SPN-9).
        assert s is not None and s.status == "cancelled" and s.helper_id == h.id
    # The routine's own things go with it, as when a routine is cancelled.
    assert stores.gone == ["r1"]
    assert await stores.conversations.kind_of(h.conversation_id, OWNER) == ("chat", None)
    assert not await stores.helpers.delete(OWNER, h.id, NOW)
    # Its name is free again.
    assert isinstance(await stores.helpers.create(OWNER, "Invoices", JOB, NOW), OwnHelper)


async def test_a_name_is_taken_in_any_case_beyond_ascii(stores: Stores) -> None:
    await made(stores, "Émile")
    assert await stores.helpers.create(OWNER, "ÉMILE", JOB, NOW) == "taken"
    other = await made(stores, "Straße")
    assert await stores.helpers.update(OWNER, other.id, name="émile") == "taken"
    assert await stores.helpers.create(OWNER, "STRASSE", JOB, NOW) == "taken"


async def test_every_write_tells(stores: Stores) -> None:
    h = await made(stores)
    writes = [
        stores.helpers.update(OWNER, h.id, job="Sends bills."),
        stores.helpers.add_note(OWNER, h.id, "Leave out refunds."),
        stores.helpers.pause(OWNER, h.id, NOW),
        stores.helpers.resume(OWNER, h.id),
    ]
    for write in writes:
        stores.told.clear()
        assert await write
        assert stores.told == ["changed"]


async def test_a_running_handoff_survives_a_pause(stores: Stores) -> None:
    h = await made(stores)
    await stores.schedules.create(handoff(h.id))
    assert await stores.schedules.enqueue("h1", NOW, None, 60.0, NOW)
    assert await stores.schedules.claim("w1", NOW, NOW) is not None
    assert await pause_helper(stores.helpers, stores.schedules, OWNER, h.id, NOW)
    assert [x.status for x in await runs(stores.schedules, "h1")] == ["running"]
    s = await stores.schedules.get("h1")
    assert s is not None and s.status == "done"


async def test_pausing_again_finishes_a_pause_that_stopped_halfway(stores: Stores) -> None:
    h = await made(stores)
    await stores.schedules.create(its_routine(h.id))
    await stores.schedules.create(handoff(h.id))
    # The helper was marked paused, then the process stopped before its schedules.
    assert await stores.helpers.pause(OWNER, h.id, NOW)
    assert not await pause_helper(stores.helpers, stores.schedules, OWNER, h.id, NOW + 5)
    r = await stores.schedules.get("r1")
    assert r is not None and r.paused_ms == NOW
    a = await stores.schedules.get("h1")
    assert a is not None and a.status == "cancelled"


async def test_resume_leaves_a_routine_paused_before_the_helper(stores: Stores) -> None:
    h = await made(stores)
    await stores.schedules.create(its_routine(h.id))
    await stores.schedules.create(its_routine(h.id, id="r2", title="Overdue on Friday"))
    assert await stores.schedules.pause(OWNER, "r2", NOW - 1)
    assert await pause_helper(stores.helpers, stores.schedules, OWNER, h.id, NOW)
    later = ms("2026-11-25T12:00")
    assert await resume_helper(stores.helpers, stores.schedules, runner, OWNER, h.id, later)
    r1 = await stores.schedules.get("r1")
    assert r1 is not None and r1.paused_ms is None
    r2 = await stores.schedules.get("r2")
    assert r2 is not None and r2.paused_ms == NOW - 1
