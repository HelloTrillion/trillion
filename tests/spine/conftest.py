from __future__ import annotations

import dataclasses
import logging
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.spine.fakes import (
    MARKER,
    ScriptedTool,
    ScriptedWrite,
    SlowIrreversible,
    SpendTool,
    UnconfiguredReader,
)
from trillion import paths
from trillion.spine.contract import ToolCall, ToolContext
from trillion.spine.deny_floor import DenyPolicy
from trillion.spine.loop_guard import LoopGuard
from trillion.spine.registry import ToolRegistry
from trillion.spine.router import Router, RouterResponse
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.spine_store import SqliteSpineStore
from trillion.tools.files import (
    FileAppend,
    FileCreate,
    FileEdit,
    FileList,
    FileOverwrite,
    FileRead,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_700_000_000_000

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> None:
        self.now += ms


@dataclasses.dataclass
class Spine:
    router: Router
    registry: ToolRegistry
    store: SqliteSpineStore
    clock: FakeClock
    granted: Path
    spend: SpendTool
    slow: SlowIrreversible
    scripted: ScriptedTool
    reader: UnconfiguredReader
    writer: ScriptedWrite

    def ctx(self, **kw: Any) -> ToolContext:
        base = ToolContext(
            owner_id="local",
            session_id="s1",
            turn=1,
            call_id="c1",
            via="text",
            granted_roots=(self.granted,),
        )
        return dataclasses.replace(base, **kw)

    async def call(
        self, name: str, args: dict[str, Any], guard: LoopGuard | None = None, **ctx_kw: Any
    ) -> RouterResponse:
        return await self.router.execute(ToolCall(name=name, args=args), self.ctx(**ctx_kw), guard)


@pytest.fixture(autouse=True)
def no_values_in_logs(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    """Never-list #7: nothing a test passes as an argument or output may reach a log."""
    caplog.set_level(logging.DEBUG)
    yield
    # `caplog.records` here would hold only this teardown's records; check every phase.
    records = [r for when in ("setup", "call", "teardown") for r in caplog.get_records(when)]
    leaked = [f"{r.name}: {r.getMessage()[:200]}" for r in records if MARKER in r.getMessage()]
    assert leaked == [], leaked


@pytest.fixture
async def spine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Spine]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "trillion-home"))
    granted = tmp_path / "granted"
    granted.mkdir()
    conn = await connect(paths.db_path())
    await migrate(conn)
    store = SqliteSpineStore(conn)
    clock = FakeClock()
    spend, slow = SpendTool(), SlowIrreversible()
    scripted, reader, writer = ScriptedTool(), UnconfiguredReader(), ScriptedWrite()
    registry = ToolRegistry()
    for tool in (
        FileList(),
        FileRead(),
        FileCreate(),
        FileOverwrite(),
        FileAppend(),
        FileEdit(),
        spend,
        slow,
        scripted,
        reader,
        writer,
    ):
        registry.register(tool)
    router = Router(registry, store, policy=DenyPolicy.default(), clock=clock)
    yield Spine(router, registry, store, clock, granted, spend, slow, scripted, reader, writer)
    await conn.close()
