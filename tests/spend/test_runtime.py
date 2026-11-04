"""The runtime builds the spend ledger and gate, and loads them before it serves (BRN-7)."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from tests.api.test_server import App, home, make  # noqa: F401
from tests.api.test_usage import NOW, in_chicago, read
from trillion.api.runtime import OWNER, Runtime, open_runtime
from trillion.brain.types import Usage
from trillion.persona import setup
from trillion.secrets.store import MemoryStore
from trillion.spend.gate import PREF, Caps
from trillion.zones import month_start


async def _record(rt: Runtime, at: int, cost: float) -> None:
    await rt.conversations.record_usage(
        owner_id=OWNER,
        conversation_id=None,
        provider="anthropic",
        model="claude-test",
        usage=Usage(input_tokens=100),
        cost_usd=cost,
        now_ms=at,
        provider_id="anthropic",
        caller="background",
    )


def test_the_ledger_and_caps_are_loaded_before_the_runtime_is_handed_out(
    make: Any, home: Path
) -> None:
    app: App = make(model=False)
    portal = app.client.portal
    assert portal is not None
    now = app.rt.clock()
    portal.call(_record, app.rt, now, 50.0)
    caps = {"anthropic": {"soft": 20, "hard": 50, "warn_pct": 80}}
    portal.call(app.rt.preferences.put, OWNER, PREF, caps, now)

    async def reopen() -> tuple[float, str, Caps]:
        again = await open_runtime(secrets=app.secrets, env={}, persona=home)
        try:
            # No await between: nothing had to be loaded after open_runtime returned.
            return (
                again.ledger.spent("anthropic").dollars,
                again.gate.state("anthropic"),
                again.gate.caps("anthropic"),
            )
        finally:
            await again.close()

    dollars, state, got = asyncio.run(reopen())
    assert dollars == 50.0
    assert state == "capped"
    assert got == Caps(20.0, 50.0, 80)


def test_the_pill_and_the_ledger_count_the_same_month(make: Any) -> None:
    app: App = make(env={"TZ": "America/Chicago"})
    # A timezone this machine does not know is passed over for the computer's own.
    app.rt.profile = setup.Profile(user_name="Sam", timezone="Nowhere/Atlantis")
    app.rt.clock = lambda: NOW
    expected = month_start(NOW, "America/Chicago")
    assert app.rt.zone() == "America/Chicago"
    usage = read(app)
    assert (usage["month"], usage["since_ms"]) == expected
    in_chicago(app)
    assert read(app)["since_ms"] == expected[1]


def test_a_new_timezone_reloads_the_ledger_from_the_new_start(make: Any) -> None:
    app: App = make()
    portal = app.client.portal
    assert portal is not None
    app.rt.clock = lambda: NOW
    app.rt.profile = setup.Profile(user_name="Sam", timezone="UTC")
    portal.call(app.rt.ledger.load)
    # In UTC the month began five hours before it did in Chicago; a row in between
    # counts in UTC and not in Chicago.
    utc_start = month_start(NOW, "UTC")[1]
    portal.call(_record, app.rt, utc_start + 60_000, 7.0)
    portal.call(app.rt.ledger.load)
    assert app.rt.ledger.spent("anthropic").dollars == 7.0
    portal.call(app.rt.use_profile, setup.Profile(user_name="Sam", timezone="America/Chicago"))
    assert not app.rt.ledger.stale
    assert app.rt.ledger.spent("anthropic").dollars == 0.0


def test_a_failed_load_closes_what_was_opened(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trillion.api import runtime
    from trillion.spend.gate import Gate

    opened: list[aiosqlite.Connection] = []
    real = runtime.connect

    async def connect(path: Path) -> aiosqlite.Connection:
        conn = await real(path)
        opened.append(conn)
        return conn

    async def broken(self: Gate) -> None:
        raise OSError("disk")

    monkeypatch.setattr(runtime, "connect", connect)
    monkeypatch.setattr(Gate, "reload_caps", broken)

    async def attempt() -> None:
        with pytest.raises(OSError):
            await open_runtime(secrets=MemoryStore(), env={}, persona=home)
        assert opened
        for conn in opened:
            with pytest.raises(ValueError):
                await conn.execute("SELECT 1")

    asyncio.run(attempt())
