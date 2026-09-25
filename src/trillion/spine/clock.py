"""Time as epoch milliseconds, injectable so tests control expiry."""

from __future__ import annotations

import time
from collections.abc import Callable

Clock = Callable[[], int]


def system_clock() -> int:
    return time.time_ns() // 1_000_000
