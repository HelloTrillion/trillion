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

import contextlib
import os
import signal
import threading
import time
from types import FrameType
from typing import Any, BinaryIO

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


def stop_when_closed(stream: BinaryIO, server: Any) -> threading.Thread:
    """Stop the server when `stream` ends. The desktop app holds the other end of the
    server's input and never writes: when the app goes, even by a force quit, the
    input closes and the server stops the way Ctrl+C stops it.

    It reads its own copy of the file descriptor, straight from the OS: a thread still
    waiting in `stream.read` when Python exits holds the stream's lock, and closing
    stdin at exit then aborts the process (a crash report on every quit)."""
    try:
        fd: int | None = os.dup(stream.fileno())
    except (OSError, ValueError):
        fd = None  # a stand-in input with no descriptor (a test harness): read it as is

    def read() -> bytes:
        return os.read(fd, 4096) if fd is not None else stream.read(4096)

    def wait() -> None:
        try:
            while read():
                pass
        except (OSError, ValueError):
            pass
        finally:
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)
        server.should_exit = True

    thread = threading.Thread(target=wait, name="desktop-lifeline", daemon=True)
    thread.start()
    return thread
