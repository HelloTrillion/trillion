"""A live widget's refresh (via "refresh") replays reads only: the router refuses what an
unattended run may not use, any non-read, and every card (the live widgets spec, D4)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.spine.conftest import Spine
from tests.spine.test_unattended import ScheduleWrite, connected
from trillion.spine.confirm import Refusal, Status, decide
from trillion.spine.contract import (
    JSON,
    BaseTool,
    Risk,
    ToolCall,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from trillion.spine.deny_floor import DenyPolicy
from trillion.spine.registry import ToolRegistry
from trillion.spine.router import Router


class BoardRead(BaseTool):
    definition = ToolDefinition(
        name="board_peek",
        description="Read the board (test).",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk=Risk.READ,
        category="workspace",
        timeout_s=5.0,
    )

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        self.calls += 1
        return ToolResult(ok=True, content="board", effect="none")


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("slow_irreversible", {}),
        ("spend_money", {"amount_cents": 5}),
        ("file_create", {}),
        ("scripted_write", {}),
    ],
)
async def test_a_refresh_refuses_anything_but_a_read(
    spine: Spine, name: str, args: dict[str, Any]
) -> None:
    r = await spine.call(name, args, via="refresh")
    assert r.status == "refused" and r.reason is Refusal.UNATTENDED, name
    assert r.pending_id is None
    assert await spine.store.list_by_status(Status.PROPOSED) == []
    assert spine.slow.executions == 0 and spine.spend.calls == []


async def test_a_freed_connector_write_is_still_refused(spine: Spine) -> None:
    router = Router(connected(), spine.store, policy=DenyPolicy.default(), clock=spine.clock)
    for name in ("gmail_draft", "gmail_send", "gmail_search", "mcp_notes_write"):
        r = await router.execute(ToolCall(name=name, args={}), spine.ctx(via="refresh"))
        assert r.status == "refused" and r.reason is Refusal.UNATTENDED, name


async def test_workspace_and_schedule_tools_are_refused(spine: Spine) -> None:
    reg = ToolRegistry()
    board, schedule = BoardRead(), ScheduleWrite()
    reg.register(board, module="trillion.tools.workspace")
    reg.register(schedule, module="trillion.tools.schedule")
    router = Router(reg, spine.store, policy=DenyPolicy.default(), clock=spine.clock)
    for name in ("board_peek", "schedule_thing"):
        r = await router.execute(ToolCall(name=name, args={}), spine.ctx(via="refresh"))
        assert r.reason is Refusal.UNATTENDED, name
    assert board.calls == 0 and schedule.calls == 0


async def test_a_refresh_settles_or_represents_no_card(spine: Spine) -> None:
    card = await spine.call("slow_irreversible", {}, session_id="s1")
    assert card.pending_id is not None
    await spine.router.mark_delivered(card.pending_id, "local", "web")
    for tool in ("confirm_pending_action", "cancel_pending_action"):
        r = await spine.call(tool, {"pending_id": card.pending_id}, via="refresh", turn=5)
        assert r.reason is Refusal.UNATTENDED
    for decision in ("confirm", "cancel"):
        resolved = await spine.router.resolve(
            card.pending_id,
            decision,  # type: ignore[arg-type]
            spine.ctx(via="refresh", turn=5),
        )
        assert resolved.reason is Refusal.UNATTENDED
    again = await spine.router.represent(card.pending_id, spine.ctx(via="refresh"))
    assert again.reason is Refusal.UNATTENDED
    a = await spine.store.get_pending(card.pending_id)
    assert a is not None and a.status is Status.PROPOSED and spine.slow.executions == 0
    # Where the decision itself is made, too.
    for event in ("confirm", "cancel"):
        verdict = decide(a, event, spine.ctx(via="refresh", turn=5), spine.clock())  # type: ignore[arg-type]
        assert getattr(verdict, "reason", None) is Refusal.UNATTENDED


async def test_a_read_runs_and_the_deny_floor_still_applies(spine: Spine) -> None:
    r = await spine.call("plain_reader", {}, via="refresh")
    assert r.status == "succeeded" and r.content.startswith("<untrusted_tool_result")
    (spine.granted / "a.txt").write_text("hello")
    ok = await spine.call("file_read", {"path": str(spine.granted / "a.txt")}, via="refresh")
    assert ok.status == "succeeded"
    outside = await spine.call(
        "file_read", {"path": str(spine.granted.parent / "outside.txt")}, via="refresh"
    )
    assert outside.reason is Refusal.DENIED_BY_FLOOR


async def test_a_refresh_never_replays_the_web_herself_or_memory(spine: Spine) -> None:
    from trillion.spine.router import REFRESH_NEVER, REFRESH_NEVER_CATEGORIES
    from trillion.tools.describe import NAME, DescribeSelf
    from trillion.tools.memory import MemoryRecall
    from trillion.tools.web import FETCH, WebFetch

    # The names the spine keeps are the tools' own.
    assert {FETCH, NAME} == REFRESH_NEVER
    recall = MemoryRecall()
    assert recall.definition.category in REFRESH_NEVER_CATEGORIES
    reg = ToolRegistry()
    for tool in (WebFetch(), DescribeSelf(), recall):
        reg.register(tool)
    router = Router(reg, spine.store, policy=DenyPolicy.default(), clock=spine.clock)
    for d in (t.definition for t in (reg.get(FETCH), reg.get(NAME), recall) if t):
        assert d.risk is Risk.READ, d.name  # refused for what they are, not their risk
        r = await router.execute(ToolCall(name=d.name, args={}), spine.ctx(via="refresh"))
        assert r.status == "refused" and r.reason is Refusal.UNATTENDED, d.name
