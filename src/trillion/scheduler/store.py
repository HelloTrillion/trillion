"""What the scheduler keeps (AGT-4, SUR-9): schedules, their runs, and notices.

The scheduler and the door depend on this, never on SQLite. The SQLite store is
`storage/sqlite/schedule_store.py`: only storage code talks to the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from trillion.scheduler.when import Repeat

Kind = Literal["reminder", "ask"]
# Where a routine puts its board: today's day conversation, or a conversation of its own.
Target = Literal["day", "own"]
ScheduleStatus = Literal["active", "done", "cancelled"]
RunStatus = Literal[
    "queued",
    "running",
    "delivered",
    "late",
    "missed",
    "blocked",
    "failed",
    "interrupted",
    "cancelled",
    # A routine not run: paused, or its day already built by hand.
    "skipped",
    # A routine that ran and found nothing worth the user's attention (AGT-6).
    "nothing",
]
NoticeKind = Literal["reminder", "ask", "missed", "failed", "ready"]
NoticeStatus = Literal["held", "delivered"]
# A notice that arrives more than this after its due time says so ("due 4:00 pm").
LATE_MS = 120_000
# A routine's notes, at most: what to watch and what to leave out, not a document.
NOTES_MAX = 1000
# Failed runs in a row after which a routine pauses itself (AGT-8), and in words.
PAUSE_AFTER = 3
PAUSE_AFTER_WORDS = "three"


@dataclass(frozen=True, kw_only=True)
class Schedule:
    id: str
    owner_id: str
    kind: Kind
    title: str
    body: str
    zone: str
    at_local: str
    repeat: Repeat | None
    next_due_ms: int | None
    status: ScheduleStatus
    created_ms: int
    created_in: str
    # A routine: a timed ask with a target. None for a plain timed ask or a reminder.
    target: Target | None = None
    # An 'own' routine's conversation, made on its first run.
    conversation_id: str | None = None
    notes: str = ""
    # Paused when set: not run, and its due time not moved on.
    paused_ms: int | None = None
    # Failed runs in a row.
    failures: int = 0


@dataclass(frozen=True, kw_only=True)
class Run:
    id: int
    schedule_id: str
    due_ms: int
    status: RunStatus
    claimed_by: str | None
    claimed_ms: int | None
    started_ms: int | None
    finished_ms: int | None
    timeout_s: float
    attempts: int
    result_notice: str | None
    detail: str | None


@dataclass(frozen=True, kw_only=True)
class Result:
    """A finished run, with the title and kind of the schedule it belongs to."""

    run: Run
    title: str
    kind: Kind
    # The schedule's zone, so its times are told as the user set them.
    zone: str = ""


@dataclass(frozen=True, kw_only=True)
class Notice:
    id: str
    owner_id: str
    source_id: str
    kind: NoticeKind
    title: str
    # Her words. None for a missed or failed notice, which is never spoken (PER-11).
    text: str | None
    due_ms: int
    late: bool
    created_ms: int
    status: NoticeStatus
    delivered_ms: int | None
    delivered_to: str | None
    notified_ms: int | None
    # The conversation a routine's notice opens; None for any other.
    conversation_id: str | None = None


class ScheduleStore(Protocol):
    async def create(self, s: Schedule) -> None: ...

    async def get(self, schedule_id: str) -> Schedule | None: ...

    async def active(self, owner_id: str) -> list[Schedule]:
        """What is coming up: active schedules, and finished ones with a run still to go."""
        ...

    async def due(self, now_ms: int) -> list[Schedule]: ...

    async def enqueue(
        self, schedule_id: str, due_ms: int, next_due_ms: int | None, timeout_s: float, now_ms: int
    ) -> bool:
        """Record one run at `due_ms` and move the schedule on, together. False if that
        occurrence was already recorded."""
        ...

    async def cancel(self, owner_id: str, schedule_id: str, now_ms: int) -> bool: ...

    async def pause(self, owner_id: str, schedule_id: str, now_ms: int) -> bool:
        """Pause an active routine. Its due time stays where it was."""
        ...

    async def resume(self, owner_id: str, schedule_id: str, next_due_ms: int | None) -> bool:
        """Resume a paused routine from `next_due_ms`, with its failures forgotten."""
        ...

    async def set_conversation(self, schedule_id: str, conversation_id: str) -> None:
        """Where an 'own' routine's runs go from now on."""
        ...

    async def add_note(self, owner_id: str, schedule_id: str, line: str) -> bool:
        """Add a line to a routine's notes (AGT-7), only while they stay within
        `NOTES_MAX`. False, and nothing changed, otherwise or for no such routine."""
        ...

    async def succeeded(self, schedule_id: str) -> None:
        """A run that finished: the failures in a row start again from none."""
        ...

    async def claim(
        self, worker: str, now_ms: int, retry_before_ms: int, kind: Kind | None = None
    ) -> Run | None:
        """Take the oldest due run, atomically: a queued one, or a blocked one last tried
        at or before `retry_before_ms`. Only a run of a schedule of `kind`, when given."""
        ...

    async def finish(
        self,
        run_id: int,
        status: RunStatus,
        now_ms: int,
        *,
        detail: str | None = None,
    ) -> bool: ...

    async def block(self, run_id: int, now_ms: int, detail: str) -> None: ...

    async def reclaim(self, worker: str, now_ms: int) -> list[Run]:
        """Runs left running by another process, or past their timeout, become
        interrupted (AGT-4). Returns them."""
        ...

    async def requeue(self, run_id: int, detail: str) -> None: ...

    async def recent(self, owner_id: str, limit: int) -> list[Result]: ...

    async def last_results(self, owner_id: str) -> dict[str, Run]:
        """The latest finished run of each schedule."""
        ...

    async def finish_with_notice(
        self,
        run_id: int,
        status: RunStatus,
        now_ms: int,
        *,
        detail: str | None = None,
        owner_id: str,
        source_id: str,
        kind: NoticeKind,
        title: str,
        text: str | None,
        due_ms: int,
        late: bool,
        conversation_id: str | None = None,
        pauses_as: str | None = None,
    ) -> Notice | None:
        """Finish a running (or interrupted) run and write its notice, together, so a
        crash between the two can never leave a run to be tried again with its notice
        already written. None, and nothing written, if the run was not running. None
        too if its schedule was cancelled meanwhile: the run ends cancelled, untold.

        `pauses_as`, for a routine's failure: it is counted in the same change, and
        the notice that pauses the routine (`PAUSE_AFTER`) takes this title."""
        ...

    async def ready_notice(
        self, owner_id: str, source_id: str, title: str, now_ms: int, conversation_id: str
    ) -> Notice:
        """A ready line with no run behind it (a background read is in), held."""
        ...

    async def notice(self, notice_id: str) -> Notice | None: ...

    async def held(self, owner_id: str) -> list[Notice]:
        """Notices not delivered yet, oldest first."""
        ...

    async def mark_delivered(self, notice_id: str, to: str, now_ms: int) -> bool:
        """Only a held notice can be delivered, so each is delivered once."""
        ...

    async def unmark_delivered(self, notice_id: str, to: str) -> bool:
        """Hold a notice again that `to` could not take. Only that page's delivery."""
        ...

    async def mark_notified(self, notice_id: str, now_ms: int) -> None: ...
