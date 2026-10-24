"""The schedule tools (AGT-9): a reminder runs at once, a timed ask asks first (D6)."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.scheduler.conftest import NY, FakeClock, ms
from trillion.scheduler.when import Repeat
from trillion.spine.contract import ToolCall, ToolContext
from trillion.spine.deny_floor import DenyPolicy
from trillion.spine.registry import ToolRegistry
from trillion.spine.router import Router
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore
from trillion.storage.sqlite.spine_store import SqliteSpineStore
from trillion.tools.schedule import AskSchedule, ReminderSet, ScheduleCancel, ScheduleList

NOW = ms("2026-10-24T12:00")
CTX = ToolContext(owner_id="local", session_id="c1", turn=1, call_id="t1", via="text")
REMIND = {"when": "2026-10-24T16:00", "say": "Time to call Sam.", "title": "Call Sam"}


class Tools:
    def __init__(self, store: SqliteScheduleStore, zone: str | None = NY) -> None:
        self.store = store
        self.clock = FakeClock(NOW)
        self.reminder, self.ask = ReminderSet(), AskSchedule()
        self.list, self.cancel = ScheduleList(), ScheduleCancel()
        for tool in (self.reminder, self.ask, self.list, self.cancel):
            tool.bind(store, zone=lambda: zone, clock=self.clock, owner="local")


@pytest.fixture
def tools(store: SqliteScheduleStore) -> Tools:
    return Tools(store)


@pytest.fixture
async def gated(tmp_path: Path) -> AsyncIterator[tuple[Tools, Router]]:
    """ask_schedule behind a real router, as the app runs it."""
    spine_conn = await connect(tmp_path / "t.db")
    await migrate(spine_conn)
    schedule_conn = await connect(tmp_path / "t.db")
    tools = Tools(SqliteScheduleStore(schedule_conn))
    registry = ToolRegistry()
    registry.register(tools.ask)
    store = SqliteSpineStore(spine_conn)
    yield tools, Router(registry, store, policy=DenyPolicy.default(), clock=tools.clock)
    await schedule_conn.close()
    await spine_conn.close()


async def test_a_reminder_is_set_and_she_gets_the_exact_time_back(tools: Tools) -> None:
    done = await tools.reminder.execute(REMIND, CTX)
    assert done.ok and done.effect == "confirmed"
    assert "Sat 24 Oct, 4:00 pm (America/New_York), once" in done.content
    (s,) = await tools.store.active("local")
    assert (s.kind, s.body, s.title, s.created_in) == (
        "reminder",
        "Time to call Sam.",
        "Call Sam",
        "c1",
    )
    assert s.next_due_ms == ms("2026-10-24T16:00") and s.id in done.content


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"when": "2026-10-24T11:58"}, "has passed"),
        ({"when": "tomorrow"}, "YYYY-MM-DDTHH:MM"),
        ({"say": "x" * 501}, "too long"),
        ({"say": "   "}, "is empty"),
        ({"title": "x" * 81}, "title must be"),
        ({"repeat": {"every": "fortnight"}}, "day, weekday, week or month"),
    ],
)
async def test_a_reminder_that_cannot_be_kept_is_refused(
    tools: Tools, change: dict[str, Any], why: str
) -> None:
    done = await tools.reminder.execute({**REMIND, **change}, CTX)
    assert not done.ok and why in done.content
    assert await tools.store.active("local") == []


async def test_the_zone_is_kept_by_its_canonical_name(store: SqliteScheduleStore) -> None:
    done = await Tools(store, zone="america/new_york").reminder.execute(REMIND, CTX)
    assert done.ok and "(America/New_York)" in done.content
    (s,) = await store.active("local")
    assert s.zone == NY


async def test_a_time_just_past_is_the_clocks_disagreeing(tools: Tools) -> None:
    tools.clock.now = ms("2026-10-24T16:00") + 30_000
    assert (await tools.reminder.execute(REMIND, CTX)).ok


async def test_no_reminder_after_reading_outside_content(tools: Tools) -> None:
    done = await tools.reminder.execute(REMIND, dataclasses.replace(CTX, read_outside=True))
    assert not done.ok and done.error_kind == "from_reading"
    assert await tools.store.active("local") == []


async def test_no_timezone_says_where_to_set_one(store: SqliteScheduleStore) -> None:
    done = await Tools(store, zone=None).reminder.execute(REMIND, CTX)
    assert not done.ok and "Settings → Names and tone" in done.content


async def test_a_timed_ask_shows_a_card_and_is_set_only_when_approved(
    gated: tuple[Tools, Router],
) -> None:
    tools, router = gated
    args = {
        "when": "2026-10-26T09:00",
        "ask": "Check my email and tell me what's urgent",
        "title": "Urgent email",
        "repeat": {"every": "weekday"},
    }
    card = await router.execute(ToolCall(name="ask_schedule", args=args), CTX)
    assert card.status == "confirmation_required"
    assert card.summary == "Every weekday at 9:00 am: Check my email and tell me what's urgent"
    assert await tools.store.active("local") == []  # nothing until the user says yes
    tap = dataclasses.replace(CTX, via="tap", turn=2)
    done = await router.resolve(card.pending_id or "", "confirm", tap)
    assert done.status == "succeeded"
    (s,) = await tools.store.active("local")
    assert s.kind == "ask" and s.repeat == Repeat(every="weekday")
    assert s.next_due_ms == ms("2026-10-26T09:00")


async def test_a_timed_ask_whose_time_passed_while_waiting_is_not_set(
    gated: tuple[Tools, Router],
) -> None:
    tools, router = gated
    args = {"when": "2026-10-24T12:05", "ask": "Check the oven.", "title": "Oven"}
    card = await router.execute(ToolCall(name="ask_schedule", args=args), CTX)
    tools.clock.advance(9 * 60_000)
    done = await router.resolve(card.pending_id or "", "confirm", dataclasses.replace(CTX, turn=2))
    assert done.status == "refused" and "has passed" in done.detail
    assert await tools.store.active("local") == []


async def test_list_and_cancel(tools: Tools) -> None:
    assert (await tools.list.execute({}, CTX)).content == "nothing is set"
    await tools.reminder.execute({**REMIND, "repeat": {"every": "day"}}, CTX)
    (s,) = await tools.store.active("local")
    listed = (await tools.list.execute({}, CTX)).content
    assert s.id in listed and "Call Sam" in listed and "Every day at 4:00 pm" in listed
    assert "last run none yet" in listed
    gone = await tools.cancel.execute({"id": s.id}, CTX)
    assert gone.ok and await tools.store.active("local") == []
    again = await tools.cancel.execute({"id": s.id}, CTX)
    assert not again.ok and again.error_kind == "not_found"


def test_no_schedule_tool_is_offered_to_a_run_nobody_watches() -> None:
    registry = ToolRegistry.discover()
    names = {"reminder_set", "ask_schedule", "schedule_list", "schedule_cancel"}
    assert names <= {d.name for d in registry.definitions()}
    assert not names & {d.name for d in registry.unattended().definitions()}
