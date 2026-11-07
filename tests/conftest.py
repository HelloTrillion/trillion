"""Shared test setup."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from typing import Any

import aiosqlite.core
import pytest

from trillion.attention import quiet
from trillion.delivery import notify
from trillion.secrets import store as secret_store


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


@pytest.fixture(autouse=True)
def _no_system_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never show a real notification on the machine running them. The door calls
    the real tool by default (`shutil.which` finds osascript), so that path finds
    nothing here; a test that passes its own `which` still gets the real command."""
    real = notify.command

    def command(*args: Any, which: Any, **kwargs: Any) -> Any:
        return None if which is shutil.which else real(*args, which=which, **kwargs)

    monkeypatch.setattr(notify, "command", command)


@pytest.fixture(autouse=True)
def _fresh_secret_registry() -> Iterator[None]:
    """Each test starts with no stored values known to the log scrubber."""
    secret_store.forget_all()
    yield
    secret_store.forget_all()


@pytest.fixture(autouse=True)
def _no_enrolled_voice(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Nobody's voice is enrolled unless a test enrols one: the voiceprint of the
    machine running the tests is never read (§5.3a)."""
    from trillion import paths

    monkeypatch.setattr(paths, "voiceprint_path", lambda: tmp_path / "voiceprint.json")
