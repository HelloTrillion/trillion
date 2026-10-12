"""Her memory as one object: write, recall, correct, forget, and reconcile with the files.

Everything else (the tools, the turn that brings memories along with a message)
goes through this. The text of a memory is never logged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from trillion.memory import files
from trillion.memory.rank import rank
from trillion.memory.types import (
    MAX_TEXT,
    SOURCES,
    TYPES,
    Memory,
    MemoryError_,
    MemoryType,
    Source,
)
from trillion.storage.sqlite.memory_store import SqliteMemoryStore

logger = logging.getLogger(__name__)

# How many she may hold. Reached, a write is refused and said so, never dropped.
MAX_ACTIVE = 5000
RECALL_LIMIT = 8


@dataclass(frozen=True, kw_only=True)
class Written:
    memory: Memory
    # True when the same thing was already remembered: nothing new was stored.
    duplicate: bool = False


@dataclass(frozen=True, kw_only=True)
class SyncReport:
    """What reconciling the index with the files found. Counts only (MEM-6b)."""

    total: int = 0
    added: int = 0
    changed: int = 0
    missing: int = 0
    unreadable: int = 0
    complete: bool = True

    @property
    def diverged(self) -> bool:
        return bool(self.added or self.changed or self.missing or self.unreadable) or (
            not self.complete
        )


def _same(a: str, b: str) -> bool:
    return " ".join(a.lower().split()) == " ".join(b.lower().split())


class MemoryBook:
    def __init__(self, store: SqliteMemoryStore, root: Path, owner_id: str) -> None:
        self._store = store
        self._root = root
        self.owner_id = owner_id
        # Every memory whose file is present, by id. Rebuilt by `sync`, kept by each change.
        self._known: dict[str, Memory] = {}
        self._lock = asyncio.Lock()
        self.last_sync = SyncReport()

    # ── Reading ──────────────────────────────────────────────────────────

    def peek(self, memory_id: str) -> Memory | None:
        return self._known.get(memory_id)

    def count(self) -> int:
        return sum(m.current for m in self._known.values())

    def recall(
        self,
        query: str = "",
        *,
        type: MemoryType | None = None,
        history: bool = False,
        limit: int = RECALL_LIMIT,
    ) -> list[Memory]:
        """What she remembers about `query`, best first; the newest when there is no query.

        A retracted or superseded memory is never returned as current (MEM-7): it
        comes back only when `history` is asked for, and says what it is."""
        pool = [
            m
            for m in self._known.values()
            if (history or m.current) and (type is None or m.type == type)
        ]
        if query.strip():
            return rank(pool, query, limit)
        return sorted(pool, key=lambda m: (m.as_of, m.id), reverse=True)[:limit]

    # ── Changing ─────────────────────────────────────────────────────────

    async def write(
        self,
        *,
        type: MemoryType,
        text: str,
        source: Source,
        moves: bool,
        now_ms: int,
        replaces: str | None = None,
    ) -> Written:
        body = files.clean(text)
        if type not in TYPES or source not in SOURCES:
            raise MemoryError_("bad_request")
        if not body:
            raise MemoryError_("empty")
        if len(body) > MAX_TEXT:
            raise MemoryError_("too_long")
        async with self._lock:
            old = self._known.get(replaces) if replaces else None
            if replaces and (old is None or not old.current):
                raise MemoryError_("not_found")
            for existing in self._known.values():
                if existing.current and existing is not old and _same(existing.text, body):
                    if old is not None:
                        # Already remembered, and now said to correct an older memory:
                        # the older one is retired in favour of the one already held.
                        await self._retire(old, existing.id, now_ms)
                    return Written(memory=existing, duplicate=True)
            if old is None and self.count() >= MAX_ACTIVE:
                raise MemoryError_("full")
            new = Memory(
                id=files.new_id(),
                type=type,
                text=body,
                source=source,
                moves=moves,
                created_at=now_ms,
                as_of=now_ms,
                supersedes=old.id if old else None,
            )
            # The new fact first: if anything fails after this, both are still on disk
            # and nothing true has been lost.
            await self._save(new, now_ms)
            if old is not None:
                await self._retire(old, new.id, now_ms)
            return Written(memory=new)

    async def _retire(self, old: Memory, by: str, now_ms: int) -> None:
        retired = replace(old, status="superseded", superseded_by=by, version=old.version + 1)
        await self._save(retired, now_ms)

    def related(self, m: Memory, limit: int = 3) -> list[Memory]:
        """Other current memories on the same subject: the ones a new fact may have outdated."""
        return [o for o in self.recall(m.text, limit=limit + 1) if o.id != m.id][:limit]

    async def forget(self, memory_id: str, now_ms: int) -> Memory:
        """Retract one memory. It stays on disk, marked, and is no longer recalled (SPN-9)."""
        async with self._lock:
            m = self._known.get(memory_id)
            if m is None:
                raise MemoryError_("not_found")
            if m.status == "retracted":
                return m
            retracted = replace(m, status="retracted", version=m.version + 1)
            await self._save(retracted, now_ms)
            return retracted

    async def _save(self, m: Memory, now_ms: int) -> None:
        try:
            digest = await asyncio.to_thread(files.write, self._root, m)
        except OSError as e:
            logger.error("memory %s could not be written: %s", m.id, type(e).__name__)
            raise MemoryError_("disk") from e
        await self._store.put(self.owner_id, m, digest, now_ms)
        self._known[m.id] = m

    # ── Files and index ──────────────────────────────────────────────────

    async def sync(self, now_ms: int) -> SyncReport:
        """Bring the index in line with the files, and say what differed.

        The files win. A file edited by hand updates its row; a new file is
        adopted; a file that is gone leaves its row in place, marked and not
        recalled. A scan that could not finish marks nothing as gone."""
        async with self._lock:
            scan = await asyncio.to_thread(files.scan, self._root)
            rows = {r.memory.id: r for r in await self._store.all(self.owner_id)}
            known: dict[str, Memory] = {}
            added = changed = missing = 0
            for found in scan.found:
                row = rows.get(found.memory.id)
                if row is None:
                    added += 1
                elif row.digest != found.digest or row.missing or row.memory != found.memory:
                    changed += 1
                else:
                    known[found.memory.id] = found.memory
                    continue
                await self._store.put(self.owner_id, found.memory, found.digest, now_ms)
                known[found.memory.id] = found.memory
            present = set(known)
            unreadable_ids = {
                name.rsplit("/", 1)[-1].removesuffix(".md") for name in scan.unreadable
            }
            for memory_id, row in rows.items():
                if memory_id in present:
                    continue
                if memory_id in unreadable_ids or not scan.complete:
                    # Its file is there but cannot be read right now, or the folder could
                    # not be listed: keep using what the index last knew.
                    if not row.missing:
                        known[memory_id] = row.memory
                    continue
                missing += 1
                if not row.missing:
                    await self._store.mark_missing(self.owner_id, memory_id, now_ms)
            self._known = known
            self.last_sync = SyncReport(
                total=len(known),
                added=added,
                changed=changed,
                missing=missing,
                unreadable=len(scan.unreadable),
                complete=scan.complete,
            )
            if self.last_sync.diverged:
                r = self.last_sync
                logger.warning(
                    "memory files and index differed: %d adopted, %d changed on disk, "
                    "%d missing, %d unreadable%s",
                    r.added,
                    r.changed,
                    r.missing,
                    r.unreadable,
                    "" if r.complete else ", and the folder could not be fully read",
                )
            return self.last_sync


def recalled_tokens(memories: Sequence[Memory]) -> int:
    """A rough size of what a recall would add to a prompt, for the bound in MEM-6b."""
    return sum(len(m.text) for m in memories) // 4
