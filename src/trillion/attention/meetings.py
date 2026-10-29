"""The meetings watcher (the speaking up spec, Meetings): a heads-up before each meeting,
and whether one is under way now, for the quiet check.

Every minute it reads the primary Google calendar through the client and account the
calendar tools use. Google's `timeMin` bounds an event's end, not its start, so one
read from now returns what is under way as well as what is next. It skips all-day
events, cancelled ones, ones that are not plain events (focus time, out of office,
working location), and every one the user has not said yes or maybe to: anyone can put
an invite on someone's calendar, so an unanswered one is no heads-up, no link and no
meeting. An event counts when the user accepted it or said maybe, organised it, or
made it on their own calendar with nobody invited.

A heads-up is due from start - lead, and is still taken up to one interval after the
start, since reads land 60-75 s apart and one may fall just past it; it is spoken only
until the start (D9). It is keyed by event and start so a moved meeting is a new one
(D5). Its detail, for the page only, is "<title> at
<start>"; the notice's own title stays "Meeting soon", safe on a lock screen. The
model sees the title, when, who else (at most three names) and whether there is a join
link: never the description or the place, which are longer, someone else's words, and
not needed for one line.

The last good read is kept in memory, so the quiet check costs no request. A read on
its way or one that failed leaves it as it was; it answers only while it is three
minutes fresh, and never with Calendar disconnected (D11).
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from trillion.attention.probes import Answer
from trillion.attention.runner import JSON, Find, WatchContext
from trillion.attention.settings import AttentionSettings
from trillion.scheduler import when
from trillion.scheduler.store import https_link
from trillion.spine.clock import Clock

if TYPE_CHECKING:
    from trillion.connectors.google.connection import GoogleConnection

logger = logging.getLogger(__name__)

NAME = "meetings"
TITLE = "Meeting soon"
INTERVAL_S = 60.0
CALENDAR_PATH = "/calendar/v3/calendars/primary/events"
# Under the runner's 20 s limit, so a slow Google is this watcher's failure, not a hang.
CALL_TIMEOUT_S = 15.0
# How far ahead it reads: at least the lead, and a minute more so a meeting starting
# exactly at lead is not missed between two checks.
AHEAD_MIN = 15
MAX_RESULTS = 50
# A read older than three checks no longer says whether she is in a meeting.
FRESH_MS = 3 * 60_000
MAX_NAMES = 3
MAX_TITLE = 200
MAX_NAME = 80


class NotConnected(Exception):
    """Calendar is not connected, or Google sign-in is not set up."""


class Calendar(Protocol):
    def connected(self) -> bool:
        """Some account has granted Calendar. Cheap: asked on every tick."""
        ...

    async def events(self, params: Mapping[str, str]) -> JSON:
        """One `events.list` on the primary calendar."""
        ...


class GoogleCalendar:
    """The primary calendar of the first account with Calendar, as the calendar tools
    choose by default, through the same Google client."""

    def __init__(
        self,
        google: Callable[[], GoogleConnection | None],
        *,
        timeout_s: float = CALL_TIMEOUT_S,
    ) -> None:
        self._google = google
        self._timeout = timeout_s

    def _account(self) -> tuple[GoogleConnection, str] | None:
        conn = self._google()
        if conn is None:
            return None
        accounts = conn.saved.with_service("calendar")
        return (conn, accounts[0]) if accounts else None

    def connected(self) -> bool:
        return self._account() is not None

    async def events(self, params: Mapping[str, str]) -> JSON:
        found = self._account()
        if found is None:
            raise NotConnected
        conn, account = found
        # Read when asked, as the tools do: the sign-in may change.
        client = conn.client()
        if client is None:
            raise NotConnected
        reply = await asyncio.wait_for(
            conn.google.call(client, account, "GET", CALENDAR_PATH, params=dict(params)),
            self._timeout,
        )
        return reply if isinstance(reply, dict) else {}


@dataclass(frozen=True, kw_only=True)
class _Event:
    id: str
    start_ms: int
    end_ms: int
    start: datetime
    title: str
    names: tuple[str, ...]
    # Someone else is in it: a guest who has not declined, an organiser who is not the
    # user, or a guest list Google left out.
    shared: bool
    link: str | None


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _one_line(value: object, limit: int) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _time(raw: object) -> datetime | None:
    """A timed start or end; None for an all-day one (a `date`, no `dateTime`)."""
    if not isinstance(raw, dict) or not isinstance(raw.get("dateTime"), str):
        return None
    try:
        dt = datetime.fromisoformat(raw["dateTime"])
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None


# What the user's own answer must be for an invite to count.
_GOING = frozenset({"accepted", "tentative"})


@dataclass(frozen=True)
class _People:
    # The user's own answer, or None when the guest list has no entry for them.
    mine: str | None
    # Others who have not declined, by the name they gave: an address is not shown.
    names: tuple[str, ...]
    # Whether anyone else who has not declined is on the list, named or not.
    others: bool


def _people(raw: object) -> _People:
    """The user's answer and who else is coming. Rooms are not people; nor is anyone
    who declined."""
    mine: str | None = None
    names: list[str] = []
    others = False
    for a in raw if isinstance(raw, list) else []:
        if not isinstance(a, dict):
            continue
        if a.get("self") is True:
            answer = a.get("responseStatus")
            mine = answer if isinstance(answer, str) else "needsAction"
            continue
        if a.get("resource") is True or a.get("responseStatus") == "declined":
            continue
        others = True
        name = a.get("displayName")
        if isinstance(name, str) and name.strip():
            names.append(_one_line(name, MAX_NAME))
    return _People(mine=mine, names=tuple(names), others=others)


def _is_me(raw: object) -> bool:
    return isinstance(raw, dict) and raw.get("self") is True


def _link(e: Mapping[str, Any]) -> str | None:
    link = e.get("hangoutLink")
    if not link:
        points = (e.get("conferenceData") or {}).get("entryPoints") or []
        link = next(
            (
                p.get("uri")
                for p in points
                if isinstance(p, dict) and p.get("entryPointType") == "video"
            ),
            None,
        )
    if not isinstance(link, str):
        return None
    # Someone else's invite, opened by a tap: https or nothing.
    try:
        return https_link(link)
    except ValueError:
        return None


def _event(e: object) -> _Event | None:
    """An event the user is going to, or None: all-day, cancelled, not a plain event,
    declined or unanswered, unreadable."""
    if not isinstance(e, dict) or e.get("status") == "cancelled" or not e.get("id"):
        return None
    kind = e.get("eventType")
    if kind is not None and kind != "default":
        return None
    start, end = _time(e.get("start")), _time(e.get("end"))
    if start is None or end is None:
        return None
    people = _people(e.get("attendees"))
    organiser = _is_me(e.get("organizer"))
    if people.mine == "declined":
        return None
    if people.mine is None:
        # No guest entry for the user: only their own event counts.
        going = organiser or _is_me(e.get("creator"))
    else:
        going = people.mine in _GOING or organiser
    if not going:
        return None
    return _Event(
        id=str(e["id"]),
        start_ms=int(start.timestamp() * 1000),
        end_ms=int(end.timestamp() * 1000),
        start=start,
        title=_one_line(e.get("summary") or "(no title)", MAX_TITLE),
        names=people.names,
        shared=people.others or not organiser or e.get("attendeesOmitted") is True,
        link=_link(e),
    )


class Meetings:
    name = NAME
    title = TITLE
    interval_s = INTERVAL_S

    def __init__(
        self,
        calendar: Calendar,
        *,
        clock: Clock,
        zone: Callable[[], str | None],
    ) -> None:
        self.calendar = calendar
        self._clock = clock
        self._zone = zone
        # The last good read: when, and the events in it. None until one succeeds, and
        # again once Calendar is disconnected.
        self._read: tuple[int, list[_Event]] | None = None

    def enabled(self, settings: AttentionSettings) -> bool:
        return settings.on(NAME) and self.calendar.connected()

    async def check(self, ctx: WatchContext) -> list[Find]:
        now = ctx.now_ms
        lead_ms = ctx.settings.meeting_lead_min * 60_000
        late_ms = int(self.interval_s * 1000)
        ahead_min = max(AHEAD_MIN, ctx.settings.meeting_lead_min) + 1
        # The last good read stands while this one is on its way, and if it fails: how
        # fresh it is decides whether it still says anything.
        found = await self.calendar.events(
            {
                "timeMin": _iso(now),
                "timeMax": _iso(now + ahead_min * 60_000),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": str(MAX_RESULTS),
                # Not focus time, out of office or working location.
                "eventTypes": "default",
            }
        )
        items = found.get("items")
        events: list[_Event] = []
        for raw in items if isinstance(items, list) else []:
            # One odd event is skipped, not the whole read.
            try:
                if (e := _event(raw)) is not None:
                    events.append(e)
            except Exception as err:
                logger.info("meetings: an event skipped: %s", type(err).__name__)
        self._read = (now, events)
        # Up to one interval late, so a check that lands just past the start still
        # shows it (unspoken, since it is fresh only until the start).
        return [
            self._find(e, now) for e in events if e.start_ms - lead_ms <= now < e.start_ms + late_ms
        ]

    def _find(self, e: _Event, now: int) -> Find:
        starts = when.clock_words(self._local(e.start))
        return Find(
            key=f"meeting:{e.id}:{_iso(e.start_ms)}",
            title=e.title,
            # Shown on the page only, so a notice with no line still says which meeting.
            detail=f"{e.title} at {starts}",
            facts={
                "title": e.title,
                "starts": starts,
                "minutes_until": max(0, math.ceil((e.start_ms - now) / 60_000)),
                "with": list(e.names[:MAX_NAMES]),
                "has_join_link": e.link is not None,
            },
            fresh_until_ms=e.start_ms,
            link=e.link,
        )

    def _local(self, dt: datetime) -> datetime:
        """In the user's zone; else as the calendar gave it."""
        zone = self._zone()
        if zone:
            try:
                return dt.astimezone(when.zone_of(zone))
            except when.WhenError:
                pass
        return dt

    def in_meeting(self) -> Answer:
        """True while a timed meeting with someone else is under way, by the last good
        read; None when there is none, it is stale, or Calendar is not connected."""
        try:
            if not self.calendar.connected():
                self._read = None
                return None
        except Exception as e:
            logger.info("meetings: can't tell if connected: %s", type(e).__name__)
            return None
        if self._read is None:
            return None
        read_ms, events = self._read
        now = self._clock()
        if now - read_ms > FRESH_MS:
            return None
        return any(e.shared and e.start_ms <= now < e.end_ms for e in events)
