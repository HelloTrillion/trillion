"""Usage rows say which provider was paid and who spent it (BRN-7)."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from importlib.resources import files
from pathlib import Path

import pytest

from trillion.brain.types import Usage
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.conversation_store import SqliteConversationStore

OWNER = "local"


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SqliteConversationStore]:
    conn = await connect(tmp_path / "t.db")
    await migrate(conn)
    yield SqliteConversationStore(conn)
    await conn.close()


async def _columns(store: SqliteConversationStore) -> list[tuple[str, str, str, str]]:
    async with store._conn.execute(
        "SELECT provider, provider_id, caller, units FROM usage ORDER BY id"
    ) as cur:
        return [tuple(r) for r in await cur.fetchall()]


async def test_the_migration_upgrades_a_database_holding_old_rows(tmp_path: Path) -> None:
    before = tmp_path / "before"
    before.mkdir()
    for entry in (files("trillion.storage") / "migrations").iterdir():
        if entry.name.endswith(".sql") and entry.name < "017":
            (before / entry.name).write_text(entry.read_text(encoding="utf-8"), encoding="utf-8")
    conn = await connect(tmp_path / "old.db")
    await migrate(conn, before)
    for provider in ("anthropic", "openai-compatible"):
        await conn.execute(
            "INSERT INTO usage (owner_id, conversation_id, provider, model, input_tokens,"
            " output_tokens, cache_read_tokens, cache_write_tokens, cost_usd, created_at)"
            " VALUES ('local', NULL, ?, 'm', 10, 2, 0, 0, NULL, 5)",
            (provider,),
        )
    assert await migrate(conn) == ["017_usage_spend", "018_attention_deferred"]
    store = SqliteConversationStore(conn)
    assert await _columns(store) == [
        ("anthropic", "anthropic", "conversation", "tokens"),
        # Which server an old OpenAI-compatible row went to was never written down.
        ("openai-compatible", "openai-compatible", "conversation", "tokens"),
    ]
    async with conn.execute("PRAGMA index_list(usage)") as cur:
        assert "usage_by_owner_time" in [r[1] for r in await cur.fetchall()]
    await conn.close()


async def test_a_row_records_its_provider_caller_and_units(
    store: SqliteConversationStore,
) -> None:
    await store.record_usage(
        owner_id=OWNER,
        conversation_id=None,
        provider="anthropic",
        model="m",
        usage=Usage(input_tokens=1),
        cost_usd=0.1,
        now_ms=1,
    )
    await store.record_usage(
        owner_id=OWNER,
        conversation_id=None,
        provider="elevenlabs",
        model="v",
        usage=Usage(input_tokens=120),
        cost_usd=None,
        now_ms=2,
        provider_id="elevenlabs",
        caller="background",
        units="chars",
    )
    assert await _columns(store) == [
        ("anthropic", "anthropic", "conversation", "tokens"),
        ("elevenlabs", "elevenlabs", "background", "chars"),
    ]


async def test_the_database_refuses_an_unknown_caller_or_unit(
    store: SqliteConversationStore,
) -> None:
    for column, value in (("caller", "someone"), ("units", "bytes")):
        with pytest.raises(sqlite3.IntegrityError):
            await store._conn.execute(
                "INSERT INTO usage (owner_id, provider, model, input_tokens, output_tokens,"  # noqa: S608 - test text only
                f" cache_read_tokens, cache_write_tokens, created_at, {column})"
                " VALUES ('local', 'p', 'm', 0, 0, 0, 0, 1, ?)",
                (value,),
            )


async def test_spend_since_sums_each_provider_and_caller_apart(
    store: SqliteConversationStore,
) -> None:
    async def row(
        pid: str, caller: str, cost: float | None, at: int, tokens: int = 100, units: str = "tokens"
    ) -> None:
        await store.record_usage(
            owner_id=OWNER,
            conversation_id=None,
            provider=pid,
            model="m",
            usage=Usage(input_tokens=tokens, output_tokens=0),
            cost_usd=cost,
            now_ms=at,
            provider_id=pid,
            caller=caller,  # type: ignore[arg-type]
            units=units,  # type: ignore[arg-type]
        )

    await row("anthropic", "conversation", 0.25, 10)
    await row("anthropic", "conversation", 0.5, 11)
    await row("anthropic", "background", None, 12, tokens=40)
    await row("anthropic", "conversation", 9.0, 1)  # before the moment
    got = await store.spend_since(OWNER, 10)
    assert sorted(got, key=lambda r: r["caller"]) == [
        {
            "provider_id": "anthropic",
            "caller": "background",
            "units": "tokens",
            "cost_usd": None,
            "unpriced": 40,
            "quantity": 40,
        },
        {
            "provider_id": "anthropic",
            "caller": "conversation",
            "units": "tokens",
            "cost_usd": 0.75,
            "unpriced": 0,
            "quantity": 200,
        },
    ]


async def test_reading_the_month_seeks_by_owner_and_time(store: SqliteConversationStore) -> None:
    async with store._conn.execute(
        "EXPLAIN QUERY PLAN SELECT provider_id FROM usage"
        " WHERE owner_id = 'local' AND created_at >= 1 AND created_at <= 2"
    ) as cur:
        plan = " ".join(str(r[-1]) for r in await cur.fetchall())
    assert "USING INDEX usage_by_owner_time (owner_id=? AND created_at>? AND created_at<?)" in plan


async def test_spend_since_can_stop_at_a_moment(store: SqliteConversationStore) -> None:
    for when in (10, 20):
        await store.record_usage(
            owner_id=OWNER,
            conversation_id=None,
            provider="anthropic",
            model="m",
            usage=Usage(input_tokens=1),
            cost_usd=1.0,
            now_ms=when,
        )
    (row,) = await store.spend_since(OWNER, 0, 19)
    assert row["cost_usd"] == 1.0


async def test_the_pill_never_counts_characters_or_seconds_as_tokens(
    store: SqliteConversationStore,
) -> None:
    await store.record_usage(
        owner_id=OWNER,
        conversation_id=None,
        provider="anthropic",
        model="m",
        usage=Usage(input_tokens=50),
        cost_usd=None,
        now_ms=1,
    )
    await store.record_usage(
        owner_id=OWNER,
        conversation_id=None,
        provider="elevenlabs",
        model="v",
        usage=Usage(input_tokens=1200),
        cost_usd=None,
        now_ms=1,
        provider_id="elevenlabs",
        units="chars",
    )
    rows = {r["provider_id"]: r for r in await store.usage_since(OWNER, 0)}
    assert (rows["anthropic"]["unit"], rows["anthropic"]["unpriced_tokens"]) == ("tokens", 50)
    voice = rows["elevenlabs"]
    assert (voice["unit"], voice["unpriced_tokens"], voice["unpriced"]) == ("chars", 0, 1200)


async def test_spend_since_also_sums_every_unit_priced_or_not(
    store: SqliteConversationStore,
) -> None:
    for cost in (0.5, None):
        await store.record_usage(
            owner_id=OWNER,
            conversation_id=None,
            provider="openrouter",
            model="m",
            usage=Usage(input_tokens=10, output_tokens=5),
            cost_usd=cost,
            now_ms=20,
            provider_id="custom:openrouter.ai",
        )
    (row,) = await store.spend_since(OWNER, 0)
    assert (row["cost_usd"], row["unpriced"], row["quantity"]) == (0.5, 15, 30)
