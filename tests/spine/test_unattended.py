"""An unattended run (via "schedule") gets only tools that never ask, and the router
refuses it every card anyway (D5, SPN-2b, TESTING §4.3)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.spine.conftest import Spine
from trillion.connectors.google.tools import (
    GmailDraft,
    GmailRead,
    GmailSearch,
    GmailSend,
)
from trillion.connectors.google.tools import Wiring as GoogleWiring
from trillion.connectors.mcp.config import ServerConfig
from trillion.connectors.mcp.tool import McpTool, Offered
from trillion.connectors.slack.tools import SlackRead, SlackSearch
from trillion.connectors.slack.tools import Wiring as SlackWiring
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
)
from trillion.spine.deny_floor import DenyPolicy
from trillion.spine.registry import RegistryError, ToolRegistry
from trillion.spine.router import Router


class ScheduleWrite(BaseTool):
    definition = ToolDefinition(
        name="schedule_thing",
        description="Change the schedule (test).",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk=Risk.WRITE,
        category="schedule",
        timeout_s=5.0,
    )

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        self.calls += 1
        return ToolResult(ok=True, content="", effect="confirmed")


class Probe(BaseTool):
    """A gated tool that counts every hook the router could run before a card."""

    definition = ToolDefinition(
        name="probe_send",
        description="Send something (test).",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk=Risk.IRREVERSIBLE,
        category="test",
        timeout_s=5.0,
    )

    def __init__(self) -> None:
        self.touched: list[str] = []

    def describe(self, args: JSON) -> ActionSummary:
        self.touched.append("describe")
        return ActionSummary(text="Send it")

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        self.touched.append("precheck")
        return Precondition(ok=True)

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        self.touched.append("execute")
        return ToolResult(ok=True, content="", effect="confirmed")


def connected() -> ToolRegistry:
    """A registry with a connected server's tools, Google's and Slack's, some freed."""
    reg = ToolRegistry()

    async def call(name: str, args: JSON, timeout: float) -> Any:
        raise AssertionError("never called")

    server = ServerConfig(name="notes", transport="stdio", command="x", risks={"read": Risk.READ})
    for name in ("read", "write"):
        offered = Offered(name=name, description=name, input_schema={"type": "object"})
        reg.register(McpTool(server, offered, f"mcp_notes_{name}", call), module="trillion.mcp")
    google = GoogleWiring(google=None, client=lambda: None, accounts=lambda s: ["a@example.com"])  # type: ignore[arg-type]
    accounts = ["a@example.com"]
    reg.register(GmailSearch(google, free=False, accounts=accounts))
    reg.register(GmailRead(google, free=True, accounts=accounts))
    reg.register(GmailDraft(google, free=True, accounts=accounts))  # freed: a write
    reg.register(GmailSend(google, free=True, accounts=accounts))  # never freeable
    slack = SlackWiring(slack=None, token=lambda: None)  # type: ignore[arg-type]
    reg.register(SlackSearch(slack, free=False))
    reg.register(SlackRead(slack, free=True))
    reg.register(ScheduleWrite(), module="trillion.tools.schedule")
    return reg


def test_the_view_lists_only_tools_that_never_ask(spine: Spine) -> None:
    names = {d.name for d in spine.registry.unattended().definitions()}
    assert {"file_list", "file_read", "plain_reader", "scripted_write"} <= names
    assert not names & {"file_create", "file_overwrite", "spend_money", "slow_irreversible"}
    assert not names & {"confirm_pending_action", "cancel_pending_action"}
    view = connected().unattended()
    names = {d.name for d in view.definitions()}
    # What the user freed, and no more: nothing still asking first, nothing that can
    # never be freed, and nothing that changes the schedule.
    assert names == {"mcp_notes_read", "gmail_read", "gmail_draft", "slack_read"}
    for name in ("gmail_search", "gmail_send", "slack_search", "mcp_notes_write"):
        assert view.definition(name) is None and view.get(name) is None
    assert view.get("schedule_thing") is None
    assert view.get("confirm_pending_action") is None
    assert view.definition("cancel_pending_action") is None


def test_the_view_is_read_only(spine: Spine) -> None:
    view = spine.registry.unattended()
    with pytest.raises(RegistryError):
        view.register(ScheduleWrite())
    with pytest.raises(RegistryError):
        view.unregister("file_read")
    assert spine.registry.definition("file_create") is not None  # the real one is untouched


def test_the_view_is_taken_when_the_run_starts(spine: Spine) -> None:
    view = spine.registry.unattended()
    spine.registry.unregister("plain_reader")
    spine.registry.register(ScheduleWrite(), module="trillion.tools.schedule")
    assert view.get("plain_reader") is not None and view.get("schedule_thing") is None


@pytest.mark.parametrize(
    ("name", "args"),
    [("slow_irreversible", {}), ("spend_money", {"amount_cents": 5}), ("file_create", {})],
)
async def test_the_router_refuses_any_card_to_an_unattended_run(
    spine: Spine, name: str, args: dict[str, Any]
) -> None:
    # Called by name: it was never in the view, and the router does not need it to be.
    assert spine.registry.unattended().definition(name) is None
    r = await spine.call(name, args, via="schedule")
    assert r.status == "refused" and r.reason is Refusal.UNATTENDED
    assert r.pending_id is None
    assert await spine.store.list_by_status(Status.PROPOSED) == []
    assert spine.slow.executions == 0 and spine.spend.calls == []


async def test_a_gated_tool_is_refused_before_any_of_its_code_runs(spine: Spine) -> None:
    reg = ToolRegistry()
    probe = Probe()
    reg.register(probe)
    router = Router(reg, spine.store, policy=DenyPolicy.default(), clock=spine.clock)
    r = await router.execute(ToolCall(name="probe_send", args={}), spine.ctx(via="schedule"))
    assert r.reason is Refusal.UNATTENDED and probe.touched == []
    live = await router.execute(ToolCall(name="probe_send", args={}), spine.ctx())
    assert live.status == "confirmation_required"  # the same call, with someone there


async def test_a_connector_still_asking_first_is_refused_by_name(spine: Spine) -> None:
    reg = connected()
    router = Router(reg, spine.store, policy=DenyPolicy.default(), clock=spine.clock)
    for name in ("mcp_notes_write", "gmail_send", "gmail_search", "slack_search"):
        # Refused before even its arguments are read: none here would be valid.
        r = await router.execute(ToolCall(name=name, args={}), spine.ctx(via="schedule"))
        assert r.status == "refused" and r.reason is Refusal.UNATTENDED, name
    assert await spine.store.list_by_status(Status.PROPOSED) == []


async def test_nothing_that_changes_the_schedule_runs_unattended(spine: Spine) -> None:
    reg = ToolRegistry()
    tool = ScheduleWrite()
    reg.register(tool, module="trillion.tools.schedule")
    router = Router(reg, spine.store, policy=DenyPolicy.default(), clock=spine.clock)
    call = ToolCall(name="schedule_thing", args={})
    r = await router.execute(call, spine.ctx(via="schedule"))
    assert r.status == "refused" and r.reason is Refusal.UNATTENDED and tool.calls == 0
    assert (await router.execute(call, spine.ctx())).status == "succeeded"
    assert tool.calls == 1


async def test_an_unattended_run_settles_no_card(spine: Spine) -> None:
    card = await spine.call("slow_irreversible", {}, session_id="s1")
    assert card.pending_id is not None
    await spine.router.mark_delivered(card.pending_id, "local", "web")
    for tool in ("confirm_pending_action", "cancel_pending_action"):
        r = await spine.call(tool, {"pending_id": card.pending_id}, via="schedule", turn=5)
        assert r.reason is Refusal.UNATTENDED
    for decision in ("confirm", "cancel"):
        resolved = await spine.router.resolve(
            card.pending_id,
            decision,  # type: ignore[arg-type]
            spine.ctx(via="schedule", turn=5),
        )
        assert resolved.reason is Refusal.UNATTENDED
    again = await spine.router.represent(card.pending_id, spine.ctx(via="schedule"))
    assert again.reason is Refusal.UNATTENDED
    # Refused before anything is looked up, as resolve() is: an id tells it nothing.
    for decision in ("confirm", "cancel"):
        nothing = await spine.router.resolve(
            "no-such-card",
            decision,  # type: ignore[arg-type]
            spine.ctx(via="schedule"),
        )
        assert nothing.reason is Refusal.UNATTENDED
    missing = await spine.router.represent("no-such-card", spine.ctx(via="schedule"))
    assert missing.reason is Refusal.UNATTENDED
    assert (await spine.store.get_pending(card.pending_id)).status is Status.PROPOSED  # type: ignore[union-attr]
    assert spine.slow.executions == 0
    assert len(await spine.store.list_by_status(Status.PROPOSED)) == 1  # no second card


async def test_a_read_still_runs_unattended(spine: Spine) -> None:
    r = await spine.call("plain_reader", {}, via="schedule")
    assert r.status == "succeeded" and r.content.startswith("<untrusted_tool_result")
    w = await spine.call("scripted_write", {}, via="schedule")
    assert w.status == "succeeded"
