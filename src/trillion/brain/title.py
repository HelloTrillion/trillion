"""She names a conversation from its first exchange, for the thread list (SUR-1).

The exchange reaches the model wrapped as untrusted text: it is what the user and
she said, never an instruction, and no tools are offered, so nothing in it can act.
A failure leaves the first-words title in place, and nothing is shown or spoken."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence

from trillion.brain.compact import transcript
from trillion.brain.types import (
    Done,
    ModelProvider,
    Msg,
    SystemBlock,
    TextBlock,
    TextDelta,
    Usage,
)
from trillion.spine.untrusted import wrap

TITLE_TOKENS = 24
# Naming is a nicety: a slow local model must not hold anything up.
TIMEOUT_S = 10.0
MAX_CHARS = 80
MAX_WORDS = 8
TITLE_TOOL = "conversation"

INSTRUCTIONS = (
    "Write a title for this conversation: 3 to 6 words, sentence case, no quotes, "
    "no ending punctuation. Answer with the title only."
)

# Straight and curly quotes, and backticks, around the whole title.
_QUOTES = "\"'`\u201c\u201d\u2018\u2019"
_LABEL = re.compile(r"^\s*title\s*:\s*", re.I)


def clean(text: str) -> str | None:
    """The model's answer as a title, or None when it is not one."""
    line = " ".join(text.split())
    line = _LABEL.sub("", line).strip().strip(_QUOTES).rstrip(".!?;:,").strip()
    if not line or len(line) > MAX_CHARS or len(line.split()) > MAX_WORDS:
        return None
    return line


async def name(
    provider: ModelProvider, messages: Sequence[Msg], *, timeout_s: float = TIMEOUT_S
) -> tuple[str | None, Usage]:
    """A title from the conversation's first exchange, or None, and what the call
    used: every paid call is recorded (BRN-4), even one whose answer is not kept."""
    text: list[str] = []
    usage = Usage()

    async def ask() -> None:
        nonlocal usage
        body = wrap(TITLE_TOOL, transcript(messages))
        async for ev in provider.stream(
            system=[SystemBlock(text=INSTRUCTIONS, cache=False)],
            messages=[Msg(role="user", blocks=(TextBlock(text=body),))],
            tools=[],
            max_tokens=TITLE_TOKENS,
        ):
            if isinstance(ev, TextDelta):
                text.append(ev.text)
            elif isinstance(ev, Done):
                usage = usage + ev.usage

    try:
        await asyncio.wait_for(ask(), timeout_s)
    except Exception:
        return None, usage
    return clean("".join(text)), usage
