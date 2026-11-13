"""The Seamless measures, counted on this computer only (docs/specs/2026-11-12-seamless.md).

What is recorded, and from where:

- the first run: when onboarding began (the first open with no persona yet), each
  onboarding step that asked the user something (the page reports it), and when her
  first reply reached the page;
- each turn with a tool over two seconds: whether her first words were heard before
  two seconds into it;
- asks, and asks approved within 30 seconds with nothing changed, read from the
  decision record (SPN-17), which already logs every card and its answer.

Numbers and times only, and nothing leaves the machine (ONB-6). Recording never fails a
turn, and a turn never waits on it: each write goes on beside the turn, owned by the
runtime (`api/writes.py`), and a failure is logged at debug, by its class name only.

`GET /api/seamless` reads them, behind the session token like every /api/ route
(SPN-11), for the one line in Settings → Confirmations. `POST /api/seamless/asked` is
the page saying an onboarding step asked something; it carries nothing.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

from trillion.api.runtime import OWNER, Runtime
from trillion.api.trace import Trace
from trillion.api.writes import SETTLE_S

logger = logging.getLogger(__name__)


async def settle(rt: Runtime, timeout_s: float = SETTLE_S) -> bool:
    """Wait, for a bounded time, for this runtime's writes on their way (tests, and
    anything that reads straight after). True if they all finished."""
    return await rt.measure_writes.settle(timeout_s)


def opened(rt: Runtime) -> None:
    """A page opened while onboarding is due: the first run begins, once."""
    store = rt.measures
    if store is None:
        return
    rt.measure_writes.start(
        "first run", store.opened(OWNER, at=rt.clock(), model_ready=rt.provider is not None)
    )


def replied(rt: Runtime) -> None:
    """A reply of hers reached the page."""
    store = rt.measures
    if store is None:
        return
    rt.measure_writes.start("first reply", store.replied(OWNER, at=rt.clock()))


def tool_turn(rt: Runtime, trace: Trace, *, voiced: bool) -> None:
    """A turn is over: if a tool in it ran over two seconds, whether she was heard in time."""
    store = rt.measures
    in_time = trace.spoke_in_time()
    if store is None or in_time is None:
        return
    rt.measure_writes.start(
        "tool turn",
        store.tool_turn(OWNER, at=rt.clock(), voiced=voiced, spoke_in_time=in_time),
    )


def create_seamless_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    @router.get("/api/seamless")
    async def read() -> dict[str, Any]:
        now = rt.clock()
        store = rt.measures
        if store is None:
            return {"now_ms": now, "first_run": None, "asks": [], "tool_turns": None}
        return {"now_ms": now} | (await store.read(OWNER, now)).to_json()

    @router.post("/api/seamless/asked", status_code=204)
    async def asked() -> None:
        # Counted only while the first run waits for her first reply.
        store = rt.measures
        if store is None:
            return
        try:
            await store.asked(OWNER)
        except Exception as e:
            logger.debug("onboarding question not recorded: %s", type(e).__name__)

    return router


# Mounted by `trillion.api.routes.plugged`: nothing to add to app.py.
ROUTES = (create_seamless_router,)
