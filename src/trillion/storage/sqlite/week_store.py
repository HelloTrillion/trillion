"""Roast my week, read from the database: the last seven days, counted (see `roast/week.py`).

Only counts, times of day, conversation titles and her own records are read: never the
text of a message, and nothing from a connected app. Read only: nothing here writes.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from trillion.roast.week import WEEK_MS, Week, zone_of

# Up to here after midnight is still "last night".
LATE_HOUR = 5
TITLES = 8
_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _clock(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    return f"{_DAYS[dt.weekday()]} at {hour}:{dt.minute:02d} {'am' if dt.hour < 12 else 'pm'}"


async def _rows(
    conn: aiosqlite.Connection, sql: str, args: tuple[object, ...]
) -> list[tuple[Any, ...]]:
    async with conn.execute(sql, args) as cur:
        return [tuple(r) for r in await cur.fetchall()]


async def read_week(conn: aiosqlite.Connection, owner: str, now_ms: int, zone: str) -> Week:
    """The last seven days up to `now_ms`, with times of day in `zone` (UTC if unknown).
    Read only: nothing here writes."""
    since = now_ms - WEEK_MS
    tz = zone_of(zone) or UTC

    sent = await _rows(
        conn,
        "SELECT m.created_at, m.conversation_id FROM messages m "
        "JOIN conversations c ON c.id = m.conversation_id "
        "WHERE m.owner_id = ? AND m.kind = 'user' AND m.created_at >= ? AND m.created_at <= ? "
        "AND c.deleted_at IS NULL",
        (owner, since, now_ms),
    )
    days: Counter[str] = Counter()
    latest: datetime | None = None
    nights: set[str] = set()
    for at, _ in sent:
        local = datetime.fromtimestamp(at / 1000, tz)
        days[_DAYS[local.weekday()]] += 1
        if local.hour < LATE_HOUR:
            nights.add(local.date().isoformat())
            # Latest into the night, whichever night it was.
            if latest is None or (local.hour, local.minute) > (latest.hour, latest.minute):
                latest = local
    busiest = days.most_common(1)[0] if days else None

    via = dict(
        await _rows(
            conn,
            "SELECT via, COUNT(*) FROM turn_timings "
            "WHERE owner_id = ? AND created_at >= ? AND created_at <= ? GROUP BY via",
            (owner, since, now_ms),
        )
    )

    titled = await _rows(
        conn,
        "SELECT c.title, MAX(m.created_at) AS last FROM messages m "
        "JOIN conversations c ON c.id = m.conversation_id "
        "WHERE m.owner_id = ? AND m.kind = 'user' AND m.created_at >= ? AND m.created_at <= ? "
        "AND c.deleted_at IS NULL AND c.kind = 'chat' AND c.title IS NOT NULL AND c.title != '' "
        "GROUP BY c.id ORDER BY last DESC LIMIT ?",
        (owner, since, now_ms, TITLES),
    )

    (interrupted,) = (
        await _rows(
            conn,
            "SELECT COUNT(*) FROM messages WHERE owner_id = ? AND kind = 'assistant' "
            "AND created_at >= ? AND created_at <= ? AND blocks_json LIKE '%[interrupted]%'",
            (owner, since, now_ms),
        )
    )[0]

    decided = dict(
        await _rows(
            conn,
            "SELECT status, COUNT(*) FROM pending_actions WHERE owner_id = ? "
            "AND created_at >= ? AND created_at <= ? GROUP BY status",
            (owner, since, now_ms),
        )
    )

    reminders = dict(
        await _rows(
            conn,
            "SELECT status, COUNT(*) FROM schedules WHERE owner_id = ? "
            "AND created_ms >= ? AND created_ms <= ? GROUP BY status",
            (owner, since, now_ms),
        )
    )

    helpers = await _rows(
        conn,
        "SELECT name, deleted_at IS NOT NULL FROM helpers "
        "WHERE owner_id = ? AND created_at >= ? AND created_at <= ? "
        "ORDER BY created_at",
        (owner, since, now_ms),
    )

    (memories,) = (
        await _rows(
            conn,
            "SELECT COUNT(*) FROM memories WHERE owner_id = ? AND created_at >= ? "
            "AND created_at <= ? AND status = 'active'",
            (owner, since, now_ms),
        )
    )[0]

    cost, known, tokens = (
        await _rows(
            conn,
            "SELECT SUM(cost_usd), COUNT(cost_usd), SUM(input_tokens + output_tokens) FROM usage "
            "WHERE owner_id = ? AND created_at >= ? AND created_at <= ?",
            (owner, since, now_ms),
        )
    )[0]

    return Week(
        messages=len(sent),
        spoken=int(via.get("voice", 0)),
        typed=int(via.get("text", 0)),
        conversations=len({c for _, c in sent}),
        titles=tuple(str(t) for t, _ in titled),
        busiest_day=busiest[0] if busiest else None,
        busiest_count=busiest[1] if busiest else 0,
        latest_night=_clock(latest) if latest is not None else None,
        late_nights=len(nights),
        interrupted=int(interrupted or 0),
        declined=int(decided.get("rejected", 0)),
        approved=int(decided.get("succeeded", 0)),
        reminders_set=sum(int(n) for n in reminders.values()),
        reminders_cancelled=int(reminders.get("cancelled", 0)),
        helpers_made=tuple(str(n) + (" (since deleted)" if gone else "") for n, gone in helpers),
        memories=int(memories or 0),
        # Unknown when no turn had a known price: never shown as free.
        spent_usd=round(float(cost), 2) if known else None,
        tokens=int(tokens or 0),
    )
