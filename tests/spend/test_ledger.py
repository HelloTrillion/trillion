"""The ledger: this month's spend per provider, in dollars or in units (BRN-7)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trillion.brain.types import Usage
from trillion.spend.ledger import Caller, Ledger, Row, Share, Spent, Unit
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.conversation_store import SqliteConversationStore

OWNER = "local"
ZONE = "America/Chicago"
MINUTE = 60_000


def at(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    """A moment on the wall clock in Chicago, in milliseconds."""
    return int(datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(ZONE)).timestamp() * 1000)


NOV_1 = at(2026, 11, 1)


class Clock:
    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SqliteConversationStore]:
    conn = await connect(tmp_path / "t.db")
    await migrate(conn)
    yield SqliteConversationStore(conn)
    await conn.close()


async def record(
    store: SqliteConversationStore,
    when: int,
    *,
    pid: str = "anthropic",
    cost: float | None = 1.0,
    tokens: int = 100,
    caller: Caller = "conversation",
    units: Unit = "tokens",
) -> None:
    await store.record_usage(
        owner_id=OWNER,
        conversation_id=None,
        provider=pid,
        model="m",
        usage=Usage(input_tokens=tokens),
        cost_usd=cost,
        now_ms=when,
        provider_id=pid,
        caller=caller,
        units=units,
    )


async def test_a_row_just_before_the_first_is_left_out_and_one_just_after_counts(
    store: SqliteConversationStore,
) -> None:
    await record(store, NOV_1 - MINUTE, cost=5.0)
    await record(store, NOV_1 + MINUTE, cost=0.25)
    ledger = Ledger(store, Clock(at(2026, 11, 4, 12)), ZONE)
    await ledger.load()
    assert ledger.spent("anthropic").dollars == pytest.approx(0.25)


async def test_the_month_is_the_users_not_utc(store: SqliteConversationStore) -> None:
    # 03:00 UTC on 1 November is still 31 October in Chicago.
    utc_first = int(datetime(2026, 11, 1, 3, tzinfo=ZoneInfo("UTC")).timestamp() * 1000)
    await record(store, utc_first, cost=2.0)
    ledger = Ledger(store, Clock(at(2026, 11, 4)), ZONE)
    await ledger.load()
    assert ledger.spent("anthropic").dollars == 0.0


async def test_a_null_cost_counts_units_not_zero_dollars(
    store: SqliteConversationStore,
) -> None:
    await record(store, NOV_1 + MINUTE, pid="custom:example.com", cost=None, tokens=700)
    await record(store, NOV_1 + MINUTE, pid="elevenlabs", cost=None, tokens=1200, units="chars")
    ledger = Ledger(store, Clock(at(2026, 11, 4)), ZONE)
    await ledger.load()
    custom = ledger.spent("custom:example.com")
    assert (custom.dollars, custom.units, custom.unit) == (0.0, 700, "tokens")
    voice = ledger.spent("elevenlabs")
    assert (voice.dollars, voice.units, voice.unit) == (0.0, 1200, "chars")


async def test_conversation_and_background_are_counted_apart(
    store: SqliteConversationStore,
) -> None:
    await record(store, NOV_1 + MINUTE, cost=1.0)
    await record(store, NOV_1 + MINUTE, cost=0.5, caller="background")
    await record(store, NOV_1 + MINUTE, cost=None, tokens=30, caller="background")
    ledger = Ledger(store, Clock(at(2026, 11, 4)), ZONE)
    await ledger.load()
    assert ledger.spent("anthropic") == Spent(
        dollars=pytest.approx(1.5),  # type: ignore[arg-type]
        units=30,
        unit="tokens",
        by_caller={
            "conversation": Share(dollars=1.0, units=0),
            "background": Share(dollars=0.5, units=30),
        },
    )


async def test_add_counts_a_new_call_without_reading_the_store_again(
    store: SqliteConversationStore,
) -> None:
    clock = Clock(at(2026, 11, 4))
    ledger = Ledger(store, clock, ZONE)
    await ledger.load()
    ledger.add(Row("deepgram", "conversation", "seconds", 90, 0.01, clock.now))
    ledger.add(Row("deepgram", "background", "seconds", 30, None, clock.now))
    got = ledger.spent("deepgram")
    assert (got.dollars, got.units, got.unit) == (0.01, 30, "seconds")
    assert got.by_caller["background"] == Share(dollars=0.0, units=30)
    assert ledger.spent("openai") == Spent(dollars=0.0, units=0, unit=None, by_caller={})


async def test_the_month_rolls_over(store: SqliteConversationStore) -> None:
    clock = Clock(at(2026, 10, 31, 23, 58))
    await record(store, clock.now, cost=3.0)
    ledger = Ledger(store, clock, ZONE)
    await ledger.load()
    assert ledger.spent("anthropic").dollars == 3.0
    clock.now = NOV_1 + MINUTE
    assert ledger.spent("anthropic").dollars == 0.0
    ledger.add(Row("anthropic", "conversation", "tokens", 10, 0.5, clock.now))
    assert ledger.spent("anthropic").dollars == 0.5


async def test_a_late_row_from_last_month_is_not_counted_in_this_one(
    store: SqliteConversationStore,
) -> None:
    clock = Clock(NOV_1 + MINUTE)
    ledger = Ledger(store, clock, ZONE)
    await ledger.load()
    ledger.add(Row("anthropic", "conversation", "tokens", 10, 4.0, NOV_1 - MINUTE))
    assert ledger.spent("anthropic").dollars == 0.0


async def test_the_timezone_can_be_read_each_time(store: SqliteConversationStore) -> None:
    await record(store, NOV_1 + MINUTE, cost=0.25)
    ledger = Ledger(store, Clock(at(2026, 11, 4)), lambda: ZONE)
    await ledger.load()
    assert ledger.spent("anthropic").dollars == 0.25
