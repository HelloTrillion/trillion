"""Month-to-date API usage, read back for the pill (BRN-4).

Read only: every model turn and compaction already writes a `usage` row. A turn
whose provider has no prices keeps its cost NULL, and is counted here as unpriced
tokens, never as $0, so the bill can never look smaller than it is.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter

from trillion.api.runtime import OWNER, Runtime


def month_start(now_ms: int, zone: str) -> tuple[str, int]:
    """The month `now_ms` falls in, and when it began: midnight on the 1st in `zone`,
    or in this machine's local time when no zone is set."""
    # A naive datetime is local time, and .timestamp() reads it back as local time.
    now = datetime.fromtimestamp(now_ms / 1000, ZoneInfo(zone) if zone else None)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return f"{start:%Y-%m}", int(start.timestamp() * 1000)


def create_usage_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/usage")

    @router.get("")
    async def read() -> dict[str, Any]:
        zone = rt.profile.timezone if rt.profile is not None else ""
        month, since = month_start(rt.clock(), zone)
        rows = await rt.conversations.usage_since(OWNER, since)
        priced = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
        return {
            "month": month,
            "since_ms": since,
            "rows": rows,
            "total_cost_usd": round(sum(priced), 6) if priced else None,
            "unpriced_tokens": sum(r["unpriced_tokens"] for r in rows),
        }

    return router
