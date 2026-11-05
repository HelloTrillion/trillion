"""The helper list (the helpers panel spec): her routines, timed asks and watchers, each
with its state, last run and next run, read from what already exists."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator, Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from tests.scheduler.conftest import NY, ms, schedule
from trillion.attention.runner import WatcherStatus
from trillion.helpers.build import helpers
from trillion.helpers.model import Helper, LastRun, Waiting, to_json
from trillion.scheduler.store import Run, RunStatus, Schedule
from trillion.scheduler.when import Repeat
from trillion.spend.types import Work
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore

NOW = ms("2026-11-05T08:00")
OWNER = "local"


def routine(**kw: Any) -> Schedule:
    base = schedule(
        id="r1",
        kind="ask",
        title="Monday revenue",
        body="Pull last week's revenue from Stripe\nand put it on a board.",
        target="own",
        conversation_id="conv-r1",
        repeat=Repeat(every="week", days=("mon",)),
        at_local="2026-11-09T07:00",
        next_due_ms=ms("2026-11-09T07:00"),
    )
    return dataclasses.replace(base, **kw)


def ask(**kw: Any) -> Schedule:
    base = schedule(
        id="a1",
        kind="ask",
        title="Remind me at 4",
        body="Tell me the weather at 4.",
        at_local="2026-11-05T16:00",
        next_due_ms=ms("2026-11-05T16:00"),
    )
    return dataclasses.replace(base, **kw)


def run(schedule_id: str, status: RunStatus, **kw: Any) -> Run:
    base = Run(
        id=1,
        schedule_id=schedule_id,
        due_ms=NOW - 3_600_000,
        status=status,
        claimed_by="w1",
        claimed_ms=NOW - 3_600_000,
        started_ms=NOW - 3_600_000,
        finished_ms=None if status == "running" else NOW - 3_500_000,
        timeout_s=120.0,
        attempts=1,
        result_notice=None,
        detail=None,
    )
    return dataclasses.replace(base, **kw)


def watcher(name: str = "meetings", **kw: Any) -> WatcherStatus:
    base = WatcherStatus(
        name=name,
        on=True,
        checking_since_ms=None,
        last_check_ms=None,
        last_error=None,
        last_error_ms=None,
        next_check_ms=NOW + 60_000,
    )
    return dataclasses.replace(base, **kw)


@dataclass
class Schedules:
    schedules: list[Schedule] = field(default_factory=list)
    last: dict[str, Run] = field(default_factory=dict)
    now: dict[str, Run] = field(default_factory=dict)

    async def active(self, owner_id: str) -> list[Schedule]:
        return [s for s in self.schedules if s.owner_id == owner_id]

    async def last_results(self, owner_id: str) -> dict[str, Run]:
        return dict(self.last)

    async def running(self, owner_id: str) -> dict[str, Run]:
        return dict(self.now)


@dataclass
class Sources:
    schedules: Schedules = field(default_factory=Schedules)
    watching: list[WatcherStatus] = field(default_factory=list)
    paused_work: set[Work] = field(default_factory=set)
    owner: str = OWNER

    async def watchers(self) -> list[WatcherStatus]:
        return list(self.watching)

    async def capped(self) -> Collection[Work]:
        return set(self.paused_work)


def one(found: list[Helper], helper_id: str) -> Helper:
    (h,) = [h for h in found if h.id == helper_id]
    return h


async def test_an_idle_routine_names_its_job_and_next_run() -> None:
    src = Sources(Schedules([routine()]))
    (h,) = await helpers(src)
    assert h == Helper(
        id="routine:r1",
        kind="routine",
        name="Monday revenue",
        job="Pull last week's revenue from Stripe",
        state="idle",
        doing=None,
        since_ms=None,
        last_run=None,
        next_ms=ms("2026-11-09T07:00"),
        waiting=(),
        conversation_id="conv-r1",
    )


async def test_a_routine_running_now_is_working_since_its_start() -> None:
    src = Sources(Schedules([routine()], now={"r1": run("r1", "running", started_ms=NOW - 12_000)}))
    (h,) = await helpers(src)
    assert (h.state, h.doing, h.since_ms) == ("working", "Monday revenue", NOW - 12_000)


async def test_a_paused_routine_is_paused() -> None:
    (h,) = await helpers(Sources(Schedules([routine(paused_ms=NOW - 1)])))
    assert h.state == "paused"


async def test_a_routine_at_the_spend_cap_is_capped_unless_the_user_paused_it() -> None:
    src = Sources(Schedules([routine(), routine(id="r2", paused_ms=NOW)]), paused_work={"routines"})
    found = await helpers(src)
    assert one(found, "routine:r1").state == "capped"
    assert one(found, "routine:r2").state == "paused"
    # Only routines are paused there: a timed ask is not.
    src.schedules.schedules.append(ask())
    assert one(await helpers(src), "ask:a1").state == "idle"


async def test_a_timed_ask_says_when_it_fires() -> None:
    (h,) = await helpers(Sources(Schedules([ask()])))
    assert (h.id, h.kind, h.name, h.job, h.state) == (
        "ask:a1",
        "ask",
        "Remind me at 4",
        "Thu 5 Nov at 4:00 pm",
        "idle",
    )
    assert (h.next_ms, h.conversation_id) == (ms("2026-11-05T16:00"), None)
    daily = ask(repeat=Repeat(every="day"))
    (h,) = await helpers(Sources(Schedules([daily])))
    assert h.job == "Every day at 4:00 pm"


async def test_a_timed_ask_in_every_state() -> None:
    working = Sources(Schedules([ask()], now={"a1": run("a1", "running")}))
    (h,) = await helpers(working)
    assert (h.state, h.doing, h.since_ms) == ("working", "Remind me at 4", NOW - 3_600_000)
    (h,) = await helpers(Sources(Schedules([ask(paused_ms=NOW)])))
    assert h.state == "paused"
    (h,) = await helpers(Sources(Schedules([ask()]), paused_work={"asks"}))
    assert h.state == "capped"


async def test_a_run_under_way_shows_even_at_the_cap() -> None:
    src = Sources(
        Schedules([routine()], now={"r1": run("r1", "running")}), paused_work={"routines"}
    )
    (h,) = await helpers(src)
    assert h.state == "working"


async def test_a_failed_last_run_keeps_its_reason() -> None:
    last = run("r1", "failed", detail="timed out after 300 s")
    (h,) = await helpers(Sources(Schedules([routine()], last={"r1": last})))
    assert h.last_run == LastRun(
        at_ms=NOW - 3_500_000, outcome="failed", reason="timed out after 300 s"
    )
    assert h.state == "idle"


async def test_a_run_with_nothing_for_the_user_is_nothing() -> None:
    last = run("r1", "nothing", detail="nothing")
    (h,) = await helpers(Sources(Schedules([routine()], last={"r1": last})))
    assert h.last_run == LastRun(at_ms=NOW - 3_500_000, outcome="nothing", reason=None)


@pytest.mark.parametrize(
    ("status", "detail", "outcome", "reason"),
    [
        ("delivered", None, "succeeded", None),
        ("late", None, "succeeded", None),
        ("skipped", "capped", "skipped", "capped"),
        ("skipped", "paused", "skipped", "paused"),
        ("blocked", "no model", "skipped", "no model"),
        ("missed", "more than an hour late", "failed", "more than an hour late"),
        ("interrupted", "interrupted", "failed", "interrupted"),
        ("failed", None, "failed", None),
    ],
)
async def test_each_run_status_has_its_outcome(
    status: RunStatus, detail: str | None, outcome: str, reason: str | None
) -> None:
    last = run("a1", status, detail=detail)
    (h,) = await helpers(Sources(Schedules([ask()], last={"a1": last})))
    assert h.last_run is not None
    assert (h.last_run.outcome, h.last_run.reason) == (outcome, reason)


async def test_a_plain_reminder_is_not_a_helper() -> None:
    assert await helpers(Sources(Schedules([schedule(id="m1")]))) == []


async def test_each_watcher_by_its_name_and_job() -> None:
    src = Sources(watching=[watcher("meetings"), watcher("email"), watcher("payments")])
    found = await helpers(src)
    assert [(h.id, h.kind, h.name) for h in found] == [
        ("watcher:meetings", "watcher", "Meeting heads-ups"),
        ("watcher:email", "watcher", "Urgent email"),
        ("watcher:payments", "watcher", "Payments"),
    ]
    assert all(h.job and h.state == "idle" and h.conversation_id is None for h in found)
    assert all(h.next_ms == NOW + 60_000 for h in found)


async def test_a_watcher_in_every_state() -> None:
    checking = watcher("email", checking_since_ms=NOW - 3000)
    (h,) = await helpers(Sources(watching=[checking]))
    assert (h.state, h.doing, h.since_ms) == ("working", "Reading your inbox", NOW - 3000)
    off = watcher("email", on=False, next_check_ms=None)
    (h,) = await helpers(Sources(watching=[off]))
    assert (h.state, h.next_ms) == ("paused", None)
    (h,) = await helpers(Sources(watching=[watcher("payments")], paused_work={"watchers"}))
    assert h.state == "capped"
    # Turned off outranks the cap: it would not run either way.
    (h,) = await helpers(Sources(watching=[off], paused_work={"watchers"}))
    assert h.state == "paused"


async def test_a_watchers_last_check_or_its_failure() -> None:
    ok = watcher("meetings", last_check_ms=NOW - 60_000)
    (h,) = await helpers(Sources(watching=[ok]))
    assert h.last_run == LastRun(at_ms=NOW - 60_000, outcome="succeeded", reason=None)
    bad = watcher(
        "email", last_check_ms=NOW - 600_000, last_error="signed_out", last_error_ms=NOW - 5000
    )
    (h,) = await helpers(Sources(watching=[bad]))
    assert h.last_run == LastRun(at_ms=NOW - 5000, outcome="failed", reason="signed_out")
    # A failure from before a restart: its time is not known, so the last check stands in.
    old = dataclasses.replace(bad, last_error_ms=None)
    (h,) = await helpers(Sources(watching=[old]))
    assert h.last_run == LastRun(at_ms=NOW - 600_000, outcome="failed", reason="signed_out")
    never = watcher("payments", last_error="AsksFirst")
    (h,) = await helpers(Sources(watching=[never]))
    assert h.last_run is None


async def test_the_order_is_working_by_start_then_the_rest_by_next_run() -> None:
    src = Sources(
        Schedules(
            [
                routine(id="r1", next_due_ms=NOW + 50_000),
                routine(id="r2", next_due_ms=None, paused_ms=NOW),
                ask(id="a1", next_due_ms=NOW + 10_000),
                ask(id="a2", next_due_ms=NOW + 30_000),
            ],
            now={"a2": run("a2", "running", started_ms=NOW - 1000)},
        ),
        watching=[
            watcher("meetings", next_check_ms=NOW + 20_000),
            watcher("email", checking_since_ms=NOW - 5000),
            watcher("payments", on=False, next_check_ms=None),
        ],
        paused_work=set(),
    )
    found = await helpers(src)
    assert [h.id for h in found] == [
        "watcher:email",
        "ask:a2",
        "ask:a1",
        "watcher:meetings",
        "routine:r1",
        "routine:r2",
        "watcher:payments",
    ]


async def test_to_json_is_the_apis_body() -> None:
    h = Helper(
        id="routine:r1",
        kind="routine",
        name="Monday revenue",
        job="Pull revenue",
        state="needs_you",
        doing=None,
        since_ms=None,
        last_run=LastRun(at_ms=5, outcome="failed", reason="no board"),
        next_ms=9,
        waiting=(Waiting(type="approval", id="act1", text="Approve a change"),),
        conversation_id="c1",
    )
    idle = dataclasses.replace(h, id="ask:a1", state="idle", waiting=(), last_run=None)
    assert to_json([h, idle]) == {
        "helpers": [
            {
                "id": "routine:r1",
                "kind": "routine",
                "name": "Monday revenue",
                "job": "Pull revenue",
                "state": "needs_you",
                "doing": None,
                "since_ms": None,
                "last_run": {"at_ms": 5, "outcome": "failed", "reason": "no board"},
                "next_ms": 9,
                "waiting": [{"type": "approval", "id": "act1", "text": "Approve a change"}],
                "conversation_id": "c1",
            },
            {
                "id": "ask:a1",
                "kind": "routine",
                "name": "Monday revenue",
                "job": "Pull revenue",
                "state": "idle",
                "doing": None,
                "since_ms": None,
                "last_run": None,
                "next_ms": 9,
                "waiting": [],
                "conversation_id": "c1",
            },
        ],
        "needs_you": 1,
    }


@dataclass
class Real:
    """The real scheduler store under the list, for what only it can show."""

    store: SqliteScheduleStore
    owner: str = OWNER

    @property
    def schedules(self) -> SqliteScheduleStore:
        return self.store

    async def watchers(self) -> list[WatcherStatus]:
        return []

    async def capped(self) -> Collection[Work]:
        return ()


@pytest.fixture
async def real(tmp_path: Path) -> AsyncIterator[Real]:
    conn = await connect(tmp_path / "t.db")
    try:
        await migrate(conn)
        yield Real(SqliteScheduleStore(conn))
    finally:
        await conn.close()


async def test_a_one_off_ask_leaves_the_list_once_it_has_fired(real: Real) -> None:
    due = ms("2026-11-05T16:00", NY)
    await real.store.create(ask(next_due_ms=due))
    assert [h.id for h in await helpers(real)] == ["ask:a1"]
    await real.store.enqueue("a1", due, None, 120.0, due)
    started = await real.store.claim("w1", due + 5, 0)
    assert started is not None
    (h,) = await helpers(real)
    assert (h.state, h.since_ms, h.next_ms) == ("working", due + 5, None)
    await real.store.finish(started.id, "delivered", due + 10)
    assert await helpers(real) == []


async def test_a_routine_that_came_to_nothing_reads_as_nothing(real: Real) -> None:
    due = ms("2026-11-09T07:00", NY)
    await real.store.create(routine())
    await real.store.enqueue("r1", due, ms("2026-11-16T07:00", NY), 300.0, due)
    started = await real.store.claim("w1", due, 0)
    assert started is not None
    # As the scheduler records a run that called `routine_nothing`.
    await real.store.finish(started.id, "nothing", due + 10, detail="nothing")
    (h,) = await helpers(real)
    assert h.last_run == LastRun(at_ms=due + 10, outcome="nothing", reason=None)
    assert (h.state, h.next_ms) == ("idle", ms("2026-11-16T07:00", NY))
