"""Her board (SUR-1): show widgets in the workspace, change them, take them off, close it.

Showing changes nothing in the world, so these run at once and never ask (D4). They
are never offered to a run nobody is watching, nor to a channel: with no page there,
a board would be invisible. The board belongs to the conversation the call is in.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from trillion.spine.contract import JSON, BaseTool, Risk, ToolContext, ToolDefinition, ToolResult
from trillion.workspace.board import BoardError, describe
from trillion.workspace.kit import MAX_WIDGETS
from trillion.workspace.packs import told
from trillion.workspace.schema import type_cards, widget_schema
from trillion.workspace.service import Boards, BoardSaveError

SHOW, UPDATE, REMOVE, CLOSE = (
    "workspace_show",
    "workspace_update",
    "workspace_remove",
    "workspace_close",
)
CATEGORY = "workspace"
_TIMEOUT = 5.0
_NO_BOARD = "the board is not available here: there is no page open to show it on."


def _refused(message: str, kind: str = "invalid") -> ToolResult:
    # Said plainly first: a model that skims a refusal must not tell the user it worked.
    if kind == "invalid":
        message = f"Nothing was shown or changed. {message} Fix the call and try again."
    return ToolResult(ok=False, content=message, effect="none", error_kind=kind)


def _named(widgets: list[dict[str, Any]]) -> str:
    return ", ".join(describe(w) for w in widgets)


def _definition(
    name: str, description: str, properties: JSON, required: list[str]
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        risk=Risk.READ,
        category=CATEGORY,
        timeout_s=_TIMEOUT,
        # Wrapped as untrusted like any result: it quotes titles and labels she wrote,
        # which may come from a page she read (SPN-5).
    )


def definitions(types: Collection[str]) -> dict[str, ToolDefinition]:
    """The four tools' definitions, telling her about the widget `types` (see
    `workspace.packs.told`). Only what she is told changes; the kit takes every type."""
    widget = widget_schema(types)
    return {
        SHOW: _definition(
            SHOW,
            "Show something on your board in the workspace, beside the conversation: numbers, "
            "charts, comparisons, plans, anything with more than three items. Opens the board. "
            "mode 'replace' (default) clears what was there; 'add' appends, "
            f"{MAX_WIDGETS} widgets at most. No widgets reopens the board as it was. Say one "
            "short line about it; never read the board aloud.\n" + type_cards(types),
            {
                "title": {"type": "string", "description": "The board's title."},
                "widgets": {"type": "array", "items": widget},
                "mode": {"type": "string", "enum": ["replace", "add"]},
            },
            ["widgets"],
        ),
        UPDATE: _definition(
            UPDATE,
            "Change one widget on your board in place, keeping its id and position: give the "
            "whole widget as it should now be, for example the same chart as a bar chart. "
            "Its fields are as for workspace_show.",
            {
                "id": {"type": "string", "description": "The widget's id, e.g. 'w2'."},
                "widget": widget,
            },
            ["id", "widget"],
        ),
        REMOVE: _definition(
            REMOVE,
            'Take widgets off your board by id; ["all"] takes everything off. Taking the last '
            "one off closes the board.",
            {
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": (
                        'Widget ids such as ["w1", "w3"]; ["all"] takes everything off.'
                    ),
                }
            },
            ["ids"],
        ),
        CLOSE: _definition(
            CLOSE,
            "Close your board and keep it. workspace_show with no widgets opens it again.",
            {},
            [],
        ),
    }


# What she is told until her connectors say otherwise: the core widgets only.
_TOLD = definitions(told([]))


class _BoardTool(BaseTool):
    def __init__(self) -> None:
        self._boards: Boards | None = None
        self._owner = ""

    def bind(self, boards: Boards, owner_id: str) -> None:
        """Called once at startup with the running process's boards."""
        self._boards, self._owner = boards, owner_id

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        boards = self._boards
        if boards is None or ctx.owner_id != self._owner:
            return _refused(_NO_BOARD)
        try:
            # Sessions use the conversation's id as their own.
            return await self._run(boards, ctx.session_id, args)
        except BoardSaveError as e:
            return _refused(str(e), "save_failed")
        except BoardError as e:
            return _refused(str(e))

    async def _run(self, boards: Boards, conversation_id: str, args: JSON) -> ToolResult:
        raise NotImplementedError


class WorkspaceShow(_BoardTool):
    definition = _TOLD[SHOW]

    async def _run(self, boards: Boards, conversation_id: str, args: JSON) -> ToolResult:
        raw = args.get("widgets", [])
        title = args.get("title")
        board = await boards.show(conversation_id, title, raw, args.get("mode", "replace"))
        if not raw:
            text = f"The board is open again: {_named(board['widgets'])}."
        else:
            text = f"Shown on the board: {_named(board['widgets'][-len(raw) :])}."
        return ToolResult(ok=True, content=text, effect="confirmed")


class WorkspaceUpdate(_BoardTool):
    definition = _TOLD[UPDATE]

    async def _run(self, boards: Boards, conversation_id: str, args: JSON) -> ToolResult:
        widget_id = str(args.get("id", ""))
        board = await boards.update(conversation_id, widget_id, args.get("widget"))
        (changed,) = [w for w in board["widgets"] if w["id"] == widget_id]
        return ToolResult(
            ok=True, content=f"Updated on the board: {_named([changed])}.", effect="confirmed"
        )


class WorkspaceRemove(_BoardTool):
    definition = _TOLD[REMOVE]

    async def _run(self, boards: Boards, conversation_id: str, args: JSON) -> ToolResult:
        ids: Any = args.get("ids")
        if ids == ["all"]:
            ids = "all"
        board, gone = await boards.remove(conversation_id, ids)
        text = f"Taken off the board: {_named(gone)}."
        if not board["widgets"]:
            text += " The board is empty and closed."
        return ToolResult(ok=True, content=text, effect="confirmed")


class WorkspaceClose(_BoardTool):
    definition = _TOLD[CLOSE]

    async def _run(self, boards: Boards, conversation_id: str, args: JSON) -> ToolResult:
        await boards.close(conversation_id)
        return ToolResult(
            ok=True,
            content="Closed the board. It is kept: workspace_show with no widgets reopens it.",
            effect="confirmed",
        )
