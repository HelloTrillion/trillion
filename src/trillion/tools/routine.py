"""A routine's own tools (the routines spec).

`routine_nothing` ends a routine's run with nothing to tell the user (AGT-6): no
notice, her reply not kept. The run sees it called; the tool itself changes nothing.
It is offered only to a routine's run, and refused anywhere else.
"""

from __future__ import annotations

from trillion.spine.contract import JSON, BaseTool, Risk, ToolContext, ToolDefinition, ToolResult
from trillion.spine.registry import ROUTINE_NOTHING

CATEGORY = "routine"
NOTHING = ROUTINE_NOTHING
_ONLY_ROUTINES = "Only a routine's run can end with nothing to say."


def for_session(kind: str, conversation_id: str) -> frozenset[str]:
    """Which routine tools a page's turn in this conversation is offered: none yet.
    `routine_nothing` never is; it belongs to a routine's run alone."""
    return frozenset()


class RoutineNothing(BaseTool):
    definition = ToolDefinition(
        name=NOTHING,
        description=(
            "End this routine's run with nothing to tell the user: nothing worth their "
            "attention was found. Then say nothing. No arguments."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk=Risk.READ,
        category=CATEGORY,
        timeout_s=5.0,
        # Her own words back, quoting nothing she read.
        trusted_output=True,
    )

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        if ctx.via != "routine":
            return ToolResult(ok=False, content=_ONLY_ROUTINES, effect="none", error_kind="invalid")
        return ToolResult(ok=True, content="Nothing to tell the user. Say nothing.", effect="none")
