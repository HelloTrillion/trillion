"""What the watchers keep (the speaking up spec): every find once, and each watcher's place.

The runner and the API depend on this, never on SQLite. The SQLite store is
`storage/sqlite/attention_store.py`: only storage code talks to the database.

A find is claimed before anything is said about it, so a key is told at most once
across restarts (D5): a process that stops between claiming and telling leaves it
claimed, never told twice. At the next start `repair` marks told the claimed ones
whose notice was written after all; the rest stay claimed, and Settings shows them as
lost. Dismissing is a soft delete: the row stays, still known.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, Protocol

FindStatus = Literal["claimed", "told", "silent", "dismissed"]
# Recent finds, as Settings lists them: the dedup window the spec names.
RECENT_MS = 14 * 86_400_000


class _Keep(enum.Enum):
    KEEP = "keep"


# "Leave the cursor as it is", told apart from None, which clears it.
KEEP: Final = _Keep.KEEP
Cursor = str | None | _Keep


@dataclass(frozen=True, kw_only=True)
class FindRecord:
    key: str
    watcher: str
    first_seen_ms: int
    status: FindStatus
    notice_id: str | None
    dismissed_ms: int | None
    # The notice's title, when it became one.
    title: str | None = None
    # What it was, in the host's words ("Planning at 10:00 am"), when the watcher said.
    detail: str | None = None


@dataclass(frozen=True, kw_only=True)
class WatcherState:
    watcher: str
    last_check_ms: int | None = None
    # Where the watcher reads on from next time; its own format.
    cursor: str | None = None
    # The kind of what last went wrong (a class name), never its message.
    last_error: str | None = None
    notes: str = ""


class AttentionStore(Protocol):
    async def known(self, owner_id: str, keys: Sequence[str]) -> set[str]:
        """Which of `keys` were already found, whatever became of them."""
        ...

    async def claim(
        self,
        owner_id: str,
        watcher: str,
        keys: Sequence[str],
        now_ms: int,
        *,
        details: Mapping[str, str | None] | None = None,
    ) -> list[str]:
        """Record each key not yet known as claimed, with its detail, and return those,
        in order. A key already known, or repeated, is left out: it is never told again.
        A deferred key (`release`) is claimed again."""
        ...

    async def telling(self, owner_id: str, keys: Sequence[str], notice_id: str) -> None:
        """These claimed keys are about to become `notice_id`: recorded first, so a stop
        before `told` can be put right by `repair`."""
        ...

    async def told(self, owner_id: str, keys: Sequence[str], notice_id: str) -> None:
        """These claimed keys became `notice_id`."""
        ...

    async def silent(self, owner_id: str, keys: Sequence[str]) -> None:
        """These claimed keys were judged not worth saying (D6)."""
        ...

    async def release(self, owner_id: str, keys: Sequence[str]) -> None:
        """These claimed keys were never told (refused at a spend cap before anything
        was said): deferred, so the watcher's next read claims them afresh. A deferred
        find is not known, and not listed in recent finds."""
        ...

    async def repair(self, owner_id: str) -> int:
        """At start: a claimed key whose notice exists was told. Returns how many."""
        ...

    async def dismiss(self, owner_id: str, key: str, now_ms: int) -> bool:
        """Set a find aside; False when there is no such find."""
        ...

    async def recent(self, owner_id: str, now_ms: int) -> list[FindRecord]:
        """Finds first seen in the last `RECENT_MS`, newest first."""
        ...

    async def state(self, owner_id: str, watcher: str) -> WatcherState: ...

    async def states(self, owner_id: str) -> list[WatcherState]: ...

    def generation(self, owner_id: str, watcher: str) -> int:
        """Moves on at each `reset`: a check started before one must not undo it."""
        ...

    async def checked(
        self,
        owner_id: str,
        watcher: str,
        now_ms: int,
        *,
        cursor: Cursor = KEEP,
        generation: int | None = None,
    ) -> bool:
        """A check finished: when, and the cursor it moved to (`KEEP` keeps the old one,
        None clears it). Clears the last error. With `generation`, from before a reset,
        it writes nothing and says False."""
        ...

    async def reset(self, owner_id: str, watcher: str) -> None:
        """Turned on again: no last check and no cursor, so it starts from now (D2).
        Its notes stay. A check on its way meanwhile can't put them back."""
        ...

    async def failed(self, owner_id: str, watcher: str, kind: str) -> None:
        """A check went wrong: its kind only."""
        ...

    async def set_notes(self, owner_id: str, watcher: str, notes: str) -> None:
        """Raises `ValueError` past `NOTES_MAX`."""
        ...

    async def add_note(self, owner_id: str, watcher: str, line: str) -> bool:
        """Add one line to a watcher's notes, if it still fits in `NOTES_MAX`. False,
        and nothing changed, when it would not."""
        ...

    async def clear_notes(self, owner_id: str, watcher: str) -> None: ...
