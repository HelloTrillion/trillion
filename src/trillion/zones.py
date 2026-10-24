"""The IANA timezone names this computer knows, shared by setup and the scheduler."""

from __future__ import annotations

import zoneinfo
from functools import cache


@cache
def names() -> dict[str, str]:
    """Every IANA zone this machine knows, by lower-case name. Empty when the machine has
    no zone data at all (Windows needs the `tzdata` package): then nothing can be checked."""
    return {z.lower(): z for z in zoneinfo.available_timezones()}
