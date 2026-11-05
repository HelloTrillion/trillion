"""Pausing a whole helper (the your helpers spec): its handoffs and its routines stop
together, by the schedules' own pause, so the runner needs nothing new."""

from __future__ import annotations

from typing import Protocol

from trillion.helpers.store import HelperStore
from trillion.scheduler.store import ScheduleStore


class Resumer(Protocol):
    """How a routine is resumed: `scheduler/runner.py`'s `resume`, as the routes use."""

    async def resume(
        self, store: ScheduleStore, owner_id: str, schedule_id: str, now_ms: int
    ) -> bool: ...


async def pause_helper(
    helpers: HelperStore, schedules: ScheduleStore, owner_id: str, helper_id: str, now_ms: int
) -> bool:
    """Pause a helper: each of its routines paused, and each handoff not yet run
    cancelled. True if this call paused the helper itself; False if it already was
    paused or is not there. Its schedules are seen to either way while it is paused,
    so a retry finishes a pause that stopped halfway."""
    paused = await helpers.pause(owner_id, helper_id, now_ms)
    helper = await helpers.get(owner_id, helper_id)
    if helper is None or helper.paused_ms is None:
        return False
    running = await schedules.running(owner_id, now_ms)
    for s in await schedules.for_helper(owner_id, helper_id):
        if s.target is not None:
            if s.paused_ms is None:
                await schedules.pause(owner_id, s.id, helper.paused_ms)
        elif s.id not in running:
            # A handoff already running finishes; one still waiting never starts.
            await schedules.cancel(owner_id, s.id, now_ms)
    return paused


async def resume_helper(
    helpers: HelperStore,
    schedules: ScheduleStore,
    runner: Resumer,
    owner_id: str,
    helper_id: str,
    now_ms: int,
) -> bool:
    """Resume a helper and the routines its pause paused, each from its next due time
    after now: what it was paused through is never run late. A routine paused on its
    own before the helper stays paused. False if the helper was not paused."""
    helper = await helpers.get(owner_id, helper_id)
    if helper is None or helper.paused_ms is None:
        return False
    for s in await schedules.for_helper(owner_id, helper_id):
        if s.target is not None and s.paused_ms is not None and s.paused_ms >= helper.paused_ms:
            await runner.resume(schedules, owner_id, s.id, now_ms)
    # Cleared last, so a resume that stops halfway is finished by trying again.
    return await helpers.resume(owner_id, helper_id)
