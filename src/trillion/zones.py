"""The IANA timezone names this computer knows, shared by setup and the scheduler, and
the month a moment falls in, shared by the usage pill and the spend ledger."""

from __future__ import annotations

import zoneinfo
from datetime import datetime
from functools import cache


@cache
def names() -> dict[str, str]:
    """Every IANA zone this machine knows, by lower-case name. Empty when the machine has
    no zone data at all (Windows needs the `tzdata` package): then nothing can be checked."""
    return {z.lower(): z for z in zoneinfo.available_timezones()}


def month_start(now_ms: int, zone: str) -> tuple[str, int]:
    """The month `now_ms` falls in, and when it began: midnight on the 1st in `zone`,
    or in this machine's local time when no zone is set."""
    # A naive datetime is local time, and .timestamp() reads it back as local time.
    now = datetime.fromtimestamp(now_ms / 1000, zoneinfo.ZoneInfo(zone) if zone else None)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return f"{start:%Y-%m}", int(start.timestamp() * 1000)
