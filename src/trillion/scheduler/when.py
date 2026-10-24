"""Times for reminders and timed asks: the user's wall clock, turned into instants (AGT-9).

A schedule keeps the local time it was set for and its zone. Every occurrence is
worked out again from those, so 9:00 stays 9:00 across a daylight-saving change.
Pure: no clock and no I/O.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import cache
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

Every = Literal["day", "weekday", "week", "month"]
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_LOCAL = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$")
# How far ahead an occurrence is looked for: every rule comes round within a month.
_HORIZON_DAYS = 400
# The longest a clock change has ever skipped is two hours; three is plenty.
_GAP_MINUTES = 180


class WhenError(ValueError):
    """A time, zone or repeat that can't be used. The message is safe to show her."""


@dataclass(frozen=True, kw_only=True)
class Repeat:
    every: Every
    days: tuple[str, ...] = ()
    day: int | None = None

    def to_json(self) -> dict[str, Any]:
        if self.every == "week":
            return {"every": "week", "days": list(self.days)}
        if self.every == "month":
            return {"every": "month", "day": self.day}
        return {"every": self.every}

    def matches(self, d: date) -> bool:
        if self.every == "day":
            return True
        if self.every == "weekday":
            return d.weekday() < 5
        if self.every == "week":
            return DAYS[d.weekday()] in self.days
        # Past the month's end means its last day: the 31st in February is the 28th.
        last = calendar.monthrange(d.year, d.month)[1]
        return d.day == min(self.day or 1, last)


def read_repeat(raw: object) -> Repeat | None:
    """A repeat as she gives it, checked. None means once."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping) or set(raw) - {"every", "days", "day"}:
        raise WhenError('repeat must look like {"every": "day"}')
    every = raw.get("every")
    if every == "week":
        days = raw.get("days")
        if not isinstance(days, list) or not days or any(d not in DAYS for d in days):
            raise WhenError('a weekly repeat needs its days, such as ["mon", "thu"]')
        return Repeat(every="week", days=tuple(d for d in DAYS if d in days))
    if every == "month":
        day = raw.get("day")
        if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 31:
            raise WhenError("a monthly repeat needs a day from 1 to 31")
        return Repeat(every="month", day=day)
    if every == "day":
        return Repeat(every="day")
    if every == "weekday":
        return Repeat(every="weekday")
    raise WhenError("repeat every must be day, weekday, week or month")


def zone_of(name: str) -> ZoneInfo:
    """The zone for a name, by its canonical spelling (the same on every system)."""
    if not isinstance(name, str) or not name.strip():
        raise WhenError("a timezone name is needed")
    wanted = name.strip()
    # A case-insensitive file system accepts 'america/new_york'; Linux does not.
    # Only the names zoneinfo lists count, so every system agrees.
    key = _zone_keys().get(wanted.lower())
    if key is None:
        raise WhenError(f"{name!r} is not a timezone this computer knows")
    try:
        return ZoneInfo(key)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise WhenError(f"{name!r} is not a timezone this computer knows") from None


@cache
def _zone_keys() -> dict[str, str]:
    return {k.lower(): k for k in available_timezones()}


def known(name: str) -> bool:
    try:
        zone_of(name)
    except WhenError:
        return False
    return True


def _naive(text: str) -> datetime:
    m = _LOCAL.match(text.strip())
    if m is None:
        raise WhenError("when must be a local date and time: YYYY-MM-DDTHH:MM")
    year, month, day, hour, minute, second = (int(g) for g in m.groups(default="0"))
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        raise WhenError("that date or time does not exist") from None


def _real(naive: datetime, tz: ZoneInfo) -> datetime:
    """The instant a wall-clock time means. One that a spring change skips moves on to
    the first real minute after it; one that happens twice in autumn is the first."""
    here = naive
    for _ in range(_GAP_MINUTES + 1):
        aware = here.replace(tzinfo=tz, fold=0)
        if aware.astimezone(UTC).astimezone(tz).replace(tzinfo=None) == here:
            return aware
        here = (here + timedelta(minutes=1)).replace(second=0)
    raise WhenError("that time does not exist in this timezone")


def to_ms(dt: datetime) -> int:
    return round(dt.timestamp() * 1000)


def parse_local(text: str, zone: str) -> datetime:
    """`YYYY-MM-DDTHH:MM` (seconds allowed) as a time in `zone`."""
    return _real(_naive(text), zone_of(zone))


def next_after(at_local: str, repeat: Repeat | None, zone: str, after_ms: int) -> int | None:
    """The next occurrence strictly after `after_ms`, in UTC ms; None when a one-off has
    passed. Every past occurrence is skipped, however many there were (D3)."""
    tz = zone_of(zone)
    start = _naive(at_local)
    if repeat is None:
        ms = to_ms(_real(start, tz))
        return ms if ms > after_ms else None
    after = datetime.fromtimestamp(after_ms / 1000, tz).replace(tzinfo=None)
    day = max(start.date(), after.date() - timedelta(days=1))
    for _ in range(_HORIZON_DAYS):
        candidate = datetime.combine(day, start.time())
        if candidate >= start and repeat.matches(day):
            try:
                found = to_ms(_real(candidate, tz))
            except WhenError:
                found = None  # a day with no real minute (Samoa, 2011): on to the next
            if found is not None and found > after_ms:
                return found
        day += timedelta(days=1)
    raise WhenError("that repeat never comes round")


def clock_words(dt: datetime) -> str:
    """'4:00 pm'."""
    return f"{dt.hour % 12 or 12}:{dt.minute:02d} {'am' if dt.hour < 12 else 'pm'}"


def day_words(dt: datetime) -> str:
    """'Sat 24 Oct'."""
    return f"{_DAY_NAMES[dt.weekday()]} {dt.day} {_MONTHS[dt.month - 1]}"


def spoken(ms: int, zone: str) -> str:
    """'Sat 24 Oct, 4:00 pm': an instant as the user reads their own clock."""
    dt = datetime.fromtimestamp(ms / 1000, zone_of(zone))
    return f"{day_words(dt)}, {clock_words(dt)}"


def _ordinal(n: int) -> str:
    end = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{end}"


def repeat_words(repeat: Repeat | None) -> str:
    """'every weekday', 'every Mon and Thu', 'every month on the 15th', or 'once'."""
    if repeat is None:
        return "once"
    if repeat.every == "week":
        names = [_DAY_NAMES[DAYS.index(d)] for d in repeat.days]
        listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        return f"every {listed}"
    if repeat.every == "month":
        day = repeat.day or 1
        tail = " (or the month's last day)" if day > 28 else ""
        return f"every month on the {_ordinal(day)}{tail}"
    return f"every {repeat.every}"


def summary(at_local: str, repeat: Repeat | None, zone: str) -> str:
    """'Every weekday at 9:00 am', or 'Sat 24 Oct at 4:00 pm' for a one-off."""
    if repeat is None:
        dt = parse_local(at_local, zone)
        return f"{day_words(dt)} at {clock_words(dt)}"
    words = repeat_words(repeat)
    return f"{words[0].upper()}{words[1:]} at {clock_words(_naive(at_local))}"
