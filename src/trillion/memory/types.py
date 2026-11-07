"""One memory, and the words used to describe it (MEM-1, MEM-6a, MEM-7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, get_args

# What a memory is about. `user`: who they are. `feedback`: how they want her to
# work. `project`: something ongoing. `reference`: where something lives.
MemoryType = Literal["user", "feedback", "project", "reference"]
TYPES: tuple[MemoryType, ...] = get_args(MemoryType)

# Who it came from. A memory can inform an action; whatever its source, it can
# never authorize one (MEM-6a).
Source = Literal["user_statement", "imported_claim", "agent_inference"]
SOURCES: tuple[Source, ...] = get_args(Source)

# Nothing is deleted (SPN-9). A memory that stopped being true is retracted, or
# superseded by the one that corrects it, and keeps its history either way.
Status = Literal["active", "retracted", "superseded"]
STATUSES: tuple[Status, ...] = get_args(Status)

MAX_TEXT = 1200


@dataclass(frozen=True, kw_only=True)
class Memory:
    id: str
    type: MemoryType
    text: str
    source: Source
    # True for a fact that changes on its own: a balance, a price, an address (MEM-8).
    moves: bool
    status: Status = "active"
    version: int = 1
    # Milliseconds since the epoch. `as_of` is when the fact was last known to hold.
    created_at: int
    as_of: int
    supersedes: str | None = None
    superseded_by: str | None = None
    # Where an imported claim came from ("openclaw USER.md"). Kept in the file's
    # header only: the file is the truth and the index does not carry it (MEM-1).
    origin: str | None = field(default=None, compare=False)

    @property
    def current(self) -> bool:
        return self.status == "active"


class MemoryError_(Exception):
    """A memory operation that cannot go ahead. `kind` is safe to show."""

    def __init__(self, kind: str, message: str = "") -> None:
        super().__init__(message or kind)
        self.kind = kind
