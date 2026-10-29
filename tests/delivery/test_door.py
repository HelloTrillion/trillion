"""The one door (SUR-9): a free page at once, a busy one after, no page a notification."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Sequence

import pytest

from tests.scheduler.conftest import FakeClock, ms, schedule, written
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
        self.silent: list[bool] = []
        self._shows = shows

    def __call__(self, title: str, body: str, *, silent: bool = False) -> bool:
        self.shown.append((title, body))
        self.silent.append(silent)
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
    return await written(store, kind=kind, text=text, title=title, due_ms=DUE, now_ms=DUE)


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


async def test_a_ready_line_keeps_its_words_to_be_spoken(store: SqliteScheduleStore) -> None:
    page = FakePage("c1")
    d, _, _ = door(store, [page])
    line = "Your Monday revenue is ready."
    await d.added(await add(store, kind="ready", text=line))
    assert [(x.kind, x.text) for x in page.got] == [("ready", line)]


async def test_a_ready_line_carries_the_conversation_it_opens(
    store: SqliteScheduleStore,
) -> None:
    page = FakePage("c1")
    d, _, _ = door(store, [page])
    n = await written(
        store,
        kind="ready",
        text="Ready.",
        title="Ready",
        due_ms=DUE,
        now_ms=DUE,
        conversation_id="conv-1",
    )
    await d.added(n)
    assert [x.conversation_id for x in page.got] == ["conv-1"]


def test_a_turn_sent_to_be_heard_always_says_when() -> None:
    with pytest.raises(ValueError):
        dataclasses.replace(IDLE, spoken_turn=1, spoken_ms=None)


class Slow(FakePage):
    """Takes the notices, then is slow to finish sending them."""

    def __init__(self, id: str, active_ms: int = 0) -> None:
        super().__init__(id, active_ms)
        self.gate = asyncio.Event()
        self.finished = False

    async def deliver(self, notices: Sequence[Delivery]) -> None:
        self.got.extend(notices)  # saved: from here it has them
        await self.gate.wait()
        self.finished = True


async def test_a_hand_over_cut_short_still_finishes_and_is_never_held_again(
    store: SqliteScheduleStore,
) -> None:
    page = Slow("c1", active_ms=1)
    d, _, _ = door(store, [page])
    n = await add(store)
    offer = asyncio.create_task(d.offer(page))
    # Until the page has them, however slow the machine: a fixed number of turns
    # was too few on a loaded CI runner.
    async with asyncio.timeout(5):
        while not page.got:
            await asyncio.sleep(0.005)
    assert [x.id for x in page.got] == [n.id]
    offer.cancel()  # the scheduler stopping, say
    await asyncio.sleep(0)
    page.gate.set()
    with pytest.raises(asyncio.CancelledError):
        await offer
    assert page.finished  # the session was never cut off mid-hand-over
    assert await store.held("local") == []  # taken once, so never held for another page


async def test_its_due_time_is_in_the_schedules_own_zone(store: SqliteScheduleStore) -> None:
    await store.create(schedule(zone="Europe/Paris", at_local="2026-10-24T22:00"))
    page = FakePage("c1")
    d, _, clock = door(store, [page])
    clock.advance(10 * 3_600_000)  # delivered the next morning: it names the day
    await d.added(await add(store))
    assert [(x.due, x.late) for x in page.got] == [("Sat 10:00 pm", True)]


async def test_a_notice_whose_zone_is_unknown_here_has_no_due_words(
    store: SqliteScheduleStore,
) -> None:
    await store.create(schedule(zone="Nowhere/Gone"))
    page = FakePage("c1")
    d, _, _ = door(store, [page])
    await d.added(await add(store))
    assert [x.due for x in page.got] == [""]


async def test_a_hand_over_stopped_while_marking_puts_back_what_it_marked(
    store: SqliteScheduleStore,
) -> None:
    page = FakePage("c1")
    d, _, _ = door(store, [page])
    first, second = await add(store), await add(store)
    looking, never = asyncio.Event(), asyncio.Event()
    get = store.get

    async def stuck(schedule_id: str) -> object:
        looking.set()  # the first is marked delivered by now
        await never.wait()
        return await get(schedule_id)

    store.get = stuck  # type: ignore[method-assign,assignment]
    offer = asyncio.create_task(d.offer(page))
    await looking.wait()
    offer.cancel()  # the server stopping
    with pytest.raises(asyncio.CancelledError):
        await offer
    store.get = get  # type: ignore[method-assign]
    assert [n.id for n in await store.held("local")] == [first.id, second.id]
    assert page.got == []
    assert await d.offer(page) == 2  # and the next free page gets both


async def test_a_store_error_while_marking_puts_back_what_it_marked(
    store: SqliteScheduleStore,
) -> None:
    import sqlite3

    page = FakePage("c1")
    d, _, _ = door(store, [page])
    first = await add(store)

    async def broken(schedule_id: str) -> object:
        raise sqlite3.OperationalError("disk I/O error")

    get = store.get
    store.get = broken  # type: ignore[method-assign,assignment]
    with pytest.raises(sqlite3.OperationalError):
        await d.offer(page)
    store.get = get  # type: ignore[method-assign]
    assert [n.id for n in await store.held("local")] == [first.id]
    assert page.got == []


async def test_with_no_page_a_tick_notifies_what_is_held_and_not_yet_shown(
    store: SqliteScheduleStore,
) -> None:
    # Held when its page closed, or written just before a crash: never notified.
    d, shown, _ = door(store, [])
    await add(store)
    await d.flush()
    await d.flush()
    assert shown.shown == [("Call", "Call now.")]  # once


async def test_a_notification_that_fails_is_not_tried_on_every_tick(
    store: SqliteScheduleStore,
) -> None:
    fails = FakeNotify(shows=False)
    d, _, _ = door(store, [], fails)
    n = await add(store)
    await d.flush()
    await d.flush()
    assert len(fails.shown) == 1
    kept = await store.notice(n.id)
    assert kept is not None and kept.status == "held" and kept.notified_ms is None


async def test_with_a_page_open_a_tick_never_notifies(store: SqliteScheduleStore) -> None:
    page = FakePage("c1")
    page.state = dataclasses.replace(page.state, turn_running=True)
    d, shown, _ = door(store, [page])
    await add(store)
    await d.flush()
    assert shown.shown == [] and page.got == []  # a busy live page: the notice waits
