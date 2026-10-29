"""What the watchers keep (the speaking up spec): every find once, and each watcher's place.

The runner and the API depend on this, never on SQLite. The SQLite store is
`storage/sqlite/attention_store.py`: only storage code talks to the database.

A find is claimed before anything is said about it, so a key is told at most once
across restarts (D5): a process that stops between claiming and telling leaves it
silent, never told twice. Dismissing is a soft delete: the row stays, still known.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

FindStatus = Literal["told", "silent", "dismissed"]
# Recent finds, as Settings lists them: the dedup window the spec names.
RECENT_MS = 14 * 86_400_000


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
        self, owner_id: str, watcher: str, keys: Sequence[str], now_ms: int
    ) -> list[str]:
        """Record each key not yet known as silent, and return those, in order. A key
        already known, or repeated, is left out: it is never told again."""
        ...

    async def told(self, owner_id: str, keys: Sequence[str], notice_id: str) -> None:
        """These claimed keys became `notice_id`."""
        ...

    async def dismiss(self, owner_id: str, key: str, now_ms: int) -> bool:
        """Set a find aside; False when there is no such find."""
        ...

    async def recent(self, owner_id: str, now_ms: int) -> list[FindRecord]:
        """Finds first seen in the last `RECENT_MS`, newest first."""
        ...

    async def state(self, owner_id: str, watcher: str) -> WatcherState: ...

    async def states(self, owner_id: str) -> list[WatcherState]: ...

    async def checked(
        self, owner_id: str, watcher: str, now_ms: int, *, cursor: str | None = None
    ) -> None:
        """A check finished: when, and the cursor it moved to (None keeps the old one).
        Clears the last error."""
        ...

    async def failed(self, owner_id: str, watcher: str, kind: str) -> None:
        """A check went wrong: its kind only."""
        ...

    async def set_notes(self, owner_id: str, watcher: str, notes: str) -> None:
        """Raises `ValueError` past `NOTES_MAX`."""
        ...

    async def clear_notes(self, owner_id: str, watcher: str) -> None: ...
