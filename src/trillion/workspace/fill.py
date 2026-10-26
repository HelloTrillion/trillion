"""The fill-in (the live widgets spec, D2, D3): fresh results into a widget's data.

One model call shaped like her naming a conversation (`brain/title.py`): no tools, so
nothing in the results can act; the fresh results reach it wrapped as untrusted, and
the answer only ever changes data. `merge` takes from the answer only the data fields
of the widget's type (an allowlist), keeps everything she decided (type, title, size,
source, asks, how often, the field that names it) from the widget as it was, and the
kit checks the result like any widget she writes."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from trillion.brain.types import Done, ModelProvider, Msg, SystemBlock, TextBlock, TextDelta, Usage
from trillion.spine.untrusted import wrap
from trillion.workspace.kit import BoardError, Widget
from trillion.workspace.schema import FIELDS
from trillion.workspace.widgets import validate_widget

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "Update this widget's data from the fresh results. Answer with the widget as JSON, "
    "same type and fields; keep anything the results don't change."
)
# A refresh is a nicety: a slow model must not hold the queue for long.
TIMEOUT_S = 20.0
# Room for the answer, scaled to the widget: a full table is long. A widget is answered
# whole, so the floor fits any ordinary card and the ceiling the largest the kit takes.
MIN_TOKENS, MAX_TOKENS = 6000, 16_000
# Characters past which an answer is no answer: three times the widget, at least this.
MIN_CHARS = 32_000
# What she decided, kept from the widget as it was whatever the answer says.
_KEPT = ("type", "title", "size", "asks", "refresh")
# The server's own, never taken from an answer.
_SERVERS = ("id", "live", "source")
# The field that names a widget (as `board.describe` names it to her) is hers too: a
# refreshed stat is still the stat she put up. A next_up's event is not kept: the next
# meeting is exactly what changes.
_NAMED = {
    "stat": "label",
    "goal": "label",
    "thread": "subject",
    "deal": "name",
    "contact": "name",
    "sprint": "name",
    # How a chart is drawn is a choice, not a number.
    "chart": "kind",
}


@dataclass(frozen=True, kw_only=True)
class Fresh:
    """One replayed read's result, and whether the router already wrapped it."""

    tool: str
    text: str
    wrapped: bool


def budget(widget: Mapping[str, Any]) -> tuple[int, int]:
    """(max tokens, max characters) for answering this widget."""
    size = len(json.dumps(as_written(widget), ensure_ascii=False))
    return min(MAX_TOKENS, max(MIN_TOKENS, size)), max(MIN_CHARS, 3 * size)


def as_written(widget: Mapping[str, Any]) -> dict[str, Any]:
    """The widget as she wrote it: without its id, its reads or when it was put up."""
    out = {k: copy.deepcopy(v) for k, v in widget.items() if k not in _SERVERS}
    source = widget.get("source")
    if isinstance(source, Mapping) and isinstance(source.get("name"), str):
        out["source"] = source["name"]
    return out


def _results(results: Sequence[Fresh]) -> str:
    # The router wraps what it returns unless the tool's output is trusted; that is
    # wrapped here, so every result arrives as data.
    parts = [r.text if r.wrapped else wrap(r.tool, r.text) for r in results]
    return "\n\n".join(parts) or "(no results)"


def parse(text: str, max_chars: int = MIN_CHARS) -> dict[str, Any] | None:
    """The JSON object in an answer, through code fences or words around it."""
    if len(text) > max_chars:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        found = json.loads(text[start : end + 1])
    except (ValueError, RecursionError):
        return None
    return found if isinstance(found, dict) else None


async def fill(
    provider: ModelProvider,
    widget: Mapping[str, Any],
    results: Sequence[Fresh],
    *,
    timeout_s: float = TIMEOUT_S,
) -> tuple[dict[str, Any] | None, Usage]:
    """The widget the model answers with, or None, and what the call used: every paid
    call is recorded (BRN-4), even one whose answer is not kept."""
    text: list[str] = []
    usage = Usage()
    max_tokens, max_chars = budget(widget)
    body = (
        "The widget:\n"
        + json.dumps(as_written(widget), ensure_ascii=False)
        + "\n\nFresh results:\n"
        + _results(results)
    )

    async def ask() -> None:
        nonlocal usage
        size = 0
        async for ev in provider.stream(
            system=[SystemBlock(text=INSTRUCTIONS, cache=False)],
            messages=[Msg(role="user", blocks=(TextBlock(text=body),))],
            tools=[],
            max_tokens=max_tokens,
        ):
            if isinstance(ev, TextDelta):
                size += len(ev.text)
                # Capped as it comes: past the cap it is no answer, but its usage counts.
                if size <= max_chars:
                    text.append(ev.text)
                else:
                    text.clear()
                    text.append(" " * (max_chars + 1))
            elif isinstance(ev, Done):
                usage = usage + ev.usage

    try:
        await asyncio.wait_for(ask(), timeout_s)
    except Exception as e:  # best effort: the old data stays
        logger.info("refresh fill-in failed: %s", type(e).__name__)
        return None, usage
    return parse("".join(text), max_chars), usage


def merge(old: Mapping[str, Any], answer: Mapping[str, Any]) -> Widget:
    """The refreshed widget, without an id or `live`: from the answer, only the data
    fields of the old widget's type; everything else from the widget as it was. Checked
    by the kit (`BoardError` if refused). An allowlist, so nothing she never chose (an
    `ask`, a new source, a field another type has) can come in with fresh numbers."""
    kind = old.get("type")
    fields = FIELDS.get(kind) if isinstance(kind, str) else None
    if fields is None:
        raise BoardError(f"a {kind!r} widget cannot be refreshed.")
    named = _NAMED.get(str(kind))
    raw: dict[str, Any] = {
        k: copy.deepcopy(answer[k]) for k in fields if k in answer and k != named
    }
    for key in (*_KEPT, *((named,) if named else ())):
        if key in old:
            raw[key] = copy.deepcopy(old[key])
    source = old.get("source")
    if isinstance(source, Mapping) and isinstance(source.get("name"), str):
        raw["source"] = source["name"]
    return validate_widget(raw, str(old.get("id", "widget")))
