"""The meetings watcher against a fake calendar and a fake clock: a heads-up due at
start - lead and not before or after, once, the events it skips, the join link, the
attendees it names, and whether a meeting is under way now."""

from __future__ import annotations

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


async def test_none_once_it_has_started() -> None:
    assert await finds_at(START, event()) == []
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
        {"email": "bo.smith@example.com"},
        {"email": "room-4@resource.example.com", "resource": True, "displayName": "Room 4"},
        {"email": "cy@example.com", "displayName": "Cy"},
        {"email": "di@example.com", "displayName": "Di"},
    ]
    (f,) = await finds_at(START - 5 * MIN, event(attendees=people))
    assert f.facts["with"] == ["Ana", "bo.smith", "Cy"]


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
        event(attendees=[ME]),
        event(attendees=[]),
        event(status="cancelled"),
        event(attendees=[{**ME, "responseStatus": "declined"}, {"email": "sam@example.com"}]),
        event(
            attendees=[ME, {"email": "room@resource.example.com", "resource": True}],
        ),
        {**event(), "start": {"date": "2026-10-29"}, "end": {"date": "2026-10-30"}},
    ],
    ids=["alone", "no-attendees", "cancelled", "declined", "only-a-room", "all-day"],
)
async def test_these_are_not_a_meeting_under_way(e: dict[str, Any]) -> None:
    w, _, _ = await read_at(START + 10 * MIN, e)
    assert w.in_meeting() is False


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


async def test_a_read_that_fails_raises_and_leaves_it_can_not_tell() -> None:
    w, clock, cal = await read_at(START + 10 * MIN, event())
    cal.boom = True
    clock.advance(MIN)
    with pytest.raises(ConnectionError):
        await w.check(ctx(clock))
    assert w.in_meeting() is None


async def test_with_calendar_disconnected_since_the_read_it_can_not_tell() -> None:
    w, _, cal = await read_at(START + 10 * MIN, event())
    cal.on = False
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


async def test_the_google_calendar_not_connected() -> None:
    assert not GoogleCalendar(lambda: None).connected()
    none = _Connection([])
    assert not GoogleCalendar(lambda: none).connected()  # type: ignore[arg-type, return-value]
    with pytest.raises(NotConnected):
        await GoogleCalendar(lambda: none).events({})  # type: ignore[arg-type, return-value]
    unset = _Connection(["me@example.com"], client=None)
    with pytest.raises(NotConnected):
        await GoogleCalendar(lambda: unset).events({})  # type: ignore[arg-type, return-value]
