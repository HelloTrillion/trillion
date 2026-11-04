"""Month-to-date API usage, read back for the pill (BRN-4).

Read only: every paid call (each model call, a sentence spoken, a stream heard)
already writes one `usage` row, through the metered providers (BRN-7). A call whose
provider has no prices keeps its cost NULL, and is counted here as unpriced units,
never as $0, so the bill can never look smaller than it is. `unpriced_turns` keeps
its old name for the API, and counts those calls.
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
