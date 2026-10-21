"""Ctrl+C stops Trillion cleanly, however many copies of it arrive."""

from __future__ import annotations

import signal

import pytest
import uvicorn

from trillion.cli import server as server_module
from trillion.cli.server import SAME_PRESS_S, Server


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [100.0]
    monkeypatch.setattr(server_module.time, "monotonic", lambda: now[0])
    return now


def make() -> Server:
    return Server(uvicorn.Config(app=lambda scope, receive, send: None))


def test_one_press_delivered_twice_is_one_graceful_stop(clock: list[float]) -> None:
    """The terminal and `uv` each deliver the same Ctrl+C."""
    s = make()
    s.handle_exit(signal.SIGINT, None)
    clock[0] += SAME_PRESS_S / 5
    s.handle_exit(signal.SIGINT, None)
    assert s.should_exit and not s.force_exit


def test_a_second_press_made_on_purpose_stops_at_once(clock: list[float]) -> None:
    s = make()
    s.handle_exit(signal.SIGINT, None)
    clock[0] += SAME_PRESS_S * 3
    s.handle_exit(signal.SIGINT, None)
    assert s.force_exit


def test_nothing_is_raised_again_after_stopping(clock: list[float]) -> None:
    """uvicorn would raise the caught signal again once stopped, landing while the
    database is closing. There is nothing left for it to raise."""
    s = make()
    for sig in (signal.SIGINT, signal.SIGTERM):
        s.handle_exit(sig, None)
    assert getattr(s, "_captured_signals", []) == []
