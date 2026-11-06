"""Only she dispatches (AGT-2): in a helper's chat or run, the router refuses the tools
that make helpers or hand them work, before any of their code runs, whether or not they
were offered (the your helpers spec)."""

from __future__ import annotations

import importlib
import inspect
from typing import get_args

import pytest

from tests.spine.conftest import Spine
from trillion.spine.confirm import Refusal, Status
from trillion.spine.contract import (
    JSON,
    ActionSummary,
    BaseTool,
    Precondition,
    Risk,
    ToolCall,
    ToolContext,
    ToolDefinition,
    ToolResult,
    Via,
)
from trillion.spine.deny_floor import DenyPolicy
from trillion.spine.registry import ToolRegistry
from trillion.spine.router import EXPIRY_MS, HELPER_NEVER, Router

VIAS: tuple[Via, ...] = get_args(Via)
HELPER = "h1"


class Counted(BaseTool):
    """A stand-in under a helper tool's name, counting the calls that reach its code."""

    def __init__(self, name: str, risk: Risk = Risk.READ) -> None:
        self.definition = ToolDefinition(
            name=name,
            description=f"{name} (test).",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            risk=risk,
            category="helper",
            timeout_s=5.0,
        )
        self.calls = 0
        self.prechecks = 0

    def describe(self, args: JSON) -> ActionSummary:
        return ActionSummary(text=self.definition.name)

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        self.prechecks += 1
        return Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        self.calls += 1
        return ToolResult(ok=True, content="done", effect="none")


def _router(spine: Spine) -> tuple[Router, dict[str, Counted]]:
    reg = ToolRegistry()
    tools = {name: Counted(name) for name in sorted(HELPER_NEVER)}
    for t in tools.values():
        reg.register(t)
    return Router(reg, spine.store, policy=DenyPolicy.default(), clock=spine.clock), tools


def test_the_never_list() -> None:
    assert frozenset({"helper_create", "helper_ask", "helper_suggest"}) == HELPER_NEVER


@pytest.mark.parametrize("via", VIAS)
@pytest.mark.parametrize("name", sorted(HELPER_NEVER))
async def test_a_helper_never_dispatches(spine: Spine, name: str, via: Via) -> None:
    router, tools = _router(spine)
    ctx = spine.ctx(via=via, helper_id=HELPER)
    r = await router.execute(ToolCall(name=name, args={}), ctx)
    assert r.status == "refused" and r.reason is Refusal.NOT_FOR_HELPERS, (name, via)
    assert r.detail == "only Trillion makes helpers or hands them work"
    assert r.pending_id is None
    assert tools[name].calls == 0 and tools[name].prechecks == 0
    assert await spine.store.list_by_status(Status.PROPOSED) == []
    (row,) = [a for a in await spine.store.list_audit() if a.tool == name]
    assert row.decision == "refused"


@pytest.mark.parametrize("name", sorted(HELPER_NEVER))
async def test_she_may_use_them(spine: Spine, name: str) -> None:
    router, tools = _router(spine)
    r = await router.execute(ToolCall(name=name, args={}), spine.ctx(via="text"))
    assert r.status == "succeeded", name
    assert tools[name].calls == 1
    (row,) = [a for a in await spine.store.list_audit() if a.tool == name]
    assert row.decision != "refused"


async def test_refused_whether_or_not_it_is_registered(spine: Spine) -> None:
    """Not offered and not even known here: still never run, and still audited."""
    reg = ToolRegistry()
    router = Router(reg, spine.store, policy=DenyPolicy.default(), clock=spine.clock)
    r = await router.execute(
        ToolCall(name="helper_ask", args={}), spine.ctx(via="text", helper_id=HELPER)
    )
    assert r.status == "refused" and r.reason is Refusal.NOT_FOR_HELPERS
    assert [a.tool for a in await spine.store.list_audit()] == ["helper_ask"]


async def test_other_tools_still_run_in_a_helpers_chat(spine: Spine) -> None:
    reg = ToolRegistry()
    other = Counted("helper_update")
    reg.register(other)
    router = Router(reg, spine.store, policy=DenyPolicy.default(), clock=spine.clock)
    r = await router.execute(
        ToolCall(name="helper_update", args={}), spine.ctx(via="text", helper_id=HELPER)
    )
    assert r.status == "succeeded" and other.calls == 1


def test_the_names_are_the_tools_own() -> None:
    """The spine keeps the names so it needs nothing from the tools; they must match."""
    try:
        helper = importlib.import_module("trillion.tools.helper")
    except ModuleNotFoundError as e:
        if e.name != "trillion.tools.helper":
            raise
        pytest.skip("until Task 3 adds the helper tools")
    names = set()
    for _, cls in inspect.getmembers(helper, inspect.isclass):
        d = getattr(cls, "definition", None)
        if issubclass(cls, BaseTool) and isinstance(d, ToolDefinition):
            names.add(d.name)
            assert d.category == "helper", d.name
    assert names >= HELPER_NEVER


async def _her_card(spine: Spine) -> tuple[Router, Counted, str]:
    """A card for helper_create, proposed in her own chat."""
    reg = ToolRegistry()
    tool = Counted("helper_create", Risk.IRREVERSIBLE)
    reg.register(tool)
    router = Router(reg, spine.store, policy=DenyPolicy.default(), clock=spine.clock)
    r = await router.execute(ToolCall(name="helper_create", args={}), spine.ctx(via="text"))
    assert r.status == "confirmation_required" and r.pending_id is not None
    return router, tool, r.pending_id


async def test_a_helper_cannot_confirm_her_card(spine: Spine) -> None:
    router, tool, pid = await _her_card(spine)
    r = await router.resolve(pid, "confirm", spine.ctx(turn=2, via="tap", helper_id=HELPER))
    assert r.status == "refused" and r.reason is Refusal.NOT_FOR_HELPERS
    assert tool.calls == 0
    a = await spine.store.get_pending(pid)
    assert a is not None and a.status is Status.PROPOSED


async def test_a_helper_cannot_raise_her_card_again(spine: Spine) -> None:
    router, tool, pid = await _her_card(spine)
    spine.clock.advance(EXPIRY_MS + 1)
    r = await router.represent(pid, spine.ctx(turn=2, via="text", helper_id=HELPER))
    assert r.status == "refused" and r.reason is Refusal.NOT_FOR_HELPERS
    assert await spine.store.list_represented_from(pid) == []
    assert tool.calls == 0
