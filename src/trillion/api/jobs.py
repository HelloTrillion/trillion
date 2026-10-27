"""Reads that run in the background (the background reads spec).

A slow read from outside is answered at once with "started" and runs here, on the
runtime rather than the turn, so the user can keep talking. Only reads run here:
writes and anything with a card keep their order in the turn (D1). At most three run
per conversation, and the same read is never started twice while one runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from trillion.spine.contract import JSON, Risk, ToolDefinition
from trillion.spine.hashing import canonical_json
from trillion.spine.registry import runs_unattended
from trillion.spine.router import RouterResponse
from trillion.spine.untrusted import is_outside

logger = logging.getLogger(__name__)

LIMIT = 3
# What the model is told at once, in place of the result (the spec's step 1).
STARTED = (
    "Running in the background; you will be told when it is in. Do not call it again; "
    "say it is on its way if asked."
)

Changed = Callable[[str, int], Awaitable[None]]
Then = Callable[["Job", RouterResponse], Awaitable[None]]


def goes_to_background(d: ToolDefinition | None) -> bool:
    """A read that runs without a card and reads someone else's words: an MCP, Google
    or Slack tool, stripe_revenue (an MCP tool), web_fetch or file_read (D1)."""
    return d is not None and d.risk is Risk.READ and runs_unattended(d) and is_outside(d.name, d)


def _key(tool: str, args: JSON) -> str:
    try:
        return f"{tool}:{canonical_json(args)}"
    except (TypeError, ValueError):
        # Arguments that cannot be compared are never taken for the same read.
        return f"{tool}:{secrets.token_hex(8)}"


@dataclass(kw_only=True)
class Job:
    id: str
    conversation_id: str
    tool: str
    args: JSON
    started_ms: int
    key: str
    task: asyncio.Task[None] | None = None


class Full(Exception):
    """This conversation already has as many reads running as it may."""


@dataclass
class _Kept:
    """What a finished job left for a conversation nobody had open: the reads its
    follow-up remembers for live widgets, and its ready notices."""

    reads: list[tuple[str, JSON]] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)


class Jobs:
    def __init__(
        self, *, clock: Callable[[], int], limit: int = LIMIT, changed: Changed | None = None
    ) -> None:
        self._clock = clock
        self.limit = limit
        # Told the conversation's count whenever it changes, so the page can show it.
        self.changed = changed
        self._jobs: dict[str, Job] = {}
        self._kept: dict[str, _Kept] = {}

    def running(self, conversation_id: str) -> list[Job]:
        return [j for j in self._jobs.values() if j.conversation_id == conversation_id]

    def same(self, conversation_id: str, tool: str, args: JSON) -> Job | None:
        """The job already running this read here, if any."""
        key = _key(tool, args)
        return next((j for j in self.running(conversation_id) if j.key == key), None)

    async def start(
        self,
        conversation_id: str,
        tool: str,
        args: JSON,
        work: Callable[[], Awaitable[RouterResponse]],
        then: Then,
    ) -> Job:
        """Run `work` as a job; `then` gets its response once it has left the running."""
        if len(self.running(conversation_id)) >= self.limit:
            raise Full(conversation_id)
        job = Job(
            id=secrets.token_hex(4),
            conversation_id=conversation_id,
            tool=tool,
            args=dict(args),
            started_ms=self._clock(),
            key=_key(tool, args),
        )
        self._jobs[job.id] = job
        job.task = asyncio.create_task(self._run(job, work, then))
        await self._tell(conversation_id)
        return job

    async def _run(
        self, job: Job, work: Callable[[], Awaitable[RouterResponse]], then: Then
    ) -> None:
        try:
            response = await work()
        except Exception as e:
            # A read that broke is a read that did not come back: the note says so.
            response = RouterResponse(status="failed", error_kind=type(e).__name__)
        finally:
            self._jobs.pop(job.id, None)
        # Off the list first: her follow-up never hears it is still running.
        await self._tell(job.conversation_id)
        try:
            await then(job, response)
        except Exception as e:
            logger.error("background read %s not delivered: %s", job.id, type(e).__name__)

    async def _tell(self, conversation_id: str) -> None:
        if self.changed is None:
            return
        try:
            await self.changed(conversation_id, len(self.running(conversation_id)))
        except Exception as e:
            logger.warning("job count not sent: %s", type(e).__name__)

    def keep_read(self, conversation_id: str, tool: str, args: JSON) -> None:
        self._kept.setdefault(conversation_id, _Kept()).reads.append((tool, dict(args)))

    def keep_notice(self, conversation_id: str, notice_id: str) -> None:
        self._kept.setdefault(conversation_id, _Kept()).notices.append(notice_id)

    def take(self, conversation_id: str) -> tuple[list[tuple[str, JSON]], list[str]]:
        """What finished jobs left for this conversation, once: their reads and notices."""
        kept = self._kept.pop(conversation_id, None)
        return (kept.reads, kept.notices) if kept is not None else ([], [])

    async def cancel(self, conversation_id: str) -> None:
        """The conversation is gone: its reads stop, and nothing is told of them."""
        self._kept.pop(conversation_id, None)
        await self._cancel([j for j in self.running(conversation_id)])

    async def stop(self) -> None:
        """The runtime is stopping: every read stops."""
        self._kept.clear()
        await self._cancel(list(self._jobs.values()))

    async def _cancel(self, jobs: list[Job]) -> None:
        tasks: list[asyncio.Task[Any]] = []
        for job in jobs:
            self._jobs.pop(job.id, None)
            if job.task is not None and not job.task.done():
                job.task.cancel()
                tasks.append(job.task)
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
