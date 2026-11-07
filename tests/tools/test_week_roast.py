"""Roast my week: only what happened in the last seven days, read as it was recorded."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import aiosqlite
import pytest

from trillion.roast.week import WEEK_MS, Week, facts, window
from trillion.spine.contract import ToolContext
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.week_store import read_week
from trillion.tools.roast import WeekRoast

OWNER = "local"
ZONE = "America/New_York"
TZ = ZoneInfo(ZONE)
# Thursday 6 November 2026, 6 pm in New York.
NOW = int(datetime(2026, 11, 6, 18, 0, tzinfo=TZ).timestamp() * 1000)


def at(day: int, hour: int, minute: int = 0) -> int:
    return int(datetime(2026, 11, day, hour, minute, tzinfo=TZ).timestamp() * 1000)


@pytest.fixture
async def conn(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    c = await connect(tmp_path / "t.db")
    await migrate(c)
    yield c
    await c.close()


async def chat(
    c: aiosqlite.Connection, cid: str, title: str | None = None, deleted: bool = False
) -> None:
    await c.execute(
        "INSERT INTO conversations (id, owner_id, created_at, kind, title, deleted_at) "
        "VALUES (?, ?, 0, 'chat', ?, ?)",
        (cid, OWNER, title, 1 if deleted else None),
    )


async def said(
    c: aiosqlite.Connection, cid: str, when: int, kind: str = "user", text: str = "hi"
) -> None:
    role = "user" if kind in ("user", "tap") else "assistant"
    await c.execute(
        "INSERT INTO messages (owner_id, conversation_id, role, kind, blocks_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (OWNER, cid, role, kind, f'[{{"type": "text", "text": "{text}"}}]', when),
    )


async def test_an_empty_week_has_nothing_to_roast(conn: aiosqlite.Connection) -> None:
    week = await read_week(conn, OWNER, NOW, ZONE)
    assert week.empty and week.messages == 0 and week.spent_usd is None


async def test_the_week_is_counted_as_it_was_recorded(conn: aiosqlite.Connection) -> None:
    await chat(conn, "a", "Garden soil")
    await chat(conn, "b", "Tax receipts")
    await chat(conn, "gone", "Deleted one", deleted=True)
    await said(conn, "a", at(3, 10))  # Tuesday morning
    await said(conn, "a", at(4, 2, 14))  # Wednesday, 2:14 am
    await said(conn, "b", at(4, 1, 5))  # Wednesday, 1:05 am: earlier in the night
    await said(conn, "b", at(4, 15))
    await said(conn, "gone", at(4, 16))  # a deleted conversation is not counted
    await said(conn, "a", NOW - WEEK_MS - 1)  # just before the week: not counted
    await said(conn, "a", at(5, 9), kind="assistant", text="Well [interrupted]")
    await conn.execute(
        "INSERT INTO usage (owner_id, conversation_id, provider, model, input_tokens, "
        "output_tokens, cache_read_tokens, cache_write_tokens, cost_usd, created_at) "
        "VALUES (?, 'a', 'p', 'm', 100, 20, 0, 0, 1.234, ?)",
        (OWNER, at(4, 15)),
    )
    await conn.execute(
        "INSERT INTO conversations (id, owner_id, created_at, kind, day) "
        "VALUES ('d', ?, 0, 'day', '2026-11-04')",
        (OWNER,),
    )
    await conn.execute("UPDATE conversations SET title = 'Your day' WHERE id = 'd'")
    await said(conn, "d", at(4, 8))  # her day's title is the app's, not a topic
    for name, deleted in (("Files", 1), ("Social", None)):
        await conn.execute(
            "INSERT INTO helpers (id, owner_id, name, name_key, job, conversation_id, "
            "created_at, deleted_at) VALUES (?, ?, ?, ?, 'a job', 'a', ?, ?)",
            (name, OWNER, name, name.lower(), at(4, 12), deleted),
        )
    await conn.commit()

    week = await read_week(conn, OWNER, NOW, ZONE)

    assert week.messages == 5 and week.conversations == 3
    assert week.busiest_day == "Wednesday" and week.busiest_count == 4
    assert week.latest_night == "Wednesday at 2:14 am" and week.late_nights == 1
    assert week.titles == ("Tax receipts", "Garden soil")
    assert week.interrupted == 1
    assert week.spent_usd == 1.23 and week.tokens == 120

    lines = facts(week)
    assert week.helpers_made == ("Files (since deleted)", "Social")
    assert lines[0] == "5 messages to you across 3 conversations."
    assert any("the latest was Wednesday at 2:14 am" in line for line in lines)
    assert any("cut you off mid-sentence 1 time" in line for line in lines)
    assert any(line == "Spent on you this week: $1.23." for line in lines)
    # Nothing is listed that didn't happen.
    assert not any("reminder" in line or "yes" in line for line in lines)


def test_an_unknown_price_is_never_shown_as_free() -> None:
    assert not any("Spent" in line for line in facts(Week(messages=1, conversations=1)))


def test_the_window_is_the_last_seven_days_where_the_user_is() -> None:
    assert window(NOW, ZONE) == ("Oct 31", "Nov 6")


def context(owner: str) -> ToolContext:
    return ToolContext(owner_id=owner, session_id="s", turn=1, call_id="c", via="text")


async def test_the_tool_reads_the_week_for_its_owner_only() -> None:
    tool = WeekRoast()
    ctx = context(OWNER)
    unbound = await tool.execute({}, ctx)
    assert not unbound.ok

    async def read() -> Week:
        return Week(messages=3, conversations=1, interrupted=2)

    tool.bind(read, OWNER)
    result = await tool.execute({}, ctx)
    assert result.ok and result.effect == "none"
    assert "3 messages to you" in result.content and "2 times" in result.content
    other = await tool.execute({}, context("someone"))
    assert not other.ok

    async def nothing() -> Week:
        return Week()

    tool.bind(nothing, OWNER)
    assert "No messages" in (await tool.execute({}, ctx)).content
