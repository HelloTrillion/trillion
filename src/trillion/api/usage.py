"""Month-to-date API usage, read back for the pill (BRN-4).

Read only: every model turn and compaction already writes a `usage` row. A turn
whose provider has no prices keeps its cost NULL, and is counted here as unpriced
tokens, never as $0, so the bill can never look smaller than it is.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from trillion.api.runtime import OWNER, Runtime
from trillion.zones import month_start


def create_usage_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/usage")

    @router.get("")
    async def read() -> dict[str, Any]:
        # The same zone the spend ledger counts in, so the pill and the caps agree.
        month, since = month_start(rt.clock(), rt.zone() or "")
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
