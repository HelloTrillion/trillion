"""The memory noter (MEM-4): a loop beside the server that reads quiet conversations.

A conversation is **quiet** once nothing has been said in it for `QUIET_MS` (ten
minutes) and no turn is running on it. Every `SWEEP_S` the noter takes the few quiet
conversations with something the user said since its last pass, oldest first, and
for each one reads what was said since that pass (`notes.py`). Each pass is recorded,
so the same words are never read twice, and a conversation that carries on is read
again from where the last pass ended once it is quiet again.

- **Off switch.** The user can turn it off in Settings. While off nothing is read,
  and turning it back on never reads what was said meanwhile: only what is said from
  then on. It is on from the first start, and reads nothing said before that start.
- **Spend.** Its calls are background work (`memories`) on the usual model, never a
  switch (BRN-11, the spend caps spec). At the hard cap it pauses and records nothing,
  so what was said is read once the cap lifts.
- **One at a time.** One pass runs at once, and a sweep stops at the first refusal.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from trillion.brain.types import ModelProvider
from trillion.memory import notes
from trillion.memory.book import MemoryBook
from trillion.spend.caller import as_caller
from trillion.spend.gate import SpendCapped
from trillion.spend.types import Work
from trillion.storage.sqlite.notes_store import Outcome, SqliteNotesStore

logger = logging.getLogger(__name__)

PREF = "memory_notes"
WORK: Work = "memories"
QUIET_MS = 10 * 60_000
SWEEP_S = 60.0
# Conversations read in one sweep: a backlog after a long time away is read a few at
# a time, not all at once.
PER_SWEEP = 5


class Prefs(Protocol):
    async def get(self, owner_id: str, key: str) -> Any: ...

    async def put(self, owner_id: str, key: str, value: Any, now_ms: int) -> None: ...


class Noter:
    def __init__(
        self,
        store: SqliteNotesStore,
        book: MemoryBook,
        prefs: Prefs,
        *,
        provider: Callable[[], ModelProvider | None],
        held: Callable[[Work], Awaitable[bool]],
        busy: Callable[[str], bool],
        clock: Callable[[], int],
        owner: str,
        sweep_s: float = SWEEP_S,
    ) -> None:
        self._store = store
        self._book = book
        self._prefs = prefs
        self._provider = provider
        self._held = held
        self._busy = busy
        self._clock = clock
        self._owner = owner
        self.sweep_s = sweep_s
        self.on = True
        # Nothing said before this is ever read: the first start, or the last switch on.
        self.since: int | None = None
        self._task: asyncio.Task[None] | None = None
        self._sweeping = asyncio.Lock()

    # ── The switch ───────────────────────────────────────────────────────

    async def load(self) -> None:
        raw = await self._prefs.get(self._owner, PREF)
        if isinstance(raw, dict) and isinstance(raw.get("since"), int):
            self.on = raw.get("on") is not False
            self.since = int(raw["since"])
            return
        # The first start with notes: on, from now.
        self.on, self.since = True, self._clock()
        await self._save()

    async def set_on(self, on: bool) -> None:
        """Off takes effect at once: a pass whose answer comes back after it keeps
        nothing. Turned back on, it reads only what is said from now: never what was
        said while it was off."""
        if on and not self.on:
            self.since = self._clock()
        self.on = on
        await self._save()

    async def _save(self) -> None:
        await self._prefs.put(
            self._owner, PREF, {"on": self.on, "since": self.since}, self._clock()
        )

    # ── The loop ─────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _loop(self) -> None:
        while True:
            # A sweep waits first: a page just opened, or a server just started, is
            # never held up by it.
            await asyncio.sleep(self.sweep_s)
            try:
                await self.sweep()
            except Exception as e:
                # The class name only: a message could carry what was said.
                logger.error("memory notes sweep failed: %s", type(e).__name__)

    async def sweep(self) -> int:
        """Read the quiet conversations waiting, a few at most. Returns how many passes
        were recorded."""
        async with self._sweeping:
            if not self.on or self.since is None:
                return 0
            provider = self._provider()
            if provider is None or await self._held(WORK):
                return 0
            now = self._clock()
            waiting = await self._store.quiet(
                self._owner, since_ms=self.since, quiet_before_ms=now - QUIET_MS, limit=PER_SWEEP
            )
            done = 0
            for q in waiting:
                if self._busy(q.conversation_id):
                    continue
                try:
                    with as_caller("background", q.conversation_id, work=WORK):
                        await self._pass(provider, q.conversation_id, q.noted, self.since)
                except SpendCapped:
                    # Paused at the cap: nothing recorded, so it is read once it lifts.
                    return done
                done += 1
            return done

    async def _pass(self, provider: ModelProvider, cid: str, noted: int, since: int) -> None:
        said, upto = await self._store.said_after(cid, self._owner, after_id=noted, since_ms=since)
        outcome: Outcome = "nothing"
        kept = 0
        if any(s.who == "user" for s in said):
            try:
                answer, _usage = await asyncio.wait_for(
                    notes.ask(provider, [(s.who, s.text) for s in said]), notes.TIMEOUT_S
                )
            except SpendCapped:
                raise
            except Exception as e:
                # Recorded as failed, and these messages are not read again: a note is a
                # nicety, and a call that fails every minute would cost every minute.
                logger.info("memory notes for %s failed: %s", cid, type(e).__name__)
                answer = ""
            if not self.on:
                # Switched off while the model was reading: nothing is kept or recorded.
                return
            found = notes.read(answer)
            if found is None:
                outcome = "failed"
            else:
                written = await notes.keep(self._book, found, self._clock())
                kept = len(written)
                outcome = "saved" if kept else "nothing"
        await self._store.record(self._owner, cid, upto, outcome, kept, self._clock())
        if kept:
            logger.info("memory notes for %s: %d kept", cid, kept)
