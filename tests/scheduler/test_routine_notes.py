"""Setting up a routine and changing it (the routines spec, AGT-7): ask_schedule with
`routine` shows a card that says so; in the routine's conversation a narrowing stands
at once and anything new is a card; nowhere else, with nobody watching, from an ask
or after an outside read; and the next run is told the notes."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.scheduler.conftest import NY, FakeClock, ms, schedule
from tests.scheduler.test_routine_runner import BOARD, Routines
from tests.scheduler.test_runner import make
from trillion.scheduler.ask import routine_request
from trillion.scheduler.store import NOTES_MAX, Schedule
from trillion.scheduler.when import Repeat
from trillion.spine.confirm import Refusal
from trillion.spine.contract import ToolCall, ToolContext
from trillion.spine.deny_floor import DenyPolicy
from trillion.spine.registry import ToolRegistry
from trillion.spine.router import Router
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore
from trillion.storage.sqlite.spine_store import SqliteSpineStore
from trillion.tools.routine import (
    NARROW,
    WIDEN,
    RoutineNarrow,
    RoutineWiden,
    for_session,
    narrows_routine,
)
from trillion.tools.schedule import AskSchedule, ScheduleList

NOW = ms("2026-10-24T12:00")
HERE = "own-1"
CTX = ToolContext(owner_id="local", session_id=HERE, turn=1, call_id="t1", via="text")
NARROWS = "Don't include refunds."
WIDENS = "Also include the support queue."


def own(**kw: Any) -> Schedule:
    base: dict[str, Any] = {
        "id": "0a0b0c0d",
        "kind": "ask",
        "title": "Monday revenue",
        "body": "Show me last week's revenue.",
        "repeat": Repeat(every="week", days=("mon",)),
        "target": "own",
        "conversation_id": HERE,
    }
    return schedule(**{**base, **kw})


class Places:
    """What each conversation is: its kind, and whether it is today's day."""

    def __init__(self) -> None:
        self.known: dict[str, tuple[str, bool]] = {
            HERE: ("routine", False),
            "day-today": ("day", True),
            "day-before": ("day", False),
            "chat-1": ("chat", False),
        }

    async def __call__(self, conversation_id: str) -> tuple[str, bool]:
        return self.known.get(conversation_id, ("chat", False))


class Setup:
    def __init__(self, store: SqliteScheduleStore, router: Router) -> None:
        self.store, self.router = store, router
        self.clock = FakeClock(NOW)
        self.ask, self.list = AskSchedule(), ScheduleList()
        self.narrow, self.widen = RoutineNarrow(), RoutineWiden()
        for tool in (self.ask, self.list):
            tool.bind(store, zone=lambda: NY, clock=self.clock, owner="local")
        for notes in (self.narrow, self.widen):
            notes.bind(store, Places(), "local")

    async def notes(self, routine_id: str = "0a0b0c0d") -> str:
        s = await self.store.get(routine_id)
        assert s is not None
        return s.notes


@pytest.fixture
async def it(tmp_path: Path) -> AsyncIterator[Setup]:
    spine_conn = await connect(tmp_path / "t.db")
    await migrate(spine_conn)
    schedule_conn = await connect(tmp_path / "t.db")
    store = SqliteScheduleStore(schedule_conn)
    registry = ToolRegistry()
    router = Router(registry, SqliteSpineStore(spine_conn), policy=DenyPolicy.default())
    setup = Setup(store, router)
    router._clock = setup.clock
    for tool in (setup.ask, setup.list, setup.narrow, setup.widen):
        registry.register(tool)
    yield setup
    await schedule_conn.close()
    await spine_conn.close()


def call(name: str, args: dict[str, Any]) -> ToolCall:
    return ToolCall(name=name, args=args)


def notes_args(text: str, routine: str = "0a0b0c0d") -> dict[str, Any]:
    return {"routine": routine, "text": text}


# ── Setting one up ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("routine", "lands"),
    [("day", "on Your day"), ("own", "in its own conversation")],
)
async def test_a_routine_is_set_up_on_a_card_that_says_so(
    it: Setup, routine: str, lands: str
) -> None:
    args = {
        "when": "2026-10-26T08:00",
        "ask": "build my day",
        "title": "Build my day",
        "repeat": {"every": "weekday"},
        "routine": routine,
    }
    card = await it.router.execute(call("ask_schedule", args), CTX)
    assert card.status == "confirmation_required"
    assert card.summary == (
        f'Every weekday at 8:00 am: build my day (a routine, "Build my day", {lands})'
    )
    assert await it.store.active("local") == []
    done = await it.router.resolve(
        card.pending_id or "", "confirm", dataclasses.replace(CTX, via="tap", turn=2)
    )
    assert done.status == "succeeded" and "routine" in done.content
    (s,) = await it.store.active("local")
    assert (s.kind, s.target, s.conversation_id, s.notes) == ("ask", routine, None, "")
    listed = (await it.list.execute({}, CTX)).content
    assert f"(routine, {lands})" in listed


async def test_a_plain_timed_ask_has_no_target(it: Setup) -> None:
    args = {"when": "2026-10-26T09:00", "ask": "Check the oven.", "title": "Oven"}
    card = await it.router.execute(call("ask_schedule", args), CTX)
    assert card.summary is not None and card.summary.endswith(": Check the oven.")
    await it.router.resolve(
        card.pending_id or "", "confirm", dataclasses.replace(CTX, via="tap", turn=2)
    )
    (s,) = await it.store.active("local")
    assert s.target is None


@pytest.mark.parametrize("bad", ["weekly", "", "DAY", 1, None])
async def test_a_routine_that_is_neither_day_nor_own_is_refused(it: Setup, bad: Any) -> None:
    args = {"when": "2026-10-26T09:00", "ask": "x", "title": "X", "routine": bad}
    card = await it.router.execute(call("ask_schedule", args), CTX)
    assert card.status == "refused" and card.pending_id is None
    # Called directly, past the router's schema, the tool refuses it too.
    pre = await it.ask.precheck(args, CTX)
    if bad is not None:
        assert not pre.ok and '"day" or "own"' in pre.reason
        assert not (await it.ask.execute(args, CTX)).ok
    assert await it.store.active("local") == []


# ── Narrowing ────────────────────────────────────────────────────────


async def test_a_narrowing_stands_at_once_and_is_read_back(it: Setup) -> None:
    await it.store.create(own())
    done = await it.router.execute(call(NARROW, notes_args(NARROWS)), CTX)
    assert done.status == "succeeded"
    assert 'Noted for "Monday revenue": Don\'t include refunds.' in done.content
    assert await it.notes() == "Don't include refunds."
    again = await it.router.execute(call(NARROW, notes_args("Never include charts.")), CTX)
    assert again.status == "succeeded"
    assert await it.notes() == "Don't include refunds.\nNever include charts."


@pytest.mark.parametrize(
    "text",
    [WIDENS, "Never skip the support queue.", "Stop showing refunds unless they are big."],
)
async def test_text_that_widens_is_refused_as_a_narrowing(it: Setup, text: str) -> None:
    await it.store.create(own())
    done = await it.router.execute(call(NARROW, notes_args(text)), CTX)
    assert done.status == "refused" and done.reason is Refusal.PRECONDITION_FAILED
    assert "this adds to the routine; use routine_widen" in done.detail
    # The tool asks again itself, whatever got past its check.
    direct = await it.narrow.execute(notes_args(text), CTX)
    assert not direct.ok and "use routine_widen" in direct.content
    assert await it.notes() == ""


# ── Widening ─────────────────────────────────────────────────────────


async def test_a_widening_is_a_card_and_stands_once_approved(it: Setup) -> None:
    await it.store.create(own())
    card = await it.router.execute(call(WIDEN, notes_args(WIDENS)), CTX)
    assert card.status == "confirmation_required"
    assert card.summary == f"Add to Monday revenue: {WIDENS}"
    assert await it.notes() == ""
    tap = dataclasses.replace(CTX, via="tap", turn=2)
    done = await it.router.resolve(card.pending_id or "", "confirm", tap)
    assert done.status == "succeeded"
    assert await it.notes() == WIDENS


# ── Where, and who ───────────────────────────────────────────────────


@pytest.mark.parametrize("via", ["schedule", "routine", "ask", "refresh", "channel"])
@pytest.mark.parametrize("name", [NARROW, WIDEN])
async def test_never_with_nobody_there_or_from_an_ask(it: Setup, via: str, name: str) -> None:
    await it.store.create(own())
    ctx = dataclasses.replace(CTX, via=via, routine_target=HERE)  # type: ignore[arg-type]
    text = NARROWS if name == NARROW else WIDENS
    r = await it.router.execute(call(name, notes_args(text)), ctx)
    assert r.status == "refused" and r.pending_id is None
    tool = it.narrow if name == NARROW else it.widen
    assert not (await tool.precheck(notes_args(text), ctx)).ok
    assert not (await tool.execute(notes_args(text), ctx)).ok
    assert await it.notes() == ""


@pytest.mark.parametrize("name", [NARROW, WIDEN])
async def test_never_after_an_outside_read(it: Setup, name: str) -> None:
    await it.store.create(own())
    ctx = dataclasses.replace(CTX, read_outside=True)
    text = NARROWS if name == NARROW else WIDENS
    r = await it.router.execute(call(name, notes_args(text)), ctx)
    assert r.status == "refused" and r.pending_id is None
    assert "after reading" in r.detail
    tool = it.narrow if name == NARROW else it.widen
    assert not (await tool.execute(notes_args(text), ctx)).ok
    assert await it.notes() == ""


@pytest.mark.parametrize("where", ["chat-1", "day-today", "own-2"])
@pytest.mark.parametrize("name", [NARROW, WIDEN])
async def test_never_from_a_conversation_it_does_not_belong_to(
    it: Setup, where: str, name: str
) -> None:
    await it.store.create(own())
    ctx = dataclasses.replace(CTX, session_id=where)
    text = NARROWS if name == NARROW else WIDENS
    r = await it.router.execute(call(name, notes_args(text)), ctx)
    assert r.status == "refused" and "not this routine's conversation" in r.detail
    tool = it.narrow if name == NARROW else it.widen
    assert not (await tool.execute(notes_args(text), ctx)).ok
    assert await it.notes() == ""


async def test_a_day_routine_is_changed_in_todays_day_conversation(it: Setup) -> None:
    await it.store.create(own(target="day", conversation_id=None))
    today = dataclasses.replace(CTX, session_id="day-today")
    assert (await it.router.execute(call(NARROW, notes_args(NARROWS)), today)).status == (
        "succeeded"
    )
    before = dataclasses.replace(CTX, session_id="day-before")
    r = await it.router.execute(call(NARROW, notes_args("Never include charts.")), before)
    assert r.status == "refused"
    assert await it.notes() == NARROWS


@pytest.mark.parametrize("routine", ["0a0b0c0e", "a1b2c3d4"])
async def test_no_such_routine(it: Setup, routine: str) -> None:
    await it.store.create(own())
    # A reminder is no routine, and neither is an id nobody has.
    await it.store.create(schedule(conversation_id=HERE))
    r = await it.router.execute(call(NARROW, notes_args(NARROWS, routine)), CTX)
    assert r.status == "refused" and "no such routine" in r.detail


async def test_the_notes_stop_at_their_cap_and_say_so(it: Setup) -> None:
    await it.store.create(own(notes="x" * (NOTES_MAX - 10)))
    r = await it.router.execute(call(NARROW, notes_args(NARROWS)), CTX)
    assert r.status == "failed" and "full" in r.content
    assert await it.notes() == "x" * (NOTES_MAX - 10)
    over = await it.narrow.execute(notes_args("Never include y."), CTX)
    assert not over.ok and over.error_kind == "full"
    assert await it.store.add_note("local", "0a0b0c0d", "Never a.")  # 9 with the break
    assert len(await it.notes()) == NOTES_MAX - 1


# ── Offered where it belongs ─────────────────────────────────────────


def test_offered_only_where_a_routine_belongs() -> None:
    routines = [own(), own(id="0e0e0e0e", target="day", conversation_id=None)]
    assert for_session("routine", HERE, routines, today=False) == {NARROW, WIDEN}
    assert for_session("day", "day-today", routines, today=True) == {NARROW, WIDEN}
    assert for_session("day", "day-before", routines, today=False) == frozenset()
    assert for_session("chat", "chat-1", routines, today=False) == frozenset()
    # No day routine: the day conversation offers nothing.
    assert for_session("day", "day-today", [own()], today=True) == frozenset()
    # A cancelled routine is changed nowhere.
    gone = [own(status="cancelled")]
    assert for_session("routine", HERE, gone, today=False) == frozenset()


# ── The next run ─────────────────────────────────────────────────────


class Seen(Routines):
    """Remembers the schedule each run was handed, as the runner read it."""

    def __init__(self) -> None:
        super().__init__(BOARD, place=HERE)
        self.handed: list[Schedule] = []

    async def build(self, s: Schedule, conversation_id: str) -> Any:
        self.handed.append(s)
        return await super().build(s, conversation_id)


async def test_the_next_run_is_told_the_notes(it: Setup) -> None:
    due = ms("2026-10-26T08:00")
    await it.store.create(own(next_due_ms=due))
    assert (await it.router.execute(call(NARROW, notes_args(NARROWS)), CTX)).status == ("succeeded")
    seen = Seen()
    sched, _, _ = make(it.store, due, routines=seen)
    await sched.tick()
    (s,) = seen.handed
    assert s.notes == NARROWS
    assert f"Follow these notes from the user: {NARROWS}" in routine_request(s)


# ── What narrows a routine ───────────────────────────────────────────

NARROWINGS = [
    "stop showing refunds",
    "less detail",
    "never mention X",
    "never mention test payments",
    "skip weekends",
    "hide the refunds chart",
    "drop the sparkline",
    "skip anything under $100",
    "fewer charts",
    "don't include test payments",
    "Do not show the support queue.",
    "Please hide the chart.",
    "No refunds.",
    "Drop the forecast",
    "without the footnotes",
    "exclude trial accounts",
]
WIDENINGS = [
    # Removing a limit is widening, and a comma or a full stop can chain a second order.
    "skip the cap",
    "drop the cap on charges",
    "less caution",
    "less conservative",
    "no caps",
    "without the cap",
    "no threshold",
    "drop the minimum",
    "stop showing stripe, show slack",
    "stop showing refunds. show slack",
    "stop showing refunds and also send it to Jo",
    "Also include the support queue.",
    "show me the support queue too",
    "include more charts",
    "don't include refunds, include new signups",
    "only the top five plus churn",
    "stop showing refunds, add churn",
    "never mention X, email me the rest",
    "less detail and post it to the team",
    "skip refunds, read example.com instead",
    "skip refunds, look at https://example.com",
    "only what jo@example.com sent",
    "leave out weekends and buy more",
    "drop the forecast, share it with Sam",
    "exclude trials and forward the rest",
    "stop and call Alex",
    "never delete anything",
    "only book meetings",
    "no charts, message Jo",
    "fewer charts, pay the invoice",
    "only include another source",
    "skip it and cancel the order",
    # A second "not" turns it round, and an exception lets more back in.
    "Never skip the support queue.",
    "don't leave out weekends",
    "Stop showing refunds unless they are big.",
    "only refunds, except when they are small",
    "leave out weekends",
    # Each of these reads more, or lets back in what was left out (the review's).
    "don't forget to check my Gmail inbox too",
    "never miss a Slack mention",
    "stop ignoring refunds",
    "stop omitting refunds",
    "stop leaving refunds out",
    "stop filtering out refunds",
    "stop limiting it to Stripe",
    "drop the limit on sources",
    "less detail, more Slack threads",
    "fewer charts and more Linear issues",
    "only look at my Gmail and Slack",
    "only show Linear issues from every team",
    "no limit on how many sources you read",
    "without the refund filter",
    "hide nothing",
    "no filters",
    "don't just look at Stripe, look at Linear",
    "stop showing refunds; read all my Slack DMs",
    "drop refunds and read Notion",
    "exclude refunds; read Notion",
    "only invoices",
    "never show refunds: read Notion instead",
    "skip the charts or the tables",
    "stop showing refunds, then send the rest",
    "stop showing refunds as well",
    "skip charts in the entire report",
    "never show the whole list of anything",
    "skip one two three four five six seven eight nine ten",
    # A second narrowing or negating word turns it round (the review's probe).
    "stop skipping slack",
    "no fewer than ten charts",
    "less hiding of slack",
    "drop the slack exclusion",
    "never show fewer than 50 items",
    "stop dropping slack threads",
    "without gmail exclusions",
    "fewer exclusions",
    "less strict",
    "only 100 slack channels",
    # A double negative after a verb opener (the final review's).
    "stop overlooking slack",
    "stop neglecting my slack",
    "stop disregarding slack messages",
    "stop dismissing slack",
    "stop suppressing slack alerts",
    "stop withholding details",
    "don't hold back",
    "stop truncating the list",
    # After a noun opener, no verb that could turn it round.
    "skip the shared ones",
    "less hidden stuff",
    "no keeping it short",
    "fewer charts, let the rest in",
    # Any "only" goes to a card, even a count.
    "only the top five",
    "only 3 charts",
    "",
]


# A short stem counts only as one of its own words: "bank" is not "ban", "notes" not
# "not" (the review's). Every form of the stem itself still counts.
@pytest.mark.parametrize(
    "text",
    [
        "Stop telling me about the bank newsletters",
        "stop showing bank fees",
        "skip bank holidays",
        "never mention my notes",
        "stop showing the cute charts",
        "hide the caption",
    ],
)
def test_a_word_that_only_starts_like_a_short_stem_still_narrows(text: str) -> None:
    assert narrows_routine(text)


@pytest.mark.parametrize(
    "text",
    [
        "stop showing banned words",
        "stop banning slack",
        "skip the ban",
        "no bans",
        "stop showing none of it",
        "stop showing nothing",
        "never mention hidden rows",
        "stop cutting slack",
        "don't cut slack",
        "skip the capped ones",
    ],
)
def test_every_form_of_a_short_stem_still_counts(text: str) -> None:
    assert not narrows_routine(text)


@pytest.mark.parametrize("text", NARROWINGS)
def test_the_spec_s_narrowings_narrow(text: str) -> None:
    assert narrows_routine(text)


@pytest.mark.parametrize("text", WIDENINGS)
def test_anything_that_could_widen_does_not(text: str) -> None:
    assert not narrows_routine(text)


@pytest.mark.parametrize("text", ["stop showing refunds", "less detail", "never mention X"])
async def test_a_spec_narrowing_stands_at_once(it: Setup, text: str) -> None:
    await it.store.create(own())
    done = await it.router.execute(call(NARROW, notes_args(text)), CTX)
    assert done.status == "succeeded"
    assert await it.notes() == text


async def test_a_mixed_one_is_refused_as_a_narrowing(it: Setup) -> None:
    await it.store.create(own())
    text = "stop showing refunds and also send it to Jo"
    done = await it.router.execute(call(NARROW, notes_args(text)), CTX)
    assert done.status == "refused" and "use routine_widen" in done.detail
    assert await it.notes() == ""


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("less detail", "less detail."),
        ("drop the sparkline", "drop the sparkline."),
        ("skip anything under $100", "skip anything under $100."),
        ("without the footnotes.", "without the footnotes."),
    ],
)
async def test_a_narrowing_is_read_back_as_said(it: Setup, text: str, said: str) -> None:
    await it.store.create(own())
    done = await it.router.execute(call(NARROW, notes_args(text)), CTX)
    assert done.status == "succeeded"
    assert f'Noted for "Monday revenue": {said}' in done.content
    assert "I'll never" not in done.content


async def test_in_the_day_only_the_routine_it_names_is_changed(it: Setup) -> None:
    # Two day routines: the day conversation belongs to the first; the other is refused.
    await it.store.create(own(id="0a0b0c0d", target="day", conversation_id=None, created_ms=1))
    await it.store.create(own(id="0e0e0e0e", target="day", conversation_id=None, created_ms=2))
    today = dataclasses.replace(CTX, session_id="day-today")
    first = await it.router.execute(call(NARROW, notes_args(NARROWS, "0a0b0c0d")), today)
    other = await it.router.execute(call(NARROW, notes_args(NARROWS, "0e0e0e0e")), today)
    assert first.status == "succeeded"
    assert other.status == "refused" and "not this routine's conversation" in other.detail
    assert await it.notes("0e0e0e0e") == ""


async def test_a_paused_routine_is_listed_as_paused(it: Setup) -> None:
    await it.store.create(own(next_due_ms=ms("2026-10-26T08:00")))
    assert await it.store.pause("local", "0a0b0c0d", NOW)
    listed = (await it.list.execute({}, CTX)).content
    assert "paused" in listed and "next " not in listed
