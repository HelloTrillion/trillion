from __future__ import annotations

import pytest

from trillion.spine.contract import Risk, ToolDefinition
from trillion.spine.labels import reason, tool_label


def definition(name: str, description: str, category: str = "mcp") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        input_schema={"type": "object", "additionalProperties": False},
        risk=Risk.IRREVERSIBLE,
        category=category,
        timeout_s=5.0,
    )


def test_first_party_tools_have_a_friendly_name() -> None:
    assert tool_label("file_create", None) == "Create a file"
    assert tool_label("rule_keep", None) == "Keep a standing rule"
    assert tool_label("gmail_send", None) == "Send an email"


def test_a_connected_tool_is_named_by_its_descriptions_first_sentence() -> None:
    d = definition(
        "mcp_notes_add_page",
        "[notes, a connected MCP server] Add a page to a notebook. Returns its id.",
    )
    assert tool_label(d.name, d) == "Add a page to a notebook"


def test_a_long_first_sentence_is_cut() -> None:
    d = definition("mcp_x_y", "[x, a connected MCP server] " + "word " * 30)
    label = tool_label(d.name, d)
    assert len(label) <= 48 and label.endswith("…")


def test_otherwise_the_name_with_spaces() -> None:
    assert tool_label("mcp_x_do_thing", None) == "mcp x do thing"
    assert tool_label("plain_tool", definition("plain_tool", "", "test")) == "plain tool"


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
