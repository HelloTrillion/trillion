"""The Living Mind's bus (GAL-10, decision 7): bounded, never blocking, ids only; and
the sources that publish on it outside a turn (memory, helpers' runs, alerts)."""

# ruff: noqa: F811 - the store fixture is shared with the scheduler tests

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from tests.scheduler.conftest import ms, schedule, store  # noqa: F401
from tests.scheduler.test_runner import Asks, make
from trillion.delivery.door import Door
from trillion.memory.book import MemoryBook
from trillion.mind.observe import EVENTS, Observer
from trillion.scheduler.runner import AskFailed
from trillion.scheduler.store import Notice
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.memory_store import SqliteMemoryStore
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore

DUE = ms("2026-10-24T16:00")


def drain(observer: Observer, *subs: Any) -> list[list[dict[str, Any]]]:
    out = []
    for s in subs:
        got = []
        while s.pending():
            got.append(s._queue.popleft())
        out.append(got)
    return out


async def test_every_page_watching_gets_each_event() -> None:
    o = Observer()
    a, b = o.subscribe(), o.subscribe()
    o.publish("tool.start", name="file_read")
    assert await asyncio.wait_for(a.get(), 1) == {"type": "tool.start", "name": "file_read"}
    assert await asyncio.wait_for(b.get(), 1) == {"type": "tool.start", "name": "file_read"}
    b.close()
    o.publish("turn", conversation_id="c1")
    assert await asyncio.wait_for(a.get(), 1) == {"type": "turn", "conversation_id": "c1"}
    assert b.pending() == 0


async def test_a_wait_ends_when_an_event_comes() -> None:
    o = Observer()
    s = o.subscribe()
    waiting = asyncio.ensure_future(s.get())
    await asyncio.sleep(0)
    assert not waiting.done()
    o.publish("alert", critical=True)
    assert await asyncio.wait_for(waiting, 1) == {"type": "alert", "critical": True}


async def test_a_slow_page_drops_its_oldest_and_never_holds_up_her() -> None:
    o = Observer(size=4)
    slow, quick = o.subscribe(), o.subscribe()
    for i in range(10):
        # Synchronous: publishing cannot wait on anyone.
        o.publish("tool.start", name=f"tool_{i}")
        assert (await asyncio.wait_for(quick.get(), 1))["name"] == f"tool_{i}"
    assert slow.pending() == 4 and slow.dropped == 6
    (kept,) = drain(o, slow)
    assert [e["name"] for e in kept] == ["tool_6", "tool_7", "tool_8", "tool_9"]


def test_with_nobody_watching_nothing_is_kept() -> None:
    o = Observer()
    o.publish("tool.start", name="file_read")
    s = o.subscribe()
    assert s.pending() == 0 and o.watched
    with s:
        pass
    assert not o.watched


def test_only_listed_fields_and_plain_ids_pass() -> None:
    o = Observer()
    s = o.subscribe()
    o.publish(
        "tool.end",
        name="file_create",
        ok=True,
        args={"content": "secret words"},
        result="the file said things",
        text="memory text",
    )
    o.publish("memory.recalled", ids=["mem_1", {"text": "no"}, "mem_2"])
    o.publish("tool.start", name="x" * 500)
    o.publish("message.text", text="hello")
    (got,) = drain(o, s)
    assert got == [
        {"type": "tool.end", "name": "file_create", "ok": True},
        {"type": "memory.recalled", "ids": ["mem_1", "mem_2"]},
        {"type": "tool.start"},
    ]


def test_the_event_table_is_the_specs() -> None:
    assert EVENTS == {
        "turn": {"conversation_id"},
        "tool.start": {"name"},
        "tool.end": {"name", "ok"},
        "memory.written": {"id"},
        "memory.recalled": {"ids"},
        "helper.started": {"id"},
        "helper.done": {"id", "ok"},
        "alert": {"critical"},
    }


async def test_a_memory_written_is_told_by_id_and_a_duplicate_is_not(tmp_path: Path) -> None:
    conn = await connect(tmp_path / "t.db")
    try:
        await migrate(conn)
        book = MemoryBook(SqliteMemoryStore(conn), tmp_path / "memory", "local")
        await book.sync(1)
        o = Observer()
        book.observe = o.publish
        s = o.subscribe()
        args: dict[str, Any] = {"type": "user", "source": "user_statement", "moves": False}
        done = await book.write(text="I take my coffee black.", now_ms=1, **args)
        await book.write(text="i take my coffee  black.", now_ms=2, **args)
        book.recalled(book.recall("coffee"))
        book.recalled([])
        (got,) = drain(o, s)
        assert got == [
            {"type": "memory.written", "id": done.memory.id},
            {"type": "memory.recalled", "ids": [done.memory.id]},
        ]
        assert "coffee" not in str(got)
    finally:
        await conn.close()


async def test_a_timed_ask_tells_its_start_and_end(store: SqliteScheduleStore) -> None:
    await store.create(schedule(kind="ask", body="Check my email."))
    sched, _, _ = make(store, DUE, Asks("Two urgent emails."))
    o = Observer()
    sched.observe = o.publish
    s = o.subscribe()
    await sched.tick()
    (got,) = drain(o, s)
    assert got == [
        {"type": "helper.started", "id": "ask:a1b2c3d4"},
        {"type": "helper.done", "id": "ask:a1b2c3d4", "ok": True},
    ]
    assert "email" not in str(got)


async def test_a_failed_ask_ends_not_ok(store: SqliteScheduleStore) -> None:
    await store.create(schedule(kind="ask", body="Check my email."))
    sched, _, _ = make(store, DUE, Asks(AskFailed("the model broke")))
    o = Observer()
    sched.observe = o.publish
    s = o.subscribe()
    await sched.tick()
    (got,) = drain(o, s)
    assert got[-1] == {"type": "helper.done", "id": "ask:a1b2c3d4", "ok": False}


async def test_a_reminder_is_no_helper_run(store: SqliteScheduleStore) -> None:
    await store.create(schedule())
    sched, _, _ = make(store, DUE)
    o = Observer()
    sched.observe = o.publish
    s = o.subscribe()
    await sched.tick()
    assert drain(o, s) == [[]]


def notice(source_id: str, source: Any = "attention") -> Notice:
    return Notice(
        id="n1", owner_id="local", source_id=source_id, kind="ready", title="T",
        text="Words that stay on the page.", due_ms=0, late=False, created_ms=0,
        status="held", delivered_ms=None, delivered_to=None, notified_ms=None, source=source,
    )  # fmt: skip


async def test_a_critical_notice_ripples_and_another_does_not(
    store: SqliteScheduleStore,
) -> None:
    door = Door(store, [], clock=lambda: 0, owner="local", notify=lambda *a, **k: False)
    o = Observer()
    door.observe = o.publish
    s = o.subscribe()
    await door.added(notice("meeting:abc"))
    await door.added(notice("security:new-device"))
    (got,) = drain(o, s)
    assert got == [{"type": "alert", "critical": True}]
