"""Roast my week: the true facts of the user's last seven days, read from this machine.

She roasts from these and nothing else, so every line can be checked (PER-10). Only
counts, times of day, conversation titles and her own records are read: never the
text of a message, and nothing from a connected app.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

WEEK_MS = 7 * 24 * 60 * 60 * 1000


@dataclass(frozen=True, kw_only=True)
class Week:
    messages: int = 0
    spoken: int = 0
    typed: int = 0
    conversations: int = 0
    titles: tuple[str, ...] = ()
    busiest_day: str | None = None
    busiest_count: int = 0
    # The latest message sent in the small hours, e.g. "Tuesday at 2:14 am".
    latest_night: str | None = None
    late_nights: int = 0
    interrupted: int = 0
    declined: int = 0
    approved: int = 0
    reminders_set: int = 0
    reminders_cancelled: int = 0
    helpers_made: tuple[str, ...] = ()
    memories: int = 0
    spent_usd: float | None = None
    tokens: int = 0

    @property
    def empty(self) -> bool:
        return self.messages == 0


def zone_of(name: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(name) if name else None
    except Exception:
        return None


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def facts(week: Week) -> list[str]:
    """One plain line per fact worth a line. Nothing is listed that did not happen."""
    across = _plural(week.conversations, "conversation")
    lines = [f"{_plural(week.messages, 'message')} to you across {across}."]
    if week.spoken or week.typed:
        lines.append(f"{_plural(week.spoken, 'turn')} spoken aloud, {week.typed} typed.")
    if week.busiest_day:
        lines.append(f"Busiest day: {week.busiest_day}, {_plural(week.busiest_count, 'message')}.")
    if week.latest_night:
        lines.append(
            f"Up talking to you after midnight on {_plural(week.late_nights, 'night')}; "
            f"the latest was {week.latest_night}."
        )
    if week.titles:
        about = "; ".join(week.titles)
        lines.append(f"What the conversations were about (their titles): {about}.")
    if week.interrupted:
        lines.append(f"They cut you off mid-sentence {_plural(week.interrupted, 'time')}.")
    if week.declined or week.approved:
        lines.append(
            f"You asked for their yes {_plural(week.declined + week.approved, 'time')}: "
            f"{week.approved} approved, {week.declined} turned down."
        )
    if week.reminders_set:
        set_ = _plural(week.reminders_set, "reminder or routine", "reminders and routines")
        gone = week.reminders_cancelled
        lines.append(f"{set_} set" + (f", {gone} of them cancelled." if gone else "."))
    if week.helpers_made:
        lines.append("Helpers they made: " + ", ".join(week.helpers_made) + ".")
    if week.memories:
        lines.append(f"{_plural(week.memories, 'thing')} they asked you to remember.")
    if week.spent_usd is not None:
        lines.append(f"Spent on you this week: ${week.spent_usd:.2f}.")
    return lines


def window(now_ms: int, zone: str) -> tuple[str, str]:
    """The week as two dates for the share card, e.g. ("Oct 31", "Nov 6")."""
    tz = zone_of(zone) or UTC
    end = datetime.fromtimestamp(now_ms / 1000, tz)
    start = end - timedelta(days=6)
    return f"{start:%b} {start.day}", f"{end:%b} {end.day}"
