"""Settings → Reminders (AGT-4, AGT-9). Behind the session token like every /api/ route.

What is coming up, what happened recently, and when the scheduler last checked, with
the server's own time beside it, so the page can tell a stopped scheduler from one
with nothing to do (TESTING §4.5). Cancelling here is the user's own act from the
page, like a tap on a card; it is not a tool.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi import Path as Segment
from fastapi.responses import JSONResponse

from trillion.api.runtime import OWNER, Runtime
from trillion.scheduler import when
from trillion.scheduler.runner import TICK_S
from trillion.scheduler.store import Result, Schedule
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore

RECENT = 20


def words(s: Schedule) -> tuple[str, str]:
    """(next, when) in the user's own zone. Empty for a zone this computer doesn't know."""
    try:
        upcoming = when.spoken(s.next_due_ms, s.zone) if s.next_due_ms is not None else ""
        return upcoming, when.summary(s.at_local, s.repeat, s.zone)
    except when.WhenError:
        return "", ""


def _due(r: Result) -> str:
    try:
        return when.spoken(r.run.due_ms, r.zone)
    except when.WhenError:
        return ""


def create_schedules_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    def store() -> SqliteScheduleStore:
        if rt.schedules is None:
            raise RuntimeError("the scheduler is not running")
        return rt.schedules

    async def state() -> dict[str, Any]:
        s = store()
        last = await s.last_results(OWNER)
        active = []
        for x in await s.active(OWNER):
            if x.target is not None:
                # A routine is listed with the routines (`api/routines.py`), not twice.
                continue
            upcoming, at = words(x)
            active.append(
                {
                    "id": x.id,
                    "kind": x.kind,
                    "title": x.title,
                    "next_ms": x.next_due_ms,
                    "next": upcoming,
                    "when": at,
                    "repeat": when.repeat_words(x.repeat),
                    "last": last[x.id].status if x.id in last else None,
                }
            )
        recent = [
            {
                "id": r.run.id,
                "schedule_id": r.run.schedule_id,
                "kind": r.kind,
                "title": r.title,
                "status": r.run.status,
                "due_ms": r.run.due_ms,
                # In the schedule's own zone, as "next" is: the browser's may differ.
                "due": _due(r),
                "finished_ms": r.run.finished_ms,
                "detail": r.run.detail or "",
            }
            for r in await s.recent(OWNER, RECENT)
        ]
        sched = rt.scheduler
        return {
            "active": active,
            "recent": recent,
            "checked_ms": sched.checked_ms if sched is not None else None,
            "tick_s": sched.tick_s if sched is not None else TICK_S,
            "now_ms": rt.clock(),
        }

    @router.get("/api/schedules")
    async def read() -> dict[str, Any]:
        return await state()

    @router.delete("/api/schedules/{schedule_id}")
    async def cancel(
        schedule_id: str = Segment(min_length=8, max_length=8, pattern=r"^[a-f0-9]+$"),
    ) -> Any:
        if not await store().cancel(OWNER, schedule_id, rt.clock()):
            return JSONResponse({"error": "not_found"}, status_code=404)
        return await state()

    return router
