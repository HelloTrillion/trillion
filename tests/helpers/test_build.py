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
from trillion.helpers.look import pick
from trillion.helpers.model import Helper, LastRun, RoutineLine, Waiting, to_json
from trillion.helpers.pausing import pause_helper
from trillion.helpers.store import OwnHelper
from trillion.scheduler.runner import BUSY_DETAIL
from trillion.scheduler.store import Run, RunStatus, Schedule
from trillion.scheduler.when import Repeat
from trillion.spend.types import Work
from trillion.spine.confirm import PendingAction, Status
from trillion.spine.contract import Risk
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.helper_store import SqliteHelperStore
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
        switched_on=True,
        connected=True,
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
    by_helper: dict[str, Run] = field(default_factory=dict)

    async def active(self, owner_id: str) -> list[Schedule]:
        return [s for s in self.schedules if s.owner_id == owner_id]

    async def last_results(self, owner_id: str) -> dict[str, Run]:
        return dict(self.last)

    async def helper_last_results(self, owner_id: str) -> dict[str, Run]:
        return dict(self.by_helper)

    async def running(self, owner_id: str, now_ms: int) -> dict[str, Run]:
        assert now_ms == NOW
        return dict(self.now)


@dataclass
class Sources:
    schedules: Schedules = field(default_factory=Schedules)
    watching: list[WatcherStatus] = field(default_factory=list)
    paused_work: set[Work] = field(default_factory=set)
    open_cards: list[PendingAction] = field(default_factory=list)
    talks: dict[str, str] = field(default_factory=dict)
    mine: list[OwnHelper] = field(default_factory=list)
    owner: str = OWNER

    def now_ms(self) -> int:
        return NOW

    async def own(self) -> list[OwnHelper]:
        return list(self.mine)

    async def cards(self) -> list[PendingAction]:
        return list(self.open_cards)

    async def conversations(self) -> dict[str, str]:
        return dict(self.talks)

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
    # "Working", not its own name again: the row already says that.
    assert (h.state, h.doing, h.since_ms) == ("working", "Working", NOW - 12_000)


async def test_a_paused_routine_is_paused_with_no_next_run() -> None:
    (h,) = await helpers(Sources(Schedules([routine(paused_ms=NOW - 1)])))
    assert (h.state, h.next_ms) == ("paused", None)


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
    assert (h.state, h.doing, h.since_ms) == ("working", "Working", NOW - 3_600_000)
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
        ("skipped", "capped", "skipped", "paused at the spend cap"),
        ("skipped", "paused", "skipped", "paused"),
        ("skipped", "already built", "skipped", "already done today"),
        ("skipped", "something new", "skipped", "something new"),
        # Blocked: it waits to be tried again, so the helper itself is idle.
        ("blocked", "no model", "skipped", "no model set up"),
        ("blocked", BUSY_DETAIL, "skipped", "busy, will try again"),
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
    assert h.state == "idle"


async def test_a_plain_reminder_is_not_a_helper() -> None:
    assert await helpers(Sources(Schedules([schedule(id="m1")]))) == []


async def test_each_watcher_by_its_name_and_job() -> None:
    src = Sources(watching=[watcher("meetings"), watcher("email"), watcher("payments")])
    found = await helpers(src)
    assert [(h.id, h.kind, h.name) for h in found] == [
        ("watcher:meetings", "watcher", "Meetings"),
        ("watcher:email", "watcher", "Urgent email"),
        ("watcher:payments", "watcher", "Payments"),
    ]
    assert all(h.job and h.state == "idle" and h.conversation_id is None for h in found)
    assert all(h.next_ms == NOW + 60_000 for h in found)


async def test_a_watcher_in_every_state() -> None:
    checking = watcher("email", checking_since_ms=NOW - 3000)
    (h,) = await helpers(Sources(watching=[checking]))
    assert (h.state, h.doing, h.since_ms) == ("working", "Reading your inbox", NOW - 3000)
    off = watcher("email", on=False, switched_on=False, next_check_ms=None)
    (h,) = await helpers(Sources(watching=[off]))
    assert (h.state, h.next_ms) == ("paused", None)
    (h,) = await helpers(Sources(watching=[watcher("payments")], paused_work={"watchers"}))
    assert h.state == "capped"
    # Turned off outranks the cap: it would not run either way.
    (h,) = await helpers(Sources(watching=[off], paused_work={"watchers"}))
    assert h.state == "paused"
    # A check already under way when the cap came is still working.
    (h,) = await helpers(Sources(watching=[checking], paused_work={"watchers"}))
    assert h.state == "working"


async def test_a_watchers_last_check_or_its_failure() -> None:
    ok = watcher("meetings", last_check_ms=NOW - 60_000)
    (h,) = await helpers(Sources(watching=[ok]))
    assert h.last_run == LastRun(at_ms=NOW - 60_000, outcome="succeeded", reason=None)
    bad = watcher(
        "email", last_check_ms=NOW - 600_000, last_error="signed_out", last_error_ms=NOW - 5000
    )
    (h,) = await helpers(Sources(watching=[bad]))
    assert h.last_run == LastRun(at_ms=NOW - 5000, outcome="failed", reason="signed out of Google")
    # A failure from before a restart: its time is not known, so the last check stands in.
    old = dataclasses.replace(bad, last_error_ms=None)
    (h,) = await helpers(Sources(watching=[old]))
    assert h.last_run == LastRun(
        at_ms=NOW - 600_000, outcome="failed", reason="signed out of Google"
    )
    never = watcher("payments", last_error="AsksFirst")
    (h,) = await helpers(Sources(watching=[never]))
    assert h.last_run is None


@pytest.mark.parametrize(
    ("name", "kind", "words"),
    [
        ("meetings", "unreachable", "couldn't reach the calendar"),
        ("email", "ConnectionError", "couldn't reach the inbox"),
        ("payments", "StripeError", "couldn't reach Stripe"),
        ("email", "NotConnected", "Google isn't connected"),
        ("payments", "AsksFirst", "Stripe is set to ask first"),
        ("meetings", "TimeoutError", "timed out"),
        ("email", "KeyError", "something went wrong"),
    ],
)
async def test_a_watchers_failure_is_told_in_plain_words(name: str, kind: str, words: str) -> None:
    bad = watcher(name, last_error=kind, last_error_ms=NOW)
    (h,) = await helpers(Sources(watching=[bad]))
    assert h.last_run == LastRun(at_ms=NOW, outcome="failed", reason=words)


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
            watcher("payments", on=False, switched_on=False, next_check_ms=None),
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
        waiting=(Waiting(type="approval", id="act1", text="Approve a change", at_ms=3),),
        conversation_id="c1",
    )
    idle = dataclasses.replace(h, id="ask:a1", kind="ask", state="idle", waiting=(), last_run=None)
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
                "waiting": [
                    {"type": "approval", "id": "act1", "text": "Approve a change", "at_ms": 3}
                ],
                "conversation_id": "c1",
                "paused": False,
                "switched_on": None,
                "connected": None,
                "notes": None,
                "routines": [],
                "icon": None,
                "color": None,
            },
            {
                "id": "ask:a1",
                "kind": "ask",
                "name": "Monday revenue",
                "job": "Pull revenue",
                "state": "idle",
                "doing": None,
                "since_ms": None,
                "last_run": None,
                "next_ms": 9,
                "waiting": [],
                "conversation_id": "c1",
                "paused": False,
                "switched_on": None,
                "connected": None,
                "notes": None,
                "routines": [],
                "icon": None,
                "color": None,
            },
        ],
        "needs_you": 1,
    }


@dataclass
class Real:
    """The real scheduler store under the list, for what only it can show."""

    store: SqliteScheduleStore
    made: SqliteHelperStore
    owner: str = OWNER
    now: int = NOW

    @property
    def schedules(self) -> SqliteScheduleStore:
        return self.store

    def now_ms(self) -> int:
        return self.now

    async def watchers(self) -> list[WatcherStatus]:
        return []

    async def cards(self) -> list[PendingAction]:
        return []

    async def conversations(self) -> dict[str, str]:
        return {}

    async def capped(self) -> Collection[Work]:
        return ()

    async def own(self) -> list[OwnHelper]:
        return await self.made.all(self.owner)


@pytest.fixture
async def real(tmp_path: Path) -> AsyncIterator[Real]:
    conn = await connect(tmp_path / "t.db")
    try:
        await migrate(conn)
        yield Real(SqliteScheduleStore(conn), SqliteHelperStore(conn))
    finally:
        await conn.close()


async def test_a_one_off_ask_leaves_the_list_once_it_has_fired(real: Real) -> None:
    due = ms("2026-11-05T16:00", NY)
    await real.store.create(ask(next_due_ms=due))
    assert [h.id for h in await helpers(real)] == ["ask:a1"]
    await real.store.enqueue("a1", due, None, 120.0, due)
    started = await real.store.claim("w1", due + 5, 0)
    assert started is not None
    real.now = due + 6
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


# ── What the page acts on, said outright (review B1, B2) ────────────────


async def test_a_routine_says_whether_the_user_paused_it() -> None:
    src = Sources(Schedules([routine(), routine(id="r2", paused_ms=NOW)]), paused_work={"routines"})
    found = await helpers(src)
    # Capped is not paused: Pause, not Resume, is what the user can do.
    assert (one(found, "routine:r1").state, one(found, "routine:r1").paused) == ("capped", False)
    assert one(found, "routine:r2").paused is True
    assert one(found, "routine:r1").switched_on is None
    assert one(found, "routine:r1").connected is None


async def test_a_watcher_says_whether_it_is_switched_on_and_connected() -> None:
    meetings = watcher("meetings", on=False, connected=False, next_check_ms=None)
    off = watcher("payments", on=False, switched_on=False, next_check_ms=None)
    found = await helpers(Sources(watching=[meetings, off, watcher("email")]))
    m = one(found, "watcher:meetings")
    # Switched on, waiting for Calendar: not running, but not turned off either.
    assert (m.state, m.switched_on, m.connected, m.paused) == ("paused", True, False, False)
    p = one(found, "watcher:payments")
    assert (p.switched_on, p.connected) == (False, True)
    e = one(found, "watcher:email")
    assert (e.state, e.switched_on, e.connected) == ("idle", True, True)


# ── Your helpers (the your helpers spec, Managing) ──────────────────────


def mine(**kw: Any) -> OwnHelper:
    base = OwnHelper(
        id="h1",
        owner_id=OWNER,
        name="Invoices",
        job="Chases overdue invoices",
        notes="Leave out the March ones.",
        conversation_id="conv-h1",
        paused_ms=None,
        created_at=NOW - 86_400_000,
        icon="receipt",
        color="teal",
    )
    return dataclasses.replace(base, **kw)


def card_in(conversation_id: str, **kw: Any) -> PendingAction:
    base = PendingAction(
        id="p1",
        owner_id=OWNER,
        tool="file_create",
        args={"path": "/x"},
        args_hash="h",
        summary="Create the sheet",
        target="/x",
        risk=Risk.IRREVERSIBLE,
        recoverable=True,
        session_id=conversation_id,
        created_turn=1,
        status=Status.PROPOSED,
        created_at=NOW - 60_000,
        expires_at=NOW + 600_000,
        represented_from=None,
    )
    return dataclasses.replace(base, **kw)


def handoff(**kw: Any) -> Schedule:
    return ask(
        **{"id": "x1", "title": "Invoices: who", "helper_id": "h1", "next_due_ms": NOW, **kw}
    )


def its_routine(**kw: Any) -> Schedule:
    base = {"id": "hr1", "title": "Overdue check", "helper_id": "h1", "conversation_id": "conv-h1"}
    return routine(**{**base, **kw})


async def test_an_idle_helper_of_yours_names_its_job_notes_and_chat() -> None:
    (h,) = await helpers(Sources(mine=[mine()]))
    assert h == Helper(
        id="own:h1",
        kind="own",
        name="Invoices",
        job="Chases overdue invoices",
        state="idle",
        doing=None,
        since_ms=None,
        last_run=None,
        next_ms=None,
        waiting=(),
        conversation_id="conv-h1",
        notes="Leave out the March ones.",
        icon="receipt",
        color="teal",
    )
    assert h.to_json()["routines"] == [] and h.to_json()["notes"] == "Leave out the March ones."
    assert (h.to_json()["icon"], h.to_json()["color"]) == ("receipt", "teal")


async def test_a_helper_of_yours_in_every_state() -> None:
    (h,) = await helpers(Sources(mine=[mine(paused_ms=NOW - 1)]))
    assert (h.state, h.paused, h.switched_on, h.connected) == ("paused", True, None, None)
    (h,) = await helpers(Sources(mine=[mine()], paused_work={"helpers"}))
    assert (h.state, h.paused) == ("capped", False)
    # Paused outranks the cap: it would not run either way.
    (h,) = await helpers(Sources(mine=[mine(paused_ms=NOW)], paused_work={"helpers"}))
    assert h.state == "paused"
    working = Sources(
        Schedules([its_routine()], now={"hr1": run("hr1", "running", started_ms=NOW - 9000)}),
        mine=[mine()],
        paused_work={"helpers"},
    )
    (h,) = await helpers(working)
    assert (h.state, h.doing, h.since_ms) == ("working", "Working", NOW - 9000)
    card = card_in("conv-h1", id="act1", created_at=NOW - 500)
    (h,) = await helpers(Sources(mine=[mine()], open_cards=[card]))
    assert h.state == "needs_you"
    assert h.waiting == (
        Waiting(type="approval", id="act1", text="Approve a change", at_ms=NOW - 500),
    )


async def test_its_routines_sit_under_it_and_are_not_listed_twice() -> None:
    src = Sources(
        Schedules(
            [
                its_routine(),
                its_routine(id="hr2", title="Weekly sum", paused_ms=NOW - 5),
                routine(),
            ],
            by_helper={"h1": run("hr1", "failed", detail="timed out after 300 s")},
        ),
        mine=[mine()],
    )
    found = await helpers(src)
    assert sorted(h.id for h in found) == ["own:h1", "routine:r1"]
    h = one(found, "own:h1")
    assert h.routines == (
        RoutineLine(id="hr1", name="Overdue check", next_ms=ms("2026-11-09T07:00"), paused=False),
        RoutineLine(id="hr2", name="Weekly sum", next_ms=None, paused=True),
    )
    assert h.to_json()["routines"][1] == {
        "id": "hr2",
        "name": "Weekly sum",
        "next_ms": None,
        "paused": True,
    }
    # Its next run is its routines' soonest; its last run is its schedules' latest.
    assert h.next_ms == ms("2026-11-09T07:00")
    assert h.last_run == LastRun(
        at_ms=NOW - 3_500_000, outcome="failed", reason="timed out after 300 s"
    )
    # A paused helper has no next run, and its routines show none either.
    src.mine = [mine(paused_ms=NOW)]
    paused = one(await helpers(src), "own:h1")
    assert paused.next_ms is None
    assert [r.next_ms for r in paused.routines] == [None, None]


async def test_a_handoff_in_progress_shows_its_helper_working_and_no_row_of_its_own() -> None:
    src = Sources(
        Schedules([handoff()], now={"x1": run("x1", "running", started_ms=NOW - 2000)}),
        mine=[mine()],
    )
    (h,) = await helpers(src)
    assert (h.id, h.state, h.doing, h.since_ms) == ("own:h1", "working", "Working", NOW - 2000)
    # Waiting to run, it is no row either, and its helper is not yet working.
    src.schedules.now = {}
    (h,) = await helpers(src)
    assert (h.id, h.state, h.routines) == ("own:h1", "idle", ())
    # A handoff of a helper since deleted is no row at all.
    src.mine = []
    assert await helpers(src) == []


async def test_a_waiting_handoff_is_not_its_helpers_next_run() -> None:
    later = ms("2026-11-09T07:00")
    src = Sources(Schedules([handoff(), its_routine(next_due_ms=later)]), mine=[mine()])
    (h,) = await helpers(src)
    assert h.next_ms == later
    src.schedules.schedules = [handoff()]
    (h,) = await helpers(src)
    assert h.next_ms is None


async def test_your_helpers_take_their_place_in_the_order() -> None:
    src = Sources(
        Schedules(
            [routine(id="r1", next_due_ms=NOW + 50_000), its_routine(next_due_ms=NOW + 10_000)],
        ),
        mine=[mine(), mine(id="h2", name="Travel", conversation_id="conv-h2")],
        watching=[watcher("email", checking_since_ms=NOW - 5000)],
    )
    found = await helpers(src)
    assert [h.id for h in found] == ["watcher:email", "own:h1", "routine:r1", "own:h2"]


async def test_the_real_store_keeps_a_helpers_last_run_across_its_schedules(real: Real) -> None:
    made = await real.made.create(OWNER, "Invoices", "Chases overdue invoices", NOW)
    assert isinstance(made, OwnHelper)
    due = ms("2026-11-05T16:00", NY)
    await real.store.create(its_routine(helper_id=made.id, next_due_ms=due + 86_400_000))
    for sid, at in (("x1", due), ("x2", due + 1000)):
        await real.store.create(handoff(id=sid, helper_id=made.id, next_due_ms=at))
        await real.store.enqueue(sid, at, None, 120.0, at)
        started = await real.store.claim("w1", at + 5, 0)
        assert started is not None
        if sid == "x2":
            real.now = at + 6
            (h,) = await helpers(real)
            assert (h.id, h.state, h.since_ms) == (f"own:{made.id}", "working", at + 5)
        await real.store.finish(started.id, "delivered", at + 10)
    real.now = due + 2000
    (h,) = await helpers(real)
    # Finished handoffs are gone from the scheduler's active list, yet still its last run.
    assert h.last_run == LastRun(at_ms=due + 1010, outcome="succeeded", reason=None)
    assert [r.id for r in h.routines] == ["hr1"] and h.next_ms == due + 86_400_000


async def test_a_helpers_last_run_is_its_runs_first_even_after_a_long_handoff(
    real: Real,
) -> None:
    made = await real.made.create(OWNER, "Invoices", "Chases overdue invoices", NOW)
    assert isinstance(made, OwnHelper)
    due = ms("2026-11-05T16:00", NY)
    await real.store.create(handoff(helper_id=made.id, next_due_ms=due))
    await real.store.create(its_routine(helper_id=made.id, next_due_ms=due + 1000))
    # The handoff starts first and finishes last, after a routine run in between.
    await real.store.enqueue("x1", due, None, 600.0, due)
    long = await real.store.claim("w1", due, 0)
    await real.store.enqueue("hr1", due + 1000, due + 86_400_000, 300.0, due + 1000)
    short = await real.store.claim("w2", due + 1000, 0)
    assert long is not None and short is not None and short.id > long.id
    await real.store.finish(short.id, "nothing", due + 2000, detail="nothing")
    await real.store.finish(long.id, "delivered", due + 9000)
    real.now = due + 10_000
    (h,) = await helpers(real)
    assert h.last_run == LastRun(at_ms=due + 9000, outcome="succeeded", reason=None)
    (first, _), *_ = await real.store.helper_history(OWNER, made.id, 10)
    assert first.id == long.id
    # A helper deleted has no last run kept for it.
    await real.made.delete(OWNER, made.id, due + 11_000)
    assert await real.store.helper_last_results(OWNER) == {}


async def test_a_handoff_a_pause_cancelled_is_no_run(real: Real) -> None:
    made = await real.made.create(OWNER, "Invoices", "Chases overdue invoices", NOW)
    assert isinstance(made, OwnHelper)
    await real.store.create(handoff(helper_id=made.id, next_due_ms=NOW))
    await real.store.enqueue("x1", NOW, None, 120.0, NOW)
    assert await pause_helper(real.made, real.store, OWNER, made.id, NOW + 1)
    assert await real.store.helper_history(OWNER, made.id, 10) == []
    assert await real.store.helper_last_results(OWNER) == {}
    (h,) = await helpers(real)
    assert (h.state, h.last_run) == ("paused", None)


async def test_a_stored_picture_not_on_the_lists_never_reaches_the_page() -> None:
    (h,) = await helpers(Sources(mine=[mine(icon="<b>", color="neon")]))
    assert (h.icon, h.color) == pick("Invoices", "Chases overdue invoices")
