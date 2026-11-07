"""Her memory as one object: write, recall, correct, forget, and reconcile with the files.

Everything else (the tools, the turn that brings memories along with a message)
goes through this. The text of a memory is never logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from trillion.memory import files, meaning
from trillion.memory.meaning import Embedder, Vector
from trillion.memory.rank import blend, rank
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
# Memories embedded at a time when catching up in the background.
EMBED_BATCH = 16


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


def same_key(text: str) -> str:
    """What two memories saying the same thing have in common: case and spacing aside."""
    return " ".join(text.lower().split())


def _same(a: str, b: str) -> bool:
    return same_key(a) == same_key(b)


class MemoryBook:
    def __init__(
        self,
        store: SqliteMemoryStore,
        root: Path,
        owner_id: str,
        *,
        embedder: Embedder | None = None,
    ) -> None:
        self._store = store
        self._root = root
        self.owner_id = owner_id
        # Every memory whose file is present, by id. Rebuilt by `sync`, kept by each change.
        self._known: dict[str, Memory] = {}
        self._lock = asyncio.Lock()
        self.last_sync = SyncReport()
        # Search by meaning (MEM-2): None when it is not installed, or once it failed.
        self._embedder = embedder
        # Each memory's vector, by id, with the digest of the text it was made from.
        self._vectors: dict[str, tuple[str, Vector]] = {}
        self._catching_up: asyncio.Task[int] | None = None

    # ── Reading ──────────────────────────────────────────────────────────

    @property
    def by_meaning(self) -> bool:
        """Whether search by meaning is on for this run."""
        return self._embedder is not None

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
        """What she remembers about `query` by its words, best first; the newest when
        there is no query. `search` adds meaning.

        A retracted or superseded memory is never returned as current (MEM-7): it
        comes back only when `history` is asked for, and says what it is."""
        pool = self._pool(type, history)
        if query.strip():
            return rank(pool, query, limit)
        return sorted(pool, key=lambda m: (m.as_of, m.id), reverse=True)[:limit]

    async def search(
        self,
        query: str = "",
        *,
        type: MemoryType | None = None,
        history: bool = False,
        limit: int = RECALL_LIMIT,
    ) -> list[Memory]:
        """`recall`, by meaning as well as words when meaning search is on (MEM-2).

        The same pool as `recall`, so nothing retired is returned as current. Any
        failure falls back to words."""
        embedder = self._embedder
        if embedder is None or not query.strip():
            return self.recall(query, type=type, history=history, limit=limit)
        pool = self._pool(type, history)
        vectors = {m.id: v for m in pool if (v := self._vector(m)) is not None}
        if not vectors:
            return rank(pool, query, limit)

        def score() -> dict[str, float]:
            (asked,) = embedder.embed([query], query=True)
            return {i: meaning.similarity(asked, v) for i, v in vectors.items()}

        try:
            near = await asyncio.to_thread(score)
        except Exception as e:
            self._meaning_failed(e)
            return rank(pool, query, limit)
        return blend(pool, query, near, floor=embedder.floor, limit=limit)

    def _pool(self, type: MemoryType | None, history: bool) -> list[Memory]:
        return [
            m
            for m in self._known.values()
            if (history or m.current) and (type is None or m.type == type)
        ]

    def _vector(self, m: Memory) -> Vector | None:
        """Its vector, if one was made from the text it has now."""
        held = self._vectors.get(m.id)
        return held[1] if held is not None and held[0] == meaning.digest(m.text) else None

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
        as_of: int | None = None,
        origin: str | None = None,
    ) -> Written:
        """`as_of` is when the fact was known, if earlier than now (an imported note's
        date); `origin` is where an imported claim came from (CON-8)."""
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
                as_of=now_ms if as_of is None else min(as_of, now_ms),
                supersedes=old.id if old else None,
                origin=origin,
            )
            if problem := files.check(new):
                raise MemoryError_("bad_request", problem)
            # The new fact first: if anything fails after this, both are still on disk
            # and nothing true has been lost.
            await self._save(new, now_ms)
            if old is not None:
                await self._retire(old, new.id, now_ms)
            return Written(memory=new)

    async def _retire(self, old: Memory, by: str, now_ms: int) -> None:
        retired = replace(old, status="superseded", superseded_by=by, version=old.version + 1)
        await self._save(retired, now_ms)

    async def related(self, m: Memory, limit: int = 3) -> list[Memory]:
        """Other current memories on the same subject: the ones a new fact may have outdated."""
        return [o for o in await self.search(m.text, limit=limit + 1) if o.id != m.id][:limit]

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
        if m.current and self._vector(m) is None:
            # Found by meaning from the next request on; by its words until then.
            await self._embed([m], now_ms)

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
            await self._load_vectors()
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

    # ── Meaning (MEM-2) ──────────────────────────────────────────────────

    async def _load_vectors(self) -> None:
        if self._embedder is None:
            return
        stored = await self._store.vectors(self.owner_id, self._embedder.model)
        self._vectors = {
            i: (d, meaning.unpack(raw)) for i, (d, raw) in stored.items() if i in self._known
        }

    async def _embed(self, batch: Sequence[Memory], now_ms: int) -> int:
        """Make and keep vectors for `batch`. Never raises: a failure turns meaning
        search off for this run, and everything is still found by its words."""
        embedder = self._embedder
        if embedder is None or not batch:
            return 0
        texts = [m.text for m in batch]
        try:
            made = await asyncio.to_thread(embedder.embed, texts, query=False)
            if len(made) != len(batch) or any(len(v) != meaning.DIMENSIONS for v in made):
                raise meaning.MeaningError("bad_output")
            for m, v in zip(batch, made, strict=True):
                d = meaning.digest(m.text)
                await self._store.put_vector(
                    self.owner_id,
                    m.id,
                    model=embedder.model,
                    digest=d,
                    vector=meaning.pack(v),
                    now_ms=now_ms,
                )
                self._vectors[m.id] = (d, v)
        except Exception as e:
            self._meaning_failed(e)
            return 0
        return len(made)

    def _meaning_failed(self, e: Exception) -> None:
        kind = e.kind if isinstance(e, meaning.MeaningError) else type(e).__name__
        if self._embedder is not None:
            logger.warning("search by meaning is off for this run: %s", kind)
        self._embedder = None

    async def catch_up(self, now: Callable[[], int]) -> int:
        """Embed every current memory that has no vector for its text now, a batch at a
        time, and return how many were made. Runs in the background after startup."""
        made = 0
        while self._embedder is not None:
            todo = [m for m in self._known.values() if m.current and self._vector(m) is None]
            if not todo:
                break
            done = await self._embed(todo[:EMBED_BATCH], now())
            if not done:
                break
            made += done
        return made

    def start_catching_up(self, now: Callable[[], int]) -> None:
        """`catch_up` in the background, on the running loop. Stopped by `close`."""
        if self._embedder is not None and self._catching_up is None:
            self._catching_up = asyncio.create_task(self.catch_up(now))

    async def close(self) -> None:
        task, self._catching_up = self._catching_up, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def recalled_tokens(memories: Sequence[Memory]) -> int:
    """A rough size of what a recall would add to a prompt, for the bound in MEM-6b."""
    return sum(len(m.text) for m in memories) // 4
