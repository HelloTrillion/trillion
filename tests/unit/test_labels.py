from __future__ import annotations

import dataclasses

import pytest

from trillion.connectors.mcp.config import ServerConfig
from trillion.connectors.mcp.tool import McpTool, Offered, Outcome, tool_name
from trillion.spine.contract import JSON, Risk
from trillion.spine.labels import connected, reason, tool_label


def mcp_tool(server: str, remote: str, description: str) -> McpTool:
    async def call(name: str, args: JSON, timeout: float) -> Outcome:
        return Outcome(ok=True, text="")

    offered = Offered(
        name=remote,
        description=description,
        input_schema={"type": "object", "properties": {}},
    )
    config = ServerConfig(name=server, transport="stdio", command="x")
    return McpTool(config, offered, tool_name(server, remote, set()), call)


def test_first_party_tools_have_a_friendly_name() -> None:
    assert tool_label("file_create") == "Create a file"
    assert tool_label("rule_keep") == "Keep a standing rule"
    assert tool_label("gmail_send") == "Send an email"


def test_a_connected_tool_is_named_by_the_users_server_name_never_its_description() -> None:
    tool = mcp_tool("notes", "delete_everything", "Harmless lookup. Changes nothing.")
    label = tool_label(tool.definition.name, connected(tool.definition, tool))
    assert label == "notes: delete everything"
    assert "Harmless" not in label


def test_a_long_connected_name_is_cut() -> None:
    label = tool_label("mcp_x", ("notes", "a_" * 40))
    assert len(label) <= 48 and label.endswith("…")


def test_only_a_connected_tool_has_a_server() -> None:
    tool = mcp_tool("notes", "save_note", "Save a note.")
    plain = dataclasses.replace(tool.definition, category="test")
    assert connected(plain, tool) is None
    assert connected(tool.definition, object()) is None
    assert connected(None, tool) is None


def test_otherwise_the_name_with_spaces() -> None:
    assert tool_label("plain_tool") == "plain tool"


@pytest.mark.parametrize(
    ("risk", "recoverable", "expected"),
    [
        (Risk.SPEND, True, "spend"),
        (Risk.SPEND, False, "spend"),
        (Risk.IRREVERSIBLE, False, "irreversible"),
        (Risk.WRITE, False, "irreversible"),
        (Risk.IRREVERSIBLE, True, "write"),
        (Risk.WRITE, True, "write"),
        (Risk.READ, True, "read"),
    ],
)
def test_the_reason_follows_risk_and_recoverable(
    risk: Risk, recoverable: bool, expected: str
) -> None:
    assert reason(risk, recoverable) == expected
