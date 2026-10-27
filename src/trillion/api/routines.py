"""Settings → Reminders → Routines (AGT-3, AGT-7). Behind the session token like every
/api/ route.

Each routine with when it runs, its next run and where it lands. Pause, resume and
delete are the user's own taps from the page, like a tap on a card; none is a tool.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi import Path as Segment
from fastapi.responses import JSONResponse

from trillion.api.runtime import OWNER, Runtime
from trillion.api.schedules import words
from trillion.scheduler import runner
from trillion.scheduler.store import Schedule
from trillion.storage.sqlite.schedule_store import SqliteScheduleStore

ID = Segment(min_length=8, max_length=8, pattern=r"^[a-f0-9]+$")


def _row(s: Schedule) -> dict[str, Any]:
    upcoming, at = words(s)
    return {
        "id": s.id,
        "name": s.title,
        "when": at,
        "next_ms": s.next_due_ms,
        "next": upcoming,
        "target": s.target,
        "conversation_id": s.conversation_id,
        "paused": s.paused_ms is not None,
        "notes": s.notes,
    }


def _not_found() -> JSONResponse:
    return JSONResponse({"error": "not_found"}, status_code=404)


def create_routines_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/routines")

    def store() -> SqliteScheduleStore:
        if rt.schedules is None:
            raise RuntimeError("the scheduler is not running")
        return rt.schedules

    async def listing() -> dict[str, Any]:
        mine = await store().active(OWNER)
        # Only routines still set: a plain timed ask or reminder is the Coming up list's.
        return {
            "routines": [_row(s) for s in mine if s.target is not None and s.status == "active"]
        }

    async def routine(schedule_id: str, *, live: bool = True) -> Schedule | None:
        """The user's own routine; with `live`, only one not cancelled or finished."""
        s = await store().get(schedule_id)
        if s is None or s.owner_id != OWNER or s.target is None:
            return None
        return s if not live or s.status == "active" else None

    @router.get("")
    async def read() -> dict[str, Any]:
        return await listing()

    @router.post("/{schedule_id}/pause")
    async def pause(schedule_id: str = ID) -> Any:
        if await routine(schedule_id) is None:
            return _not_found()
        # False when it was already paused: the tap is answered with how it stands.
        await store().pause(OWNER, schedule_id, rt.clock())
        return await listing()

    @router.post("/{schedule_id}/resume")
    async def resume(schedule_id: str = ID) -> Any:
        if await routine(schedule_id) is None:
            return _not_found()
        # From the next due time after now: what it was paused through never runs late.
        await runner.resume(store(), OWNER, schedule_id, rt.clock())
        return await listing()

    @router.delete("/{schedule_id}")
    async def delete(schedule_id: str = ID) -> Any:
        if await routine(schedule_id, live=False) is None:
            return _not_found()
        # Already cancelled is fine: a second tap, or a page that had not caught up.
        await store().cancel(OWNER, schedule_id, rt.clock())
        return await listing()

    return router
