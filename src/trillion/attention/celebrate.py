"""When a notice deserves a celebration, and how big (GAL-9, the supernova spec).

The attention engine is the only thing that decides this. The door asks, as it asks
the quiet check, about each notice it is about to hand over; nothing else can ask for
one. There is no tool for it and no argument that carries it: a plugin, a tool or a
model's words can't make the orb burst.

The triggers are few and come from the scheduler's own records, never from what a
model said:

- large: a routine's 30th, 100th or 365th run that brought something to the user;
- medium: the first such run of a helper the user made;
- small: a handoff or timed ask that worked for 15 minutes or more and finished.

The biggest that applies wins. A notice with no run behind it (a background read), a
failed, missed or empty run, or a notice older than half an hour by the time it is
shown, never celebrates.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Literal, Protocol

from trillion.scheduler.store import Notice, Run, RunStatus, Schedule

logger = logging.getLogger(__name__)

Tier = Literal["small", "medium", "large"]
TIERS: tuple[Tier, ...] = ("small", "medium", "large")

# A routine's runs that brought something, at which it celebrates.
MILESTONES = frozenset({30, 100, 365})
# A handoff or timed ask that worked at least this long.
LONG_RUN_MS = 15 * 60_000
# A celebration is for now: a notice shown later than this after it was written is
# only shown.
FRESH_MS = 30 * 60_000
# A run that brought the user something. A run with nothing to say (AGT-6) is not news.
_BROUGHT: frozenset[RunStatus] = frozenset({"delivered", "late"})
# Only these carry a finished run's result; reminders, missed and failed ones never do.
_KINDS = frozenset({"ready", "ask"})


class Records(Protocol):
    """What the engine reads of the scheduler: nothing it can change."""

    async def get(self, schedule_id: str) -> Schedule | None: ...

    async def run_of_notice(self, owner_id: str, notice_id: str) -> Run | None: ...

    async def brought(
        self,
        owner_id: str,
        up_to_run: int,
        *,
        schedule_id: str | None = None,
        helper_id: str | None = None,
    ) -> int:
        """Runs up to and including `up_to_run` that brought something: one schedule's,
        or all of a helper's."""
        ...


def biggest(tiers: list[Tier]) -> Tier | None:
    return max(tiers, key=TIERS.index, default=None)


class Celebrations:
    """The door's `Celebrate`: which tier, if any, for one notice."""

    def __init__(
        self, records: Records, *, owner: str, on: Callable[[], bool] = lambda: True
    ) -> None:
        self._records = records
        self._owner = owner
        self._on = on

    async def tier(self, notice: Notice, now_ms: int) -> Tier | None:
        if not self._on():
            return None
        if notice.source != "schedule" or notice.kind not in _KINDS:
            return None
        if now_ms - notice.created_ms > FRESH_MS:
            return None
        s = await self._records.get(notice.source_id)
        if s is None or s.owner_id != self._owner:
            return None
        run = await self._records.run_of_notice(self._owner, notice.id)
        if run is None or run.schedule_id != s.id:
            # No run behind it: nothing finished.
            return None
        if run.status not in _BROUGHT:
            return None
        found: list[Tier] = []
        if s.target is not None:
            count = await self._records.brought(self._owner, run.id, schedule_id=s.id)
            if count in MILESTONES:
                found.append("large")
        if s.helper_id is not None:
            # Its first: no run of any of its schedules brought anything before this one.
            if await self._records.brought(self._owner, run.id, helper_id=s.helper_id) == 1:
                found.append("medium")
        if s.target is None and _took(run) >= LONG_RUN_MS:
            found.append("small")
        tier = biggest(found)
        if tier is not None:
            logger.info("celebration: %s for notice %s", tier, notice.id)
        return tier


def _took(run: Run) -> int:
    if run.started_ms is None or run.finished_ms is None:
        return 0
    return run.finished_ms - run.started_ms
