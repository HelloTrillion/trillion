"""Compaction: when a conversation outgrows the model, summarize it from the front (BRN-5).

Three things must come through a compaction untouched, and none of them depends
on the summary being good:

- the identity block and her rules, which are the system block, not the history;
- the user's standing rules, which are in the system block too (`standing.py`);
- every pending action, which lives in the database and is listed again in the
  context of each new message (SPN-3).

The summary is the model's own notes on what came before. It was written from a
conversation that held tool results, so it reaches the model as untrusted text:
information about the past, never an instruction.

The cut only ever falls at the start of a turn, before something the user said or
tapped. A tool call and its result are never separated, and the message being
answered now is always kept whole.

Nobody waits for a summary. Once the history is past `COMPACT_AT`, it is written in
the background after her reply, and put in place at the next pause between turns
(`api/session.py`). Only a history past `ceiling`, too long to send as it is, is
summarized before the turn that would send it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from trillion.brain.types import (
    ContextBlock,
    Done,
    ModelProvider,
    Msg,
    SystemBlock,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)
from trillion.spine.untrusted import wrap

# Rough tokens in the history before the oldest turns are summarized, and how much
# of the newest is kept word for word. Chosen so the history, the system block and
# a reply fit a model with a 16k context, which many local models have.
COMPACT_AT = 12_000
KEEP = 3_000
SUMMARY_TOKENS = 1_500
# The summary is written after a turn, so a turn or two can be sent before it is in
# place. Past this many times `COMPACT_AT` a turn waits for it: a summary that keeps
# failing, or a burst of long tool results, must not grow the history without end.
CEILING_TIMES = 2
# A local model can be slow to write a page of notes in the background. A turn that
# has to wait for one waits no longer than `WAIT_S`, then goes ahead without it.
TIMEOUT_S = 90.0
WAIT_S = 20.0
_CHARS_PER_TOKEN = 4
_CLIP = 600
TURN_STARTS = ("user", "tap")
SUMMARY_TOOL = "conversation_summary"


class CompactionFailed(Exception):
    """The summary could not be written. The conversation carries on unsummarized."""


@dataclass(frozen=True, kw_only=True)
class Summary:
    text: str
    usage: Usage


def _size(m: Msg) -> int:
    chars = 0
    for b in m.blocks:
        if isinstance(b, TextBlock | ContextBlock):
            chars += len(b.text)
        elif isinstance(b, ToolUseBlock):
            chars += len(b.name) + len(json.dumps(dict(b.args), ensure_ascii=False))
        elif isinstance(b, ToolResultBlock):
            chars += len(b.content)
    return chars // _CHARS_PER_TOKEN + 4


def estimate(messages: Sequence[Msg]) -> int:
    """Tokens, roughly. Generous on purpose: running out is worse than compacting early."""
    return sum(_size(m) for m in messages)


def ceiling(compact_at: int) -> int:
    """Past this, the history is summarized before the turn rather than after it."""
    return compact_at * CEILING_TIMES


def cut_point(kinds: Sequence[str], messages: Sequence[Msg], keep: int = KEEP) -> int | None:
    """Where the kept part begins, or None if there is nothing that can be summarized.

    The earliest turn start whose tail fits in `keep`. If even the newest turn is
    bigger than that, the newest turn is kept whole and everything before it goes."""
    starts = [i for i, k in enumerate(kinds) if k in TURN_STARTS and i > 0]
    if not starts:
        return None
    for i in starts:
        if estimate(messages[i:]) <= keep:
            return i
    return starts[-1]


def _clip(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= _CLIP else line[: _CLIP - 1] + "…"


def transcript(messages: Sequence[Msg]) -> str:
    """The messages as plain lines for the summarizer. The context each message was
    sent with is left out: it is the clock, cards and memories, all kept elsewhere."""
    lines: list[str] = []
    for m in messages:
        for b in m.blocks:
            if isinstance(b, TextBlock) and b.text.strip():
                who = "User" if m.role == "user" else "Assistant"
                lines.append(f"{who}: {_clip(b.text)}")
            elif isinstance(b, ToolUseBlock):
                args = json.dumps(dict(b.args), ensure_ascii=False)
                lines.append(f"Assistant called {b.name} with {_clip(args)}")
            elif isinstance(b, ToolResultBlock):
                lines.append(f"Tool result: {_clip(b.content)}")
    return "\n".join(lines)


async def summarize(
    provider: ModelProvider,
    instructions: str,
    previous: str | None,
    messages: Sequence[Msg],
) -> Summary:
    """Ask the model for notes on `messages`, folding in the summary before them.

    No tools are offered, so nothing in the old conversation can make this call act."""
    before = f"Notes on the conversation before this part:\n{previous}\n\n" if previous else ""
    body = f"{before}The part to summarize:\n{transcript(messages)}"
    text: list[str] = []
    usage = Usage()
    async for ev in provider.stream(
        system=[SystemBlock(text=instructions, cache=False)],
        messages=[Msg(role="user", blocks=(TextBlock(text=body),))],
        tools=[],
        max_tokens=SUMMARY_TOKENS,
    ):
        if isinstance(ev, TextDelta):
            text.append(ev.text)
        elif isinstance(ev, Done):
            usage = usage + ev.usage
    said = "".join(text).strip()
    if not said:
        raise CompactionFailed("empty")
    return Summary(text=said, usage=usage)


def with_summary(summary: str | None, messages: Sequence[Msg]) -> list[Msg]:
    """What the model is sent: the summary rides at the front of the first kept
    message, which always starts a turn, so roles still alternate."""
    if not summary or not messages:
        return list(messages)
    first = messages[0]
    note = ContextBlock(text=wrap(SUMMARY_TOOL, summary))
    return [Msg(role=first.role, blocks=(note, *first.blocks), raw=first.raw), *messages[1:]]
