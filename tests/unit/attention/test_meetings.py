"""The meetings watcher against a fake calendar and a fake clock: a heads-up due at
start - lead and not before or after, once, the events it skips, the join link, the
attendees it names, and whether a meeting is under way now."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.scheduler.conftest import FakeClock
from tests.unit.attention.test_runner import Door
from trillion.attention.meetings import (
    CALENDAR_PATH,
    CALL_TIMEOUT_S,
    GoogleCalendar,
    Meetings,
    NotConnected,
)
from trillion.attention.runner import AttentionRunner, Find, WatchContext
from trillion.attention.settings import DEFAULT, AttentionSettings
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.attention_store import SqliteAttentionStore
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore

MIN = 60_000
# Thu 29 Oct 2026, 16:00 UTC: 10:00 am in Denver.
START = int(datetime(2026, 10, 29, 16, 0, tzinfo=UTC).timestamp() * 1000)
ZONE = "America/Denver"
ME = {"email": "me@example.com", "self": True, "responseStatus": "accepted"}
ORGANISED = {"email": "me@example.com", "self": True}


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def event(
    eid: str = "e1",
    *,
    start: int = START,
    minutes: int = 30,
    summary: str = "Planning with Sam",
    attendees: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    e: dict[str, Any] = {
        "id": eid,
        "status": "confirmed",
        "summary": summary,
        "start": {"dateTime": iso(start)},
        "end": {"dateTime": iso(start + minutes * MIN)},
        "attendees": attendees
        if attendees is not None
        else [ME, {"email": "sam@example.com", "displayName": "Sam Lee"}],
    }
    e.update(extra)
    return e


@dataclass
class Calendar:
    items: list[dict[str, Any]] = field(default_factory=list)
    on: bool = True
    boom: bool = False
    asked: list[Mapping[str, str]] = field(default_factory=list)

    def connected(self) -> bool:
        return self.on

    async def events(self, params: Mapping[str, str]) -> dict[str, Any]:
        self.asked.append(dict(params))
        if self.boom:
            raise ConnectionError("calendar said: secret title")
        return {"items": list(self.items)}


def watcher(cal: Calendar, clock: FakeClock, zone: str | None = ZONE) -> Meetings:
    return Meetings(cal, clock=clock, zone=lambda: zone)


def ctx(clock: FakeClock, settings: AttentionSettings = DEFAULT) -> WatchContext:
    return WatchContext(
        owner="local",
        now_ms=clock(),
        clock=clock,
        settings=settings,
        notes="",
        cursor=None,
        last_check_ms=None,
        provider=None,
    )


async def finds_at(now: int, *items: dict[str, Any], lead: int = 5) -> list[Find]:
    clock = FakeClock(now)
    w = watcher(Calendar(list(items)), clock)
    return await w.check(ctx(clock, replace(DEFAULT, meeting_lead_min=lead)))


async def test_it_reads_the_primary_calendar_for_what_is_on_and_next() -> None:
    clock = FakeClock(START - 5 * MIN)
    cal = Calendar()
    await watcher(cal, clock).check(ctx(clock))
    (params,) = cal.asked
    # Google filters timeMin by end: what is under way now comes back too.
    assert params == {
        "timeMin": iso(START - 5 * MIN),
        "timeMax": iso(START - 5 * MIN + 16 * MIN),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": "50",
        "eventTypes": "default",
    }


async def test_a_long_lead_reads_far_enough_ahead() -> None:
    clock = FakeClock(START - 30 * MIN)
    cal = Calendar()
    await watcher(cal, clock).check(ctx(clock, replace(DEFAULT, meeting_lead_min=30)))
    assert cal.asked[0]["timeMax"] == iso(START - 30 * MIN + 31 * MIN)


async def test_a_heads_up_is_due_at_start_less_lead_and_not_before() -> None:
    assert await finds_at(START - 5 * MIN - 1, event()) == []
    (f,) = await finds_at(START - 5 * MIN, event())
    assert f.key == "meeting:e1:2026-10-29T16:00:00Z"
    assert f.fresh_until_ms == START
    assert f.title == "Planning with Sam"
    assert f.facts == {
        "title": "Planning with Sam",
        "starts": "10:00 am",
        "minutes_until": 5,
        "with": ["Sam Lee"],
        "has_join_link": False,
    }
    # For the page only: what it was, in the host's words.
    assert f.detail == "Planning with Sam at 10:00 am"


async def test_the_lead_comes_from_settings() -> None:
    assert await finds_at(START - 11 * MIN, event(), lead=10) == []
    (f,) = await finds_at(START - 10 * MIN, event(), lead=10)
    assert f.facts["minutes_until"] == 10


async def test_minutes_until_rounds_up() -> None:
    (f,) = await finds_at(START - 4 * MIN - 30_000, event())
    assert f.facts["minutes_until"] == 5


async def test_one_interval_after_the_start_it_is_still_shown_but_not_fresh() -> None:
    # A read that lands just past the start still has it: shown, unspoken (D9).
    (f,) = await finds_at(START, event())
    assert f.fresh_until_ms == START and f.facts["minutes_until"] == 0
    (f,) = await finds_at(START + MIN - 1, event())
    assert f.facts["minutes_until"] == 0
    assert await finds_at(START + MIN, event()) == []


async def test_with_no_zone_the_start_is_in_the_events_own_time() -> None:
    clock = FakeClock(START - 5 * MIN)
    w = watcher(Calendar([event(start=START)]), clock, zone=None)
    (f,) = await w.check(ctx(clock))
    assert f.facts["starts"] == "4:00 pm"


@pytest.mark.parametrize(
    "skipped",
    [
        event(status="cancelled"),
        event(
            attendees=[
                {**ME, "responseStatus": "declined"},
                {"email": "sam@example.com"},
            ]
        ),
        {
            **event(),
            "start": {"date": "2026-10-29"},
            "end": {"date": "2026-10-30"},
        },
    ],
    ids=["cancelled", "declined", "all-day"],
)
async def test_cancelled_declined_and_all_day_events_are_skipped(skipped: dict[str, Any]) -> None:
    assert await finds_at(START - 5 * MIN, skipped) == []


@pytest.mark.parametrize("kind", ["focusTime", "outOfOffice", "workingLocation", "birthday"])
async def test_events_that_are_not_plain_events_are_skipped(kind: str) -> None:
    assert await finds_at(START - 5 * MIN, event(eventType=kind)) == []
    assert len(await finds_at(START - 5 * MIN, event(eventType="default"))) == 1


@pytest.mark.parametrize(
    "e",
    [
        # An invite nobody answered: anyone can put one on the calendar.
        event(attendees=[{**ME, "responseStatus": "needsAction"}, {"email": "sam@example.com"}]),
        event(attendees=[{"email": "me@example.com", "self": True}, {"email": "sam@example.com"}]),
        # Not on the guest list, and not theirs.
        event(attendees=[{"email": "sam@example.com", "displayName": "Sam"}]),
        event(attendees=[]),
        event(attendees=[], organizer={"email": "sam@example.com"}),
    ],
    ids=["needs-action", "no-answer", "not-a-guest", "no-guests", "someone-elses"],
)
async def test_only_events_the_user_is_going_to_count(e: dict[str, Any]) -> None:
    assert await finds_at(START - 5 * MIN, e) == []
    w, _, _ = await read_at(START + 10 * MIN, e)
    assert w.in_meeting() is False


@pytest.mark.parametrize(
    "e",
    [
        event(attendees=[{**ME, "responseStatus": "tentative"}, {"email": "sam@example.com"}]),
        # Their own: organised, or made on their calendar with nobody invited.
        event(
            attendees=[{**ME, "responseStatus": "needsAction"}, {"email": "sam@example.com"}],
            organizer=ORGANISED,
        ),
        event(attendees=[], organizer=ORGANISED),
        event(attendees=[], creator=ORGANISED),
    ],
    ids=["tentative", "organised-unanswered", "organised", "created"],
)
async def test_maybe_and_the_users_own_events_count(e: dict[str, Any]) -> None:
    assert len(await finds_at(START - 5 * MIN, e)) == 1


async def test_an_unanswered_invite_has_no_link_and_no_heads_up() -> None:
    e = event(
        attendees=[{**ME, "responseStatus": "needsAction"}, {"email": "sam@example.com"}],
        hangoutLink="https://meet.example.com/x",
    )
    assert await finds_at(START - 5 * MIN, e) == []


async def test_one_odd_event_is_skipped_not_the_whole_read() -> None:
    # Its conference data is not what Google sends: reading it raises.
    odd = {**event("odd"), "conferenceData": ["not", "an", "object"]}
    found = await finds_at(START - 5 * MIN, odd, event("e2"))
    assert [f.key.split(":")[1] for f in found] == ["e2"]


async def test_an_offset_start_is_keyed_in_utc() -> None:
    e = event()
    e["start"] = {"dateTime": "2026-10-29T10:00:00-06:00"}
    (f,) = await finds_at(START - 5 * MIN, e)
    assert f.key == "meeting:e1:2026-10-29T16:00:00Z"


async def test_someone_elses_decline_does_not_skip_it() -> None:
    sam = {"email": "sam@example.com", "responseStatus": "declined"}
    assert len(await finds_at(START - 5 * MIN, event(attendees=[ME, sam]))) == 1


@pytest.mark.parametrize(
    ("extra", "link"),
    [
        (
            {"hangoutLink": "https://meet.google.com/abc-defg-hij"},
            "https://meet.google.com/abc-defg-hij",
        ),
        ({"hangoutLink": "http://meet.example.com/x"}, None),
        ({"hangoutLink": "javascript:alert(1)"}, None),
        (
            {
                "conferenceData": {
                    "entryPoints": [
                        {"entryPointType": "phone", "uri": "tel:+1-555-0100"},
                        {"entryPointType": "video", "uri": "https://zoom.example.com/j/1"},
                    ]
                }
            },
            "https://zoom.example.com/j/1",
        ),
        (
            {
                "conferenceData": {
                    "entryPoints": [{"entryPointType": "video", "uri": "zoommtg://join?x=1"}]
                }
            },
            None,
        ),
        ({}, None),
    ],
    ids=["hangout", "http", "javascript", "conference-video", "conference-other-scheme", "none"],
)
async def test_the_join_link_is_kept_only_when_https(
    extra: dict[str, Any], link: str | None
) -> None:
    (f,) = await finds_at(START - 5 * MIN, event(**extra))
    assert f.link == link
    assert f.facts["has_join_link"] is (link is not None)


async def test_attendees_exclude_the_user_and_rooms_and_stop_at_three() -> None:
    people = [
        ME,
        {"email": "ana@example.com", "displayName": "Ana"},
        # No name given: an address is not sent, not even part of it.
        {"email": "bo.smith@example.com"},
        {"email": "room-4@resource.example.com", "resource": True, "displayName": "Room 4"},
        {"email": "ed@example.com", "displayName": "Ed", "responseStatus": "declined"},
        {"email": "cy@example.com", "displayName": "Cy"},
        {"email": "di@example.com", "displayName": "Di"},
        {"email": "fay@example.com", "displayName": "Fay"},
    ]
    (f,) = await finds_at(START - 5 * MIN, event(attendees=people))
    assert f.facts["with"] == ["Ana", "Cy", "Di"]
    assert "bo.smith" not in str(f.facts) and "Ed" not in f.facts["with"]


async def test_the_description_and_location_never_reach_the_facts() -> None:
    e = event(description="Wire the money first.", location="Back room, 4th floor")
    (f,) = await finds_at(START - 5 * MIN, e)
    assert "Wire" not in str(f.facts) and "Back room" not in str(f.facts)


async def test_a_long_title_is_cut_to_one_line() -> None:
    (f,) = await finds_at(START - 5 * MIN, event(summary="Plan\n\n" + "x" * 500))
    assert "\n" not in f.facts["title"] and len(f.facts["title"]) <= 200


# ── In the runner: once, a moved meeting, switched off ──────────────────


@dataclass
class Rig:
    tmp: Path
    clock: FakeClock
    cal: Calendar
    door: Door = field(default_factory=Door)
    settings: AttentionSettings = DEFAULT
    conns: list[Any] = field(default_factory=list)

    async def runner(self) -> tuple[AttentionRunner, Meetings]:
        a, b = await connect(self.tmp / "t.db"), await connect(self.tmp / "t.db")
        await migrate(a)
        self.conns += [a, b]
        r = AttentionRunner(
            SqliteAttentionStore(a),
            SqliteScheduleStore(b),
            self.door,
            clock=self.clock,
            owner="local",
            provider=lambda: None,
            settings=lambda: self.settings,
        )
        w = watcher(self.cal, self.clock)
        r.add(w)
        return r, w


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    r = Rig(tmp_path, FakeClock(START - 5 * MIN), Calendar([event()]))
    try:
        yield r
    finally:
        for c in r.conns:
            await c.close()


async def test_a_heads_up_is_told_once(rig: Rig) -> None:
    r, _ = await rig.runner()
    await r.tick()
    (n,) = rig.door.notices
    assert (n.kind, n.source_id, n.title) == (
        "heads_up",
        "meeting:e1:2026-10-29T16:00:00Z",
        "Meeting soon",
    )
    assert n.fresh_until_ms == START
    rig.clock.advance(MIN)
    await r.tick()
    assert len(rig.cal.asked) == 2 and len(rig.door.notices) == 1


@pytest.mark.parametrize("phase_s", [0, 15, 30, 45, 60])
async def test_reads_75_s_apart_never_miss_the_shortest_lead(rig: Rig, phase_s: int) -> None:
    # A minute's interval on a 15 s tick: reads land 60-75 s apart in real use.
    rig.settings = replace(DEFAULT, meeting_lead_min=2)
    rig.clock = FakeClock(START - 6 * MIN + phase_s * 1000)
    r, _ = await rig.runner()
    while rig.clock() < START + 2 * MIN:
        await r.tick()
        rig.clock.advance(75_000)
    (n,) = rig.door.notices
    assert n.source_id == "meeting:e1:2026-10-29T16:00:00Z"


async def test_a_heads_up_first_read_just_after_the_start_is_shown_not_fresh(rig: Rig) -> None:
    rig.clock = FakeClock(START + 30_000)
    r, _ = await rig.runner()
    await r.tick()
    (n,) = rig.door.notices
    # Past its freshness: the door shows it and does not speak it (D9).
    assert n.fresh_until_ms == START < rig.clock()


async def test_a_moved_meeting_is_a_new_heads_up(rig: Rig) -> None:
    r, _ = await rig.runner()
    await r.tick()
    rig.cal.items = [event(start=START + 10 * MIN)]
    rig.clock.advance(10 * MIN)
    await r.tick()
    assert [n.source_id for n in rig.door.notices] == [
        "meeting:e1:2026-10-29T16:00:00Z",
        "meeting:e1:2026-10-29T16:10:00Z",
    ]


async def test_meetings_switched_off_are_not_read(rig: Rig) -> None:
    rig.settings = replace(DEFAULT, watchers={"meetings": False})
    r, w = await rig.runner()
    await r.tick()
    assert rig.cal.asked == [] and rig.door.notices == []
    assert w.in_meeting() is None


async def test_with_calendar_not_connected_it_is_not_read(rig: Rig) -> None:
    rig.cal.on = False
    r, w = await rig.runner()
    await r.tick()
    assert rig.cal.asked == [] and rig.door.notices == []
    assert w.in_meeting() is None


# ── In a meeting now ────────────────────────────────────────────────────


async def read_at(now: int, *items: dict[str, Any]) -> tuple[Meetings, FakeClock, Calendar]:
    clock = FakeClock(now)
    cal = Calendar(list(items))
    w = watcher(cal, clock)
    await w.check(ctx(clock))
    return w, clock, cal


async def test_in_a_meeting_while_one_with_someone_else_is_under_way() -> None:
    w, _, _ = await read_at(START + 10 * MIN, event())
    assert w.in_meeting() is True


async def test_not_in_a_meeting_when_none_is_under_way() -> None:
    w, _, _ = await read_at(START - 5 * MIN, event())
    assert w.in_meeting() is False
    w, _, _ = await read_at(START + 30 * MIN, event())
    assert w.in_meeting() is False


@pytest.mark.parametrize(
    "e",
    [
        event(attendees=[ME], organizer=ORGANISED),
        event(attendees=[], organizer=ORGANISED),
        event(status="cancelled"),
        event(attendees=[{**ME, "responseStatus": "declined"}, {"email": "sam@example.com"}]),
        event(
            attendees=[ME, {"email": "room@resource.example.com", "resource": True}],
            organizer=ORGANISED,
        ),
        event(
            attendees=[ME, {"email": "sam@example.com", "responseStatus": "declined"}],
            organizer=ORGANISED,
        ),
        {**event(), "start": {"date": "2026-10-29"}, "end": {"date": "2026-10-30"}},
    ],
    ids=[
        "alone",
        "no-attendees",
        "cancelled",
        "declined",
        "only-a-room",
        "the-other-declined",
        "all-day",
    ],
)
async def test_these_are_not_a_meeting_under_way(e: dict[str, Any]) -> None:
    w, _, _ = await read_at(START + 10 * MIN, e)
    assert w.in_meeting() is False


async def test_an_organised_event_with_nobody_else_is_a_heads_up_but_not_a_meeting() -> None:
    e = event(attendees=[], organizer=ORGANISED)
    assert len(await finds_at(START - 5 * MIN, e)) == 1
    w, _, _ = await read_at(START + 10 * MIN, e)
    assert w.in_meeting() is False


@pytest.mark.parametrize(
    "e",
    [
        # Someone else organised it: they are in it, guest list or not.
        event(attendees=[ME]),
        event(attendees=[ME], organizer={"email": "sam@example.com"}),
        # Google left the guest list out.
        event(attendees=[ME], organizer=ORGANISED, attendeesOmitted=True),
        # A guest with no name given still counts.
        event(attendees=[ME, {"email": "sam@example.com"}], organizer=ORGANISED),
    ],
    ids=["no-organiser", "someone-elses", "guests-left-out", "unnamed-guest"],
)
async def test_hidden_guests_still_make_it_a_meeting(e: dict[str, Any]) -> None:
    w, _, _ = await read_at(START + 10 * MIN, e)
    assert w.in_meeting() is True


async def test_it_can_not_tell_before_the_first_read() -> None:
    w = watcher(Calendar([event()]), FakeClock(START + 10 * MIN))
    assert w.in_meeting() is None


async def test_the_last_read_answers_until_it_is_three_minutes_old() -> None:
    w, clock, _ = await read_at(START + 10 * MIN, event())
    clock.advance(3 * MIN)
    assert w.in_meeting() is True
    clock.advance(1)
    assert w.in_meeting() is None


async def test_the_meeting_ends_by_the_clock_between_reads() -> None:
    w, clock, _ = await read_at(START + 29 * MIN, event())
    assert w.in_meeting() is True
    clock.advance(MIN)
    assert w.in_meeting() is False


async def test_a_read_that_fails_keeps_the_last_good_one_while_it_is_fresh() -> None:
    w, clock, cal = await read_at(START + 10 * MIN, event())
    cal.boom = True
    clock.advance(MIN)
    with pytest.raises(ConnectionError):
        await w.check(ctx(clock))
    assert w.in_meeting() is True
    clock.advance(MIN)
    with pytest.raises(ConnectionError):
        await w.check(ctx(clock))
    assert w.in_meeting() is True
    # Three minutes on from the last good read, it no longer says.
    clock.advance(MIN + 1)
    assert w.in_meeting() is None


async def test_a_read_on_its_way_keeps_the_last_good_one() -> None:
    w, clock, cal = await read_at(START + 10 * MIN, event())
    seen: list[Any] = []

    async def slow(params: Mapping[str, str]) -> dict[str, Any]:
        seen.append(w.in_meeting())
        return {"items": []}

    cal.events = slow  # type: ignore[method-assign]
    clock.advance(MIN)
    await w.check(ctx(clock))
    assert seen == [True] and w.in_meeting() is False


async def test_with_calendar_disconnected_since_the_read_it_can_not_tell() -> None:
    w, _, cal = await read_at(START + 10 * MIN, event())
    cal.on = False
    assert w.in_meeting() is None
    # Forgotten, not just hidden: connected again, it waits for a new read.
    cal.on = True
    assert w.in_meeting() is None


# ── The real calendar: the same Google client and request the calendar tools use ──


class _Google:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def call(self, client: object, email: str, method: str, path: str, **kw: Any) -> Any:
        self.calls.append((client, email, method, path, kw))
        return {"items": []}


class _Saved:
    def __init__(self, accounts: list[str]) -> None:
        self.accounts = accounts

    def with_service(self, service: str) -> list[str]:
        return list(self.accounts) if service == "calendar" else []


class _Connection:
    def __init__(self, accounts: list[str], client: object | None = "client") -> None:
        self.saved = _Saved(accounts)
        self.google = _Google()
        self._client = client

    def client(self) -> object | None:
        return self._client


async def test_the_google_calendar_asks_as_the_first_calendar_account() -> None:
    conn = _Connection(["me@example.com", "work@example.com"])
    cal = GoogleCalendar(lambda: conn)  # type: ignore[arg-type, return-value]
    assert cal.connected()
    assert await cal.events({"timeMin": "a"}) == {"items": []}
    assert conn.google.calls == [
        ("client", "me@example.com", "GET", CALENDAR_PATH, {"params": {"timeMin": "a"}})
    ]
    assert CALENDAR_PATH == "/calendar/v3/calendars/primary/events"


async def test_the_google_calendar_gives_up_on_a_slow_google() -> None:
    assert CALL_TIMEOUT_S == 15.0

    class Hangs(_Google):
        async def call(self, *a: Any, **kw: Any) -> Any:
            await asyncio.sleep(10)

    conn = _Connection(["me@example.com"])
    conn.google = Hangs()
    cal = GoogleCalendar(lambda: conn, timeout_s=0.05)  # type: ignore[arg-type, return-value]
    with pytest.raises(TimeoutError):
        await cal.events({})


async def test_the_google_calendar_not_connected() -> None:
    assert not GoogleCalendar(lambda: None).connected()
    none = _Connection([])
    assert not GoogleCalendar(lambda: none).connected()  # type: ignore[arg-type, return-value]
    with pytest.raises(NotConnected):
        await GoogleCalendar(lambda: none).events({})  # type: ignore[arg-type, return-value]
    unset = _Connection(["me@example.com"], client=None)
    with pytest.raises(NotConnected):
        await GoogleCalendar(lambda: unset).events({})  # type: ignore[arg-type, return-value]
