"""A browsing task wired to a real router, as the browsing helper runs one."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from trillion import paths
from trillion.browser.broker import Mode
from trillion.browser.log import TaskLog
from trillion.browser.quarantine import Quarantine
from trillion.browser.runtime import BrowserRuntime
from trillion.browser.task import Task
from trillion.browser.tools import task_registry
from trillion.spine.contract import ToolCall, ToolContext
from trillion.spine.deny_floor import DenyPolicy
from trillion.spine.router import Router, RouterResponse
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.browser_log_store import SqliteBrowserLog
from trillion.storage.sqlite.spine_store import SqliteSpineStore


class Clock:
    def __init__(self) -> None:
        self.now = 1_700_000_000_000

    def __call__(self) -> int:
        return self.now


@dataclasses.dataclass
class Driven:
    task: Task
    router: Router
    store: SqliteSpineStore
    log: SqliteBrowserLog
    turn: int = 1

    def ctx(self, **kw: Any) -> ToolContext:
        base = ToolContext(
            owner_id="local",
            session_id=f"browse:{self.task.id}",
            turn=self.turn,
            call_id="c",
            via="text",
            read_outside=True,
            helper_id=f"browse:{self.task.id}",
        )
        return dataclasses.replace(base, **kw)

    async def call(self, name: str, **args: Any) -> RouterResponse:
        return await self.router.dispatch(ToolCall(name=name, args=args), self.ctx())

    async def tap(self, pending_id: str, decision: str = "confirm") -> RouterResponse:
        await self.router.mark_delivered(pending_id, "local", "test")
        return await self.router.resolve(
            pending_id,
            decision,
            self.ctx(via="tap", turn=self.turn + 1),  # type: ignore[arg-type]
        )

    async def rows(self) -> list[Any]:
        return await self.log.for_task("local", self.task.id, self.task.clock())


@asynccontextmanager
async def driven(
    rt: BrowserRuntime,
    tmp_path: Path,
    *,
    mode: Mode = "research",
    named: frozenset[str] = frozenset({"fixture.test"}),
    site: str | None = None,
    attended: bool = True,
    storage_state: Any = None,
    search_url: str = "http://fixture.test/search?q={q}",
    search_fallback: str | None = None,
) -> AsyncIterator[Driven]:
    conn = await connect(paths.db_path())
    await migrate(conn)
    store = SqliteSpineStore(conn)
    sink = SqliteBrowserLog(conn)
    clock = Clock()
    task = Task(
        runtime=rt,
        log=TaskLog(sink, "local", "0" * 16, clock),
        quarantine=Quarantine(tmp_path / "quarantine"),
        request="test",
        mode=mode,
        named_hosts=named,
        clock=clock,
        task_id="0" * 16,
        site=site,
        attended=attended,
        storage_state=storage_state,
        search_url=search_url,
        search_fallback=search_fallback,
    )
    router = Router(
        task_registry(task), store, policy=DenyPolicy.default(), clock=clock, require_delivery=True
    )
    await task.open()
    try:
        yield Driven(task=task, router=router, store=store, log=sink)
    finally:
        await task.close()
        await conn.close()
