"""The web server `trillion run` starts, stopped the way a person means it.

uvicorn catches Ctrl+C, shuts down, and then raises the signal again so the
program ends as if it had never been caught. Two things go wrong with that here:

- In a terminal, Ctrl+C reaches both `uv` and Trillion, and `uv` passes its own
  copy on: one press arrives twice. uvicorn reads the second as "stop right now"
  and drops open connections instead of closing them.
- The signal raised again lands while Trillion is still closing its database,
  cancels that, and the user is shown a page of tracebacks for a clean stop.

So a repeat within a moment is the same press, and nothing is raised again: the
server stops, `trillion run` closes what it opened and says so. A second press
made on purpose, later, still stops it at once.
"""

from __future__ import annotations

import signal
import time
from types import FrameType

import uvicorn

# Closer together than this, two Ctrl+C are one press delivered twice.
SAME_PRESS_S = 0.5


class Server(uvicorn.Server):
    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(config)
        self._first_stop: float | None = None

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        now = time.monotonic()
        first = self._first_stop
        if first is not None and sig == signal.SIGINT and now - first < SAME_PRESS_S:
            return
        if first is None:
            self._first_stop = now
        super().handle_exit(sig, frame)
        # uvicorn keeps the signal to raise again once it has stopped. The stop is
        # already handled, so there is nothing to raise.
        captured = getattr(self, "_captured_signals", None)
        if isinstance(captured, list):
            captured.clear()
