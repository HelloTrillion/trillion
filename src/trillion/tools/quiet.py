"""`stay_quiet`: she says nothing this turn (VOI-7).

The tool itself changes nothing. The loop ends the turn on it, when it was called
alone and accepted, and the session does the rest: after `listening` it keeps the
microphone open for the user to go on, after `done` it rests. Its result is saved
with the call, so later turns see that she chose silence.
"""

from __future__ import annotations

from typing import Final

from trillion.spine.contract import JSON, BaseTool, Risk, ToolContext, ToolDefinition, ToolResult

QUIET: Final = "stay_quiet"
# Its own category, so a run nobody is watching is never offered it (`UNATTENDED_NEVER`)
# and the loop knows it by more than its name.
CATEGORY: Final = "quiet"
KINDS: Final = ("listening", "done")

_HOST_TURN = "This turn answers a note from the app, not the user: answer it."
_ASKED = "They asked a question: answer it, briefly."
_TYPED = "They typed it: there is no pause to wait through, so answer it."
_SAID = {
    "listening": "You said nothing. They can go on.",
    "done": "You said nothing. They signed off.",
}


def _refused(text: str) -> ToolResult:
    return ToolResult(ok=False, content=text, effect="none", error_kind="refused")


class StayQuiet(BaseTool):
    definition = ToolDefinition(
        name=QUIET,
        description=(
            "Say nothing this turn. listening: they stopped in an unfinished sentence,"
            " still thinking aloud (spoken only). A finished sentence, even a vague one"
            ' like "I just don\'t know", gets an answer. done: they signed off, such as'
            ' "okay, thanks" or "bye", or asked for a moment, such as "let me come back'
            ' to you". Never after a question: a tag question like "you know what I'
            ' mean?" or "right?" gets a short reply. Call it alone, with no text.'
        ),
        input_schema={
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": list(KINDS)}},
            "required": ["kind"],
            "additionalProperties": False,
        },
        risk=Risk.READ,
        category=CATEGORY,
        timeout_s=1.0,
        # The host's own words back, quoting nothing she read.
        trusted_output=True,
    )

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        kind = str(args.get("kind", ""))
        if kind not in KINDS:
            return _refused("kind must be listening or done")
        if ctx.host_turn:
            return _refused(_HOST_TURN)
        if ctx.asked:
            return _refused(_ASKED)
        if kind == "listening" and ctx.via != "voice":
            return _refused(_TYPED)
        return ToolResult(ok=True, content=_SAID[kind], effect="none")
