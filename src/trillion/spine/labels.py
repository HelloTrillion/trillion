"""What an approval card calls a tool and why it needs the user (SPN-2a).

Both come from the host: a table for tools whose names are fixed, the registered
description for a connected tool, and the pending action's own risk. Nothing the
model writes reaches either."""

from __future__ import annotations

import re
from typing import Literal

from trillion.spine.contract import Risk, ToolDefinition

Reason = Literal["irreversible", "spend", "write", "read"]

MAX_LABEL = 48

LABELS: dict[str, str] = {
    "file_create": "Create a file",
    "file_overwrite": "Replace a file",
    "file_append": "Add to a file",
    "file_edit": "Edit a file",
    "rule_keep": "Keep a standing rule",
    "rule_drop": "Drop a standing rule",
    "ask_schedule": "Schedule a timed ask",
    "reminder_set": "Set a reminder",
    "schedule_cancel": "Cancel a schedule",
    "memory_forget": "Forget a memory",
    # Google and Slack tools ask unless the user let them run on their own.
    "gmail_search": "Search your mail",
    "gmail_read": "Read an email",
    "gmail_draft": "Save a Gmail draft",
    "gmail_send": "Send an email",
    "calendar_events": "See your calendar",
    "calendar_busy": "Find free time",
    "calendar_create": "Add a calendar event",
    "calendar_update": "Change a calendar event",
    "calendar_delete": "Delete a calendar event",
    "drive_search": "Search your Drive files",
    "drive_read": "Read a Drive file",
    "slack_search": "Search Slack",
    "slack_conversations": "List Slack conversations",
    "slack_read": "Read a Slack conversation",
    "slack_post": "Post to Slack",
}

# What an MCP tool's description starts with (connectors/mcp/tool.py): the server's
# name, which the card already shows in the summary.
_SERVER_PREFIX = re.compile(r"^\[[^\]]*\]\s*")
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")


def tool_label(name: str, definition: ToolDefinition | None) -> str:
    """A short, plain name for the tool."""
    if name in LABELS:
        return LABELS[name]
    text = _SERVER_PREFIX.sub("", definition.description).strip() if definition else ""
    end = _SENTENCE_END.search(text)
    first = " ".join((text[: end.start()] if end else text).split())
    if not first:
        return name.replace("_", " ")
    return first if len(first) <= MAX_LABEL else first[: MAX_LABEL - 1].rstrip() + "…"


def reason(risk: Risk, recoverable: bool) -> Reason:
    """Why it needs the user. "irreversible" exactly when a spoken yes is not enough
    for it besides spend (SPN-14), so the card's words and its tap rule agree: a gated
    action that can be put right, like a new file, is a change, not a loss."""
    if risk is Risk.SPEND:
        return "spend"
    if not recoverable:
        return "irreversible"
    return "read" if risk is Risk.READ else "write"
