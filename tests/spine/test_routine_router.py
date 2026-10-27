"""A routine's run (via "routine"): everything a timed ask may not do, it may not do
either, except show a board, and only on the conversation it was started for (the
routines spec, D5)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.spine.conftest import Spine
from trillion.spine.confirm import Refusal, Status
from trillion.spine.contract import (
    JSON,
    ActionSummary,
    BaseTool,
    Risk,
    ToolCall,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from trillion.spine.deny_floor import DenyPolicy
from trillion.spine.registry import (
    ROUTINE_BOARD,
    ROUTINE_LIST,
    ROUTINE_NOTHING,
    ToolRegistry,
    runs_in_routine,
)
from trillion.spine.router import Router
from trillion.tools.day import DAY_OPEN
from trillion.tools.routine import CATEGORY, RoutineNothing
from trillion.tools.schedule import ScheduleList
from trillion.tools.workspace import CATEGORY as BOARD_CATEGORY
from trillion.tools.workspace import REMOVE, SHOW, UPDATE


class Counted(BaseTool):
    """A tool that counts its calls, under whatever name, risk and category."""

    def __init__(self, name: str, category: str, risk: Risk = Risk.READ) -> None:
        self.definition = ToolDefinition(
            name=name,
            description=f"{name} (test).",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            risk=risk,
            category=category,
            timeout_s=5.0,
        )
        self.calls: list[str] = []

    def describe(self, args: JSON) -> ActionSummary:
        return ActionSummary(text=self.definition.name)

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        self.calls.append(ctx.session_id)
        return ToolResult(ok=True, content="done", effect="confirmed")


def routed(spine: Spine) -> tuple[Router, dict[str, Counted]]:
    reg = ToolRegistry()
    tools = {
        name: Counted(name, category)
        for name, category in (
            (SHOW, "workspace"),
            (UPDATE, "workspace"),
            (REMOVE, "workspace"),
            ("workspace_close", "workspace"),
            (DAY_OPEN, "workspace"),
            ("ask_schedule", "schedule"),
            ("rule_keep", "rules"),
            ("routine_narrow", "routine"),
            ("schedule_list", "schedule"),
            ("plain_read", "test"),
        )
    }
    for name in ("reminder_set", "schedule_cancel"):
        tools[name] = Counted(name, "schedule", Risk.WRITE)
    tools["send_it"] = Counted("send_it", "test", Risk.IRREVERSIBLE)
    for tool in tools.values():
        reg.register(tool, module="tests.spine")
    reg.register(RoutineNothing())
    return Router(reg, spine.store, policy=DenyPolicy.default(), clock=spine.clock), tools


def test_the_names_the_spine_holds_are_the_tools_names() -> None:
    assert {SHOW, UPDATE, REMOVE} == ROUTINE_BOARD
    assert RoutineNothing.definition.name == ROUTINE_NOTHING
    assert ScheduleList.definition.name == ROUTINE_LIST
    assert ScheduleList.definition.category == "schedule"
    assert RoutineNothing.definition.category == CATEGORY
    assert BOARD_CATEGORY == "workspace"


def test_the_view_is_the_unattended_one_with_the_board_and_nothing(spine: Spine) -> None:
    router, _ = routed(spine)
    reg = router._registry
    names = {d.name for d in reg.routine().definitions()}
    assert names == {SHOW, UPDATE, REMOVE, ROUTINE_NOTHING, ROUTINE_LIST, "plain_read"}
    unattended = {d.name for d in reg.unattended().definitions()}
    assert unattended == {"plain_read"}  # a timed ask still gets none of them


def test_only_the_tools_by_their_own_family_and_risk() -> None:
    impostor = Counted(SHOW, "schedule")
    assert not runs_in_routine(impostor.definition)
    writes = Counted(ROUTINE_NOTHING, "routine", Risk.WRITE)
    assert not runs_in_routine(writes.definition)
    assert runs_in_routine(Counted(ROUTINE_LIST, "schedule").definition)
    assert not runs_in_routine(Counted(ROUTINE_LIST, "rules").definition)
    assert not runs_in_routine(Counted(ROUTINE_LIST, "schedule", Risk.WRITE).definition)


async def test_the_board_tools_run_on_its_own_conversation(spine: Spine) -> None:
    router, tools = routed(spine)
    ctx = spine.ctx(via="routine", session_id="target", routine_target="target")
    for name in (SHOW, UPDATE, REMOVE):
        r = await router.execute(ToolCall(name=name, args={}), ctx)
        assert r.status == "succeeded", name
        assert tools[name].calls == ["target"]


@pytest.mark.parametrize(
    "where",
    [
        {"session_id": "other", "routine_target": "target"},
        {"session_id": "target", "routine_target": None},
        {"session_id": "other", "routine_target": None},
    ],
)
async def test_the_board_tools_are_refused_anywhere_else(
    spine: Spine, where: dict[str, Any]
) -> None:
    router, tools = routed(spine)
    for name in (SHOW, UPDATE, REMOVE):
        r = await router.execute(ToolCall(name=name, args={}), spine.ctx(via="routine", **where))
        assert r.status == "refused" and r.reason is Refusal.UNATTENDED, name
        assert "own conversation" in r.detail
        assert tools[name].calls == []


async def test_a_timed_ask_still_gets_no_board(spine: Spine) -> None:
    router, tools = routed(spine)
    ctx = spine.ctx(via="schedule", session_id="target", routine_target="target")
    for name in (SHOW, ROUTINE_NOTHING):
        r = await router.execute(ToolCall(name=name, args={}), ctx)
        assert r.status == "refused" and r.reason is Refusal.UNATTENDED
    assert tools[SHOW].calls == []


@pytest.mark.parametrize(
    "name",
    [DAY_OPEN, "workspace_close", "ask_schedule", "rule_keep", "routine_narrow", "send_it"],
)
async def test_everything_a_timed_ask_is_refused_a_routine_is_refused(
    spine: Spine, name: str
) -> None:
    router, tools = routed(spine)
    ctx = spine.ctx(via="routine", session_id="target", routine_target="target")
    r = await router.execute(ToolCall(name=name, args={}), ctx)
    assert r.status == "refused" and r.reason is Refusal.UNATTENDED
    assert r.pending_id is None and tools[name].calls == []
    assert await spine.store.list_by_status(Status.PROPOSED) == []


async def test_a_routine_settles_no_card(spine: Spine) -> None:
    card = await spine.call("slow_irreversible", {}, session_id="target")
    assert card.pending_id is not None
    ctx = spine.ctx(via="routine", session_id="target", routine_target="target", turn=5)
    for tool in ("confirm_pending_action", "cancel_pending_action"):
        r = await spine.router.execute(
            ToolCall(name=tool, args={"pending_id": card.pending_id}), ctx
        )
        assert r.reason is Refusal.UNATTENDED
    assert (await spine.router.resolve(card.pending_id, "confirm", ctx)).reason is (
        Refusal.UNATTENDED
    )
    assert (await spine.router.represent(card.pending_id, ctx)).reason is Refusal.UNATTENDED
    assert spine.slow.executions == 0


async def test_routine_nothing_runs_only_in_a_routine(spine: Spine) -> None:
    router, _ = routed(spine)
    call = ToolCall(name=ROUTINE_NOTHING, args={})
    r = await router.execute(call, spine.ctx(via="routine", routine_target="s1"))
    assert r.status == "succeeded"
    # Someone there: offered to nobody, and refused by the tool itself if called.
    typed = await router.execute(call, spine.ctx(via="text"))
    assert typed.status == "failed"


def test_an_offered_view_is_live_and_filtered(spine: Spine) -> None:
    router, _ = routed(spine)
    reg = router._registry
    view = reg.offered(lambda d: d.category != CATEGORY)
    assert view.definition(ROUTINE_NOTHING) is None and view.get(ROUTINE_NOTHING) is None
    assert "confirm_pending_action" in {d.name for d in view.definitions()}
    late = Counted("late_read", "test")
    reg.register(late, module="tests.spine")
    assert view.get("late_read") is late  # registered after the view was taken
    assert "late_read" in {d.name for d in view.definitions()}
    assert ROUTINE_NOTHING not in {d.name for d in view.definitions()}


async def test_a_routine_may_list_the_schedule_and_nothing_more(spine: Spine) -> None:
    router, tools = routed(spine)
    ctx = spine.ctx(via="routine", session_id="target", routine_target="target")
    listed = await router.execute(ToolCall(name=ROUTINE_LIST, args={}), ctx)
    assert listed.status == "succeeded" and tools[ROUTINE_LIST].calls == ["target"]
    for name in ("reminder_set", "schedule_cancel", "ask_schedule"):
        r = await router.execute(ToolCall(name=name, args={}), ctx)
        assert r.status == "refused" and r.reason is Refusal.UNATTENDED, name
        assert tools[name].calls == []
    # A plain timed ask still may not.
    timed = spine.ctx(via="schedule", session_id="target")
    r = await router.execute(ToolCall(name=ROUTINE_LIST, args={}), timed)
    assert r.status == "refused" and tools[ROUTINE_LIST].calls == ["target"]
