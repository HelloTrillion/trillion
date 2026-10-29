"""Shared test setup."""

from __future__ import annotations

import aiosqlite.core
import pytest

from trillion.attention import quiet


def _daemon_connection_threads() -> None:
    # aiosqlite's worker thread is not a daemon, so a test that fails before it closes
    # its connection would keep the whole pytest process from exiting. Tests only.
    original = aiosqlite.core.Connection.__init__

    def init(self: aiosqlite.core.Connection, *args: object, **kwargs: object) -> None:
        original(self, *args, **kwargs)  # type: ignore[arg-type]
        self._thread.daemon = True

    aiosqlite.core.Connection.__init__ = init  # type: ignore[method-assign]


_daemon_connection_threads()


@pytest.fixture(autouse=True)
def _cant_tell(monkeypatch: pytest.MonkeyPatch) -> None:
    """The runtime's quiet check never asks this computer's mic or Focus: whatever the
    machine running the tests is doing, can't tell never holds her voice. The probe
    tests call the probes themselves."""
    monkeypatch.setattr(quiet, "SYSTEM_MIC", lambda host: None)
    monkeypatch.setattr(quiet, "SYSTEM_FOCUS", lambda: None)
