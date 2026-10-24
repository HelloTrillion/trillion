"""Shared test setup."""

from __future__ import annotations

import aiosqlite.core


def _daemon_connection_threads() -> None:
    # aiosqlite's worker thread is not a daemon, so a test that fails before it closes
    # its connection would keep the whole pytest process from exiting. Tests only.
    original = aiosqlite.core.Connection.__init__

    def init(self: aiosqlite.core.Connection, *args: object, **kwargs: object) -> None:
        original(self, *args, **kwargs)  # type: ignore[arg-type]
        self._thread.daemon = True

    aiosqlite.core.Connection.__init__ = init  # type: ignore[method-assign]


_daemon_connection_threads()
