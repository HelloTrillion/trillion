"""Notices that may stay silent: a quiet one is shown and notified, never spoken (D7),
and a watcher's line delivered after its moment is shown, not spoken (D9)."""

from __future__ import annotations

import asyncio
import dataclasses
import logging

import pytest

from tests.delivery.test_door import DUE, FakeNotify, FakePage, add
from tests.scheduler.conftest import FakeClock
from trillion.delivery import door as door_module
from trillion.delivery.door import Door, Quiet
from trillion.scheduler.store import Notice
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore

MEET = "https://meet.example.com/abc"


class FakeQuiet:
    """Holds whatever it is told to, and keeps what it was asked about."""

    def __init__(self, reason: str | None = None) -> None:
        self.reason_for = reason
        self.asked: list[tuple[str, int]] = []
        self.hosts: list[str | None] = []

    async def reason(self, notice: Notice, now_ms: int, *, host: str | None = None) -> str | None:
        self.asked.append((notice.id, now_ms))
        self.hosts.append(host)
        return self.reason_for


class Broken:
    async def reason(self, notice: Notice, now_ms: int, *, host: str | None = None) -> str | None:
        raise OSError("no audio service")


def door(
    store: SqliteScheduleStore, pages: list[FakePage], quiet: Quiet | None = None
) -> tuple[Door, FakeNotify, FakeClock]:
    clock, shown = FakeClock(DUE), FakeNotify()
    d = Door(store, pages, clock=clock, owner="local", notify=shown, quiet=quiet)
    return d, shown, clock


async def heads_up(
    store: SqliteScheduleStore,
    *,
    key: str = "meeting:e1:1",
    text: str | None = "Your 10:00 with Sam starts in five minutes.",
    fresh_until_ms: int | None = None,
    link: str | None = None,
) -> Notice:
    return await store.attention_notice(
        "local",
        key,
        title="Sam",
        text=text,
        now_ms=DUE,
        link=link,
        fresh_until_ms=fresh_until_ms,
    )


async def test_a_notice_is_spoken_when_nothing_holds_it(store: SqliteScheduleStore) -> None:
    page = FakePage("c1")
    d, shown, _ = door(store, [page])
    await d.added(await add(store))
    (got,) = page.got
    assert (got.spoken, got.quiet, got.link) == (True, None, None)
    assert shown.shown == []  # a page is open and nothing held her voice


async def test_a_quiet_notice_is_delivered_unspoken_and_notified_once(
    store: SqliteScheduleStore,
) -> None:
    page = FakePage("c1")
    d, shown, _ = door(store, [page], FakeQuiet("quiet hours"))
    n = await heads_up(store)
    await d.added(n)
    await d.drain()
    (got,) = page.got
    assert got.text == "Your 10:00 with Sam starts in five minutes."
    assert (got.spoken, got.quiet) == (False, "quiet hours")
    # The page may not be looked at: the computer shows it too, without her words.
    assert len(shown.shown) == 1 and shown.shown[0][0] == "Sam"
    kept = await store.notice(n.id)
    assert kept is not None and kept.status == "delivered" and kept.notified_ms == DUE
    await d.added(n)
    await d.flush()
    await d.drain()
    assert len(shown.shown) == 1  # once


async def test_a_notice_notified_while_no_page_was_open_is_not_notified_again(
    store: SqliteScheduleStore,
) -> None:
    pages: list[FakePage] = []
    d, shown, _ = door(store, pages, FakeQuiet("in a meeting"))
    await d.added(await heads_up(store))
    assert len(shown.shown) == 1
    page = FakePage("c1")
    pages.append(page)
    assert await d.offer(page) == 1
    assert page.got[0].quiet == "in a meeting" and len(shown.shown) == 1


async def test_the_door_asks_about_each_notice_and_uses_the_answer(
    store: SqliteScheduleStore,
) -> None:
    page = FakePage("c1")
    page.state = dataclasses.replace(page.state, turn_running=True)
    quiet = FakeQuiet("another app is using the microphone")
    d, _, clock = door(store, [page], quiet)
    reminder = await add(store)
    meeting = await heads_up(store)
    await d.added(reminder)
    clock.advance(5)
    page.state = dataclasses.replace(page.state, turn_running=False)
    await d.offer(page)
    # Which reasons apply to which kind is the quiet check's to decide, not the door's.
    assert quiet.asked == [(reminder.id, DUE + 5), (meeting.id, DUE + 5)]
    assert [(x.spoken, x.quiet) for x in page.got] == [
        (False, "another app is using the microphone"),
        (False, "another app is using the microphone"),
    ]


async def test_a_notice_without_words_is_never_asked_about(store: SqliteScheduleStore) -> None:
    page = FakePage("c1")
    quiet = FakeQuiet("quiet hours")
    d, shown, _ = door(store, [page], quiet)
    await d.added(await add(store, kind="missed", text=None))
    await d.drain()
    assert quiet.asked == []
    assert [(x.spoken, x.quiet) for x in page.got] == [(False, None)]
    assert shown.shown == []


async def test_a_heads_up_with_no_line_is_shown_and_notified_without_a_held_reason(
    store: SqliteScheduleStore,
) -> None:
    page = FakePage("c1")
    quiet = FakeQuiet("quiet hours")
    d, shown, _ = door(store, [page], quiet)
    n = await heads_up(store, text=None)  # no model reached: shown by its title
    await d.added(n)
    await d.drain()
    # Nothing was held, so no reason beside it; the window may be hidden, so the
    # computer shows it too, silent since it is quiet hours.
    assert [(x.spoken, x.quiet) for x in page.got] == [(False, None)]
    assert (shown.shown, shown.silent) == ([("Sam", "Ready in Trillion")], [True])
    await d.flush()
    await d.drain()
    assert len(shown.shown) == 1  # once
    kept = await store.notice(n.id)
    assert kept is not None and kept.notified_ms == DUE


async def test_a_heads_up_with_no_line_makes_a_sound_when_nothing_is_quiet(
    store: SqliteScheduleStore,
) -> None:
    page = FakePage("c1")
    d, shown, _ = door(store, [page], FakeQuiet())
    await d.added(await heads_up(store, text=None))
    await d.drain()
    assert shown.silent == [False]


async def test_a_line_delivered_after_its_moment_is_shown_not_spoken(
    store: SqliteScheduleStore,
) -> None:
    page = FakePage("c1")
    quiet = FakeQuiet()
    d, shown, _ = door(store, [page], quiet)
    await d.added(await heads_up(store, key="a", fresh_until_ms=DUE - 1))
    await d.added(await heads_up(store, key="b", fresh_until_ms=DUE))
    assert [(x.spoken, x.quiet) for x in page.got] == [(False, None), (True, None)]
    # A stale one is not spoken, whatever the quiet check says; it is asked only
    # whether its notification may make a sound.
    await d.drain()
    # Shown, not spoken: the computer shows the stale one too, in case no one is looking.
    assert shown.shown == [("Sam", "Ready in Trillion")]


async def test_a_heads_up_carries_its_link(store: SqliteScheduleStore) -> None:
    page = FakePage("c1")
    d, _, _ = door(store, [page])
    await d.added(await heads_up(store, link=MEET))
    (got,) = page.got
    assert (got.kind, got.link, got.spoken, got.due) == ("heads_up", MEET, True, "")


async def test_a_quiet_check_that_fails_does_not_hold_delivery(
    store: SqliteScheduleStore, caplog: pytest.LogCaptureFixture
) -> None:
    page = FakePage("c1")
    d, shown, _ = door(store, [page], Broken())
    with caplog.at_level(logging.WARNING, logger="trillion.delivery.door"):
        await d.added(await heads_up(store))
    (got,) = page.got
    assert (got.spoken, got.quiet) == (True, None)  # can't tell never holds her voice
    assert "OSError" in caplog.text and "no audio service" not in caplog.text
    assert shown.shown == []


class Hangs:
    async def reason(self, notice: Notice, now_ms: int, *, host: str | None = None) -> str | None:
        await asyncio.sleep(3600)
        return "never"


async def test_a_quiet_check_that_never_answers_does_not_stop_the_door(
    store: SqliteScheduleStore, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(door_module, "QUIET_TIMEOUT_S", 0.05)
    page = FakePage("c1")
    d, _, _ = door(store, [page], Hangs())
    with caplog.at_level(logging.WARNING, logger="trillion.delivery.door"):
        await asyncio.wait_for(d.added(await heads_up(store)), 5)
    (got,) = page.got
    assert (got.spoken, got.quiet) == (True, None)
    assert "TimeoutError" in caplog.text


def test_the_quiet_check_has_a_limit() -> None:
    assert door_module.QUIET_TIMEOUT_S == 2.5


async def test_a_quiet_notification_makes_no_sound_and_a_held_one_is_as_before(
    store: SqliteScheduleStore,
) -> None:
    page = FakePage("c1")
    d, shown, _ = door(store, [page], FakeQuiet("quiet hours"))
    await d.added(await heads_up(store))
    await d.drain()
    pages: list[FakePage] = []
    held, held_shown, _ = door(store, pages)
    await held.added(await heads_up(store, key="meeting:e2:1"))
    assert (shown.silent, held_shown.silent) == ([True], [False])


async def test_several_quiet_notices_are_notified_after_the_hand_over_not_during_it(
    store: SqliteScheduleStore,
) -> None:
    import threading

    gate = threading.Event()
    shown: list[str] = []

    def slow(title: str, body: str, *, silent: bool = False) -> bool:
        gate.wait(5)
        shown.append(title)
        return True

    pages: list[FakePage] = []
    d, _, _ = door(store, pages, FakeQuiet("in a meeting"))
    d.notify = slow
    first = await heads_up(store, key="meeting:e1:1")
    second = await heads_up(store, key="meeting:e2:1")
    page = FakePage("c1")
    pages.append(page)
    try:
        # The session waits on `offer`: the notifications must not hold it.
        assert await asyncio.wait_for(d.offer(page), 1) == 2
    finally:
        gate.set()
    await d.drain()
    assert len(shown) == 2
    for n in (first, second):
        kept = await store.notice(n.id)
        assert kept is not None and kept.notified_ms is not None
    # Once only, however often it is offered again.
    await d.flush()
    await d.drain()
    assert len(shown) == 2


async def test_a_heads_up_carries_its_detail_for_the_page(store: SqliteScheduleStore) -> None:
    page = FakePage("c1")
    d, _, _ = door(store, [page])
    n = await store.attention_notice(
        "local", "meeting:e1:1", title="Meeting soon", text=None, now_ms=DUE,
        detail="Planning at 10:00 am",
    )  # fmt: skip
    await d.added(n)
    (got,) = page.got
    assert (got.title, got.text, got.detail, got.spoken) == (
        "Meeting soon",
        None,
        "Planning at 10:00 am",
        False,
    )


async def test_with_no_page_a_quiet_notice_is_notified_silently(
    store: SqliteScheduleStore,
) -> None:
    pages: list[FakePage] = []
    quiet = FakeQuiet("quiet hours")
    d, shown, _ = door(store, pages, quiet)
    await d.added(await heads_up(store))
    assert shown.silent == [True]
    assert quiet.hosts == [None]  # no page, so no page's host


async def test_with_no_page_a_notice_is_notified_with_a_sound_when_nothing_is_quiet(
    store: SqliteScheduleStore,
) -> None:
    d, shown, _ = door(store, [], FakeQuiet())
    await d.added(await add(store))
    assert shown.silent == [False]


async def test_a_held_notice_notified_on_a_tick_asks_the_quiet_check_too(
    store: SqliteScheduleStore,
) -> None:
    n = await heads_up(store, text=None)
    d, shown, _ = door(store, [], FakeQuiet("in a meeting"))
    await d.flush()  # as after a restart: held, never shown
    assert shown.silent == [True]
    kept = await store.notice(n.id)
    assert kept is not None and kept.notified_ms == DUE


async def test_a_held_notice_without_words_is_notified_as_before(
    store: SqliteScheduleStore,
) -> None:
    quiet = FakeQuiet("quiet hours")
    d, shown, _ = door(store, [], quiet)
    await d.added(await add(store, kind="missed", text=None))
    assert (quiet.asked, shown.silent) == ([], [False])


async def test_with_no_page_a_quiet_check_that_never_answers_still_notifies(
    store: SqliteScheduleStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(door_module, "QUIET_TIMEOUT_S", 0.05)
    d, shown, _ = door(store, [], Hangs())
    await asyncio.wait_for(d.added(await heads_up(store)), 5)
    assert shown.silent == [False]  # can't tell never holds anything


async def test_the_quiet_check_is_told_the_host_of_the_page_it_delivers_to(
    store: SqliteScheduleStore,
) -> None:
    page = FakePage("c1")
    page.state = dataclasses.replace(page.state, host="safari")
    quiet = FakeQuiet()
    d, _, _ = door(store, [page], quiet)
    await d.added(await heads_up(store))
    assert quiet.hosts == ["safari"]
