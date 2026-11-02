"""What the scheduler keeps (AGT-4, SUR-9): schedules, their runs, and notices.

The scheduler and the door depend on this, never on SQLite. The SQLite store is
`storage/sqlite/schedule_store.py`: only storage code talks to the database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import urlsplit

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
# heads_up: a watcher's one line about a meeting, an email or a payment.
NoticeKind = Literal["reminder", "ask", "missed", "failed", "ready", "heads_up"]
# Where a notice came from: a schedule's run, or a watcher (`source_id` is its find's key).
NoticeSource = Literal["schedule", "attention"]
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
    source: NoticeSource = "schedule"
    # Where its button opens: a meeting's join link, https only.
    link: str | None = None
    # After this it is shown, not spoken: a heads-up after the meeting began is noise.
    fresh_until_ms: int | None = None
    # What a watcher's find was, in the host's words, for the page only: never spoken,
    # never on the system notification, never saved as her message.
    detail: str | None = None


def https_link(link: str | None) -> str | None:
    """A link a notice may carry: https or none. It came from someone else's invite and
    is opened by a tap, so nothing else is kept."""
    if link is None:
        return None
    # No spaces, control characters or backslashes anywhere: a parser may read past
    # them, a browser may drop them or read "\\" as "/", and the two need not agree on
    # where the link goes.
    if len(link) > 2000 or any(
        c.isspace() or ord(c) < 32 or ord(c) == 127 or c == "\\" for c in link
    ):
        raise ValueError("a notice's link must be https")
    try:
        parts = urlsplit(link)
        host = parts.hostname
        # Read for its check alone: a port out of range or not a number raises.
        _ = parts.port
    except ValueError:
        raise ValueError("a notice's link must be https") from None
    # A name and password before the host ("https://bank.example@elsewhere") hides
    # where it goes.
    if (
        parts.scheme != "https"
        or not link.startswith("https://")
        or not host
        or "@" in parts.netloc
        or not _plain_host(host)
    ):
        raise ValueError("a notice's link must be https")
    return link


_HOST_LABEL = re.compile(r"^[A-Za-z0-9-]{1,63}$")


def _plain_host(host: str) -> bool:
    """A host name a browser reads the way we do: ASCII letters, digits and dashes in
    dotted labels, at least two. A label in another script is taken in its IDNA form;
    one that has none, or that holds a dot of another script ("。"), is not."""
    labels = host.split(".")
    if len(labels) < 2:
        return False
    for label in labels:
        if not label.isascii():
            try:
                label = label.encode("idna").decode("ascii")
            except UnicodeError:
                return False
        if not _HOST_LABEL.fullmatch(label):
            return False
    return True


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

    async def attention_notice(
        self,
        owner_id: str,
        key: str,
        *,
        title: str,
        text: str | None,
        now_ms: int,
        link: str | None = None,
        fresh_until_ms: int | None = None,
        detail: str | None = None,
        notice_id: str | None = None,
        due_ms: int | None = None,
    ) -> Notice:
        """A watcher's heads-up, held for the door: due now, from `attention`, its find's
        `key` as its source. `text` is None when no model wrote her line: it is shown by
        its title and its `detail`, and never spoken. `notice_id`, when given, is its id:
        the runner records it on the finds first. `due_ms`, when given, is when it
        may first be handed over, for one keyed `action:` (see `held`). Raises
        `ValueError` for a link that is not https."""
        ...

    async def release_notice(self, notice_id: str, text: str | None, now_ms: int) -> bool:
        """A held notice is due now, with her words if it had none. One already
        delivered is left as it is. Whether it was still held."""
        ...

    async def has_notice(self, owner_id: str, key: str) -> bool:
        """Whether a heads-up with this `key` was ever written, delivered or not."""
        ...

    async def notice(self, notice_id: str) -> Notice | None: ...

    async def held(self, owner_id: str, now_ms: int | None = None) -> list[Notice]:
        """Notices not delivered yet, oldest first. With `now_ms`, a told-about action
        (keyed `action:`) not yet due is left out: her line is still being written."""
        ...

    async def mark_delivered(self, notice_id: str, to: str, now_ms: int) -> bool:
        """Only a held notice can be delivered, so each is delivered once."""
        ...

    async def unmark_delivered(self, notice_id: str, to: str) -> bool:
        """Hold a notice again that `to` could not take. Only that page's delivery."""
        ...

    async def mark_notified(self, notice_id: str, now_ms: int) -> None: ...
