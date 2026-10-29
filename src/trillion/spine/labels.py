"""What an approval card calls a tool and why it needs the user (SPN-2a).

Both come from the host: a table for tools whose names are fixed, the user's own
name for a connected server, and the pending action's own risk. Nothing the model
or a server writes about itself reaches either."""

from __future__ import annotations

import re
from typing import Literal

from trillion.spine.contract import Risk, ToolDefinition

Reason = Literal["irreversible", "spend", "write", "read"]

MAX_LABEL = 48
_NOT_NAME = re.compile(r"[^A-Za-z0-9]+")

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
    "routine_widen": "Add to a routine",
    "speak_up_widen": "Speak up about more",
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
    "stripe_revenue": "Add up Stripe revenue",
}


def connected(definition: ToolDefinition | None, tool: object | None) -> tuple[str, str] | None:
    """A connected MCP tool's server, as the user named it in their config, and the
    tool's own name there; None for any other tool."""
    if definition is None or definition.category != "mcp":
        return None
    server, remote = getattr(tool, "server", None), getattr(tool, "remote_name", None)
    if isinstance(server, str) and isinstance(remote, str):
        return server, remote
    return None


def tool_label(name: str, mcp: tuple[str, str] | None = None) -> str:
    """A short, plain name for the tool. Never from a description: a connected server
    writes its own, and could call a delete tool a harmless lookup."""
    if name in LABELS:
        return LABELS[name]
    if mcp is None:
        return name.replace("_", " ")
    server, remote = mcp
    # The tool's name is the server's: kept to the letters and digits tool_name()
    # allows, so it cannot read as a sentence ("Harmless lookup. Changes nothing").
    tool = _NOT_NAME.sub(" ", remote).strip().lower() or name.replace("_", " ")
    label = " ".join(f"{server}: {tool}".split())
    return label if len(label) <= MAX_LABEL else label[: MAX_LABEL - 1].rstrip() + "…"


def reason(risk: Risk, recoverable: bool) -> Reason:
    """Why it needs the user. "irreversible" exactly when a spoken yes is not enough
    for it besides spend (SPN-14), so the card's words and its tap rule agree: a gated
    action that can be put right, like a new file, is a change, not a loss."""
    if risk is Risk.SPEND:
        return "spend"
    if not recoverable:
        return "irreversible"
    return "read" if risk is Risk.READ else "write"
