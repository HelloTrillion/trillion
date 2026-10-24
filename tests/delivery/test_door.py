"""The one door (SUR-9): a free page at once, a busy one after, no page a notification."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Sequence

import pytest

from tests.scheduler.conftest import FakeClock, ms
from trillion.delivery.door import (
    PLAYBACK_GRACE_MS,
    READY,
    Delivery,
    Door,
    Facts,
    NotFree,
    free,
    notification,
)
from trillion.scheduler.store import Notice, NoticeKind
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore

DUE = ms("2026-10-24T16:00")
IDLE = Facts(
    turn_running=False,
    listening=False,
    spoken_turn=None,
    spoken_ms=None,
    heard_turn=None,
    active_ms=0,
)


class FakePage:
    def __init__(self, id: str, active_ms: int = 0) -> None:
        self.id = id
        self.state = dataclasses.replace(IDLE, active_ms=active_ms)
        self.got: list[Delivery] = []

    def facts(self) -> Facts:
        return self.state

    async def deliver(self, notices: Sequence[Delivery]) -> None:
        self.got.extend(notices)


class FakeNotify:
    def __init__(self, shows: bool = True) -> None:
        self.shown: list[tuple[str, str]] = []
        self._shows = shows

    def __call__(self, title: str, body: str) -> bool:
        self.shown.append((title, body))
        return self._shows


def door(
    store: SqliteScheduleStore, pages: list[FakePage], notify: FakeNotify | None = None
) -> tuple[Door, FakeNotify, FakeClock]:
    clock = FakeClock(DUE)
    shown = notify or FakeNotify()
    return Door(store, pages, clock=clock, owner="local", notify=shown), shown, clock


async def add(
    store: SqliteScheduleStore,
    kind: NoticeKind = "reminder",
    text: str | None = "Call now.",
    title: str = "Call",
) -> Notice:
    return await store.add_notice(
        owner_id="local",
        source_id="a1b2c3d4",
        kind=kind,
        title=title,
        text=text,
        due_ms=DUE,
        late=False,
        now_ms=DUE,
    )


def test_free_is_judged_from_facts() -> None:
    assert free(IDLE, 0)
    assert not free(dataclasses.replace(IDLE, turn_running=True), 0)
    assert not free(dataclasses.replace(IDLE, listening=True), 0)
    playing = dataclasses.replace(IDLE, spoken_turn=3, spoken_ms=1000, heard_turn=2)
    assert not free(playing, 1000)
    assert free(dataclasses.replace(playing, heard_turn=3), 1000)
    # A page that never says it finished playing stops holding notices back.
    assert free(playing, 1000 + PLAYBACK_GRACE_MS + 1)


async def test_a_free_page_gets_the_notice_at_once(store: SqliteScheduleStore) -> None:
    page = FakePage("c1")
    d, shown, _ = door(store, [page])
    n = await add(store)
    await d.added(n)
    assert [(x.id, x.text, x.late) for x in page.got] == [(n.id, "Call now.", False)]
    kept = await store.notice(n.id)
    assert kept is not None and kept.delivered_to == "c1"
    assert shown.shown == []  # a page is open: no notification


async def test_a_busy_page_gets_it_once_the_reply_is_over(store: SqliteScheduleStore) -> None:
    page = FakePage("c1")
    page.state = dataclasses.replace(page.state, turn_running=True)
    d, shown, _ = door(store, [page])
    await d.added(await add(store))
    assert page.got == [] and shown.shown == []
    page.state = dataclasses.replace(page.state, turn_running=False)
    assert await d.offer(page) == 1
    assert await d.offer(page) == 0  # once
    assert len(page.got) == 1


async def test_with_no_page_it_is_notified_once_and_held(store: SqliteScheduleStore) -> None:
    pages: list[FakePage] = []
    d, shown, clock = door(store, pages)
    first = await add(store)
    await d.added(first)
    await d.added(first)  # told again about the same notice: still shown once
    assert shown.shown == [("Call", "Call now.")]
    second = await add(store, kind="ask", text="Two urgent emails.", title="Email")
    clock.advance(1)
    await d.added(second)
    assert shown.shown[-1] == ("Email", READY)  # never her words on the lock screen
    page = FakePage("c1")
    pages.append(page)
    assert await d.offer(page) == 2
    assert [x.id for x in page.got] == [first.id, second.id]  # oldest first
    another = FakePage("c2", active_ms=5)
    pages.append(another)
    assert await d.offer(another) == 0  # delivered once, wherever


async def test_a_notification_that_could_not_be_shown_leaves_it_held(
    store: SqliteScheduleStore,
) -> None:
    d, _, _ = door(store, [], FakeNotify(shows=False))
    n = await add(store)
    await d.added(n)
    kept = await store.notice(n.id)
    assert kept is not None and kept.notified_ms is None and kept.status == "held"


async def test_only_the_most_recent_page_gets_it(store: SqliteScheduleStore) -> None:
    older, newer = FakePage("c1", active_ms=1), FakePage("c2", active_ms=2)
    d, _, _ = door(store, [older, newer])
    await d.added(await add(store))
    assert older.got == [] and len(newer.got) == 1
    await d.added(await add(store))
    assert await d.offer(older) == 0


async def test_a_notice_delivered_late_says_so(store: SqliteScheduleStore) -> None:
    page = FakePage("c1")
    page.state = dataclasses.replace(page.state, turn_running=True)
    d, _, clock = door(store, [page])
    await d.added(await add(store))
    clock.advance(10 * 60_000)  # the reply ran long; the notice is now ten minutes old
    page.state = dataclasses.replace(page.state, turn_running=False)
    await d.flush()
    assert [x.late for x in page.got] == [True]


async def test_missed_and_failed_carry_no_words(store: SqliteScheduleStore) -> None:
    page = FakePage("c1")
    d, _, _ = door(store, [page])
    for kind in ("missed", "failed"):
        await d.added(await add(store, kind=kind, text=None))  # type: ignore[arg-type]
    assert [(x.kind, x.text) for x in page.got] == [("missed", None), ("failed", None)]
    missed = await add(store, kind="missed", text=None)
    assert notification(missed) == ("Call", "Missed: Trillion was not running at the time")


class Refuses(FakePage):
    """A page that came busy between the door's look and its delivery."""

    async def deliver(self, notices: Sequence[Delivery]) -> None:
        raise NotFree


async def test_a_page_that_cannot_take_them_leaves_them_held(store: SqliteScheduleStore) -> None:
    page = Refuses("c1", active_ms=2)
    pages: list[FakePage] = [page]
    d, _, _ = door(store, pages)
    first, second = await add(store), await add(store)
    await d.added(first)
    assert [n.id for n in await store.held("local")] == [first.id, second.id]
    pages.remove(page)
    after = FakePage("c2", active_ms=3)
    pages.append(after)
    assert await d.offer(after) == 2
    assert [x.id for x in after.got] == [first.id, second.id]


async def test_two_notices_at_once_with_no_page_show_once_each(
    store: SqliteScheduleStore,
) -> None:
    d, shown, _ = door(store, [])
    n = await add(store)
    await asyncio.gather(d.added(n), d.added(n))
    assert shown.shown == [("Call", "Call now.")]


async def test_missed_and_failed_never_carry_stored_words(store: SqliteScheduleStore) -> None:
    page = FakePage("c1")
    d, _, _ = door(store, [page])
    await d.added(await add(store, kind="failed", text="stray words"))
    assert [(x.kind, x.text) for x in page.got] == [("failed", None)]


def test_a_turn_sent_to_be_heard_always_says_when() -> None:
    with pytest.raises(ValueError):
        dataclasses.replace(IDLE, spoken_turn=1, spoken_ms=None)
