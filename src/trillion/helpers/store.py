"""What a helper you make keeps (the your helpers spec, AGT-1, AGT-7): its name, its
job, the notes it learns, and a conversation of its own.

Helpers depend on this, never on SQLite. The SQLite store is
`storage/sqlite/helper_store.py`: only storage code talks to the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from trillion.connectors.mcp.catalog import ENTRIES
from trillion.scheduler.store import NOTES_MAX

# At most this many helpers, and a name and a job at most this long.
MAX_HELPERS = 20
NAME_MAX = 40
JOB_MAX = 200
# What a card may draft beyond that: at most this many connections, and this many
# questions it asks first, each at most this long.
CONNECTORS_MAX = 4
ASKS_MAX = 3
ASK_MAX = 160
# The Connected tools entries a helper may suggest, by catalog id, with their titles.
CONNECTOR_TITLES: dict[str, str] = {e.id: e.title for e in ENTRIES}

__all__ = [
    "ASKS_MAX",
    "ASK_MAX",
    "CONNECTORS_MAX",
    "CONNECTOR_TITLES",
    "JOB_MAX",
    "MAX_HELPERS",
    "NAME_MAX",
    "NOTES_MAX",
    "HelperStore",
    "OwnHelper",
]


@dataclass(frozen=True, kw_only=True)
class OwnHelper:
    id: str
    owner_id: str
    name: str
    # One line: what it is for.
    job: str
    # What it has learned from the user, a line at a time (AGT-7).
    notes: str
    # Its own chat, kind 'helper', titled with its name.
    conversation_id: str
    # Paused when set: handed no work, and its routines paused with it.
    paused_ms: int | None
    created_at: int
    # Its picture: names from the lists in `helpers/look.py`.
    icon: str = "spark"
    color: str = "blue"
    # When its first hello ran; None until it has.
    hello_ms: int | None = None
    # Connected tools entries its job would use (catalog ids), and what it asks first.
    connectors: tuple[str, ...] = ()
    asks: tuple[str, ...] = ()


class HelperStore(Protocol):
    async def create(
        self,
        owner_id: str,
        name: str,
        job: str,
        now_ms: int,
        *,
        icon: str | None = None,
        color: str | None = None,
        notes: str = "",
        connectors: tuple[str, ...] = (),
        asks: tuple[str, ...] = (),
    ) -> OwnHelper | Literal["full", "taken"]:
        """Make a helper and its 'helper' conversation, titled with its name, together.
        "full" at `MAX_HELPERS`, "taken" when the name is, in any case. Its picture is
        `look.pick`'s: the icon and colour given when they are on the lists. The rest
        is what its card drafted, already checked and within its limits."""
        ...

    async def get(self, owner_id: str, helper_id: str) -> OwnHelper | None: ...

    async def by_conversation(self, owner_id: str, conversation_id: str) -> OwnHelper | None:
        """The helper whose own conversation this is, if any."""
        ...

    async def all(self, owner_id: str) -> list[OwnHelper]:
        """Every helper of the owner's, oldest first."""
        ...

    async def update(
        self,
        owner_id: str,
        helper_id: str,
        *,
        name: str | None = None,
        job: str | None = None,
        notes: str | None = None,
        connectors: tuple[str, ...] | None = None,
        asks: tuple[str, ...] | None = None,
    ) -> OwnHelper | Literal["taken"] | None:
        """A new name, job, notes, connections or questions: each one given replaces
        what it had. A new name renames its conversation too. "taken" when another
        helper has the name; None for no such helper."""
        ...

    async def add_note(self, owner_id: str, helper_id: str, line: str) -> bool:
        """Add a line to its notes (AGT-7), only while they stay within `NOTES_MAX`.
        False, and nothing changed, otherwise or for no such helper."""
        ...

    async def set_look(self, owner_id: str, helper_id: str, icon: str, color: str) -> bool:
        """A new picture. False, and nothing changed, for a name not on the lists or no
        such helper."""
        ...

    async def said_hello(self, owner_id: str, helper_id: str, now_ms: int) -> bool:
        """Mark its first hello as run: True only the first time, for one call at once."""
        ...

    async def pause(self, owner_id: str, helper_id: str, now_ms: int) -> bool:
        """Mark it paused. False if it already was, or there is none. Its schedules are
        `helpers/pausing.py`'s to pause."""
        ...

    async def resume(self, owner_id: str, helper_id: str) -> bool: ...

    async def delete(self, owner_id: str, helper_id: str, now_ms: int) -> bool:
        """Cancel its schedules, keep its conversation as a plain chat, and remove it,
        together. False for no such helper."""
        ...
