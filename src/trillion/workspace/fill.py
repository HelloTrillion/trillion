"""The fill-in (the live widgets spec, D2, D3): fresh results into a widget's data.

One model call shaped like her naming a conversation (`brain/title.py`): no tools, so
nothing in the results can act; the fresh results reach it wrapped as untrusted, and
the answer only ever changes data. `merge` keeps what she decided (type, title, size,
source, asks, how often) from the widget as it was, and the kit checks the rest like
any widget she writes."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from trillion.brain.types import Done, ModelProvider, Msg, SystemBlock, TextBlock, TextDelta, Usage
from trillion.spine.untrusted import wrap
from trillion.workspace.kit import Widget
from trillion.workspace.widgets import validate_widget

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "Update this widget's data from the fresh results. Answer with the widget as JSON, "
    "same type and fields; keep anything the results don't change."
)
# A refresh is a nicety: a slow model must not hold the queue for long.
TIMEOUT_S = 20.0
MAX_TOKENS = 2000
# Longer than any widget the kit takes: anything past it is not an answer.
MAX_CHARS = 20_000
_WRAPPED = "<untrusted_tool_result"
# What she decided, kept from the widget as it was whatever the answer says.
_KEPT = ("type", "title", "size", "asks", "refresh")
# The server's own, never taken from an answer.
_SERVERS = ("id", "live", "source")


def as_written(widget: Mapping[str, Any]) -> dict[str, Any]:
    """The widget as she wrote it: without its id, its reads or when it was put up."""
    out = {k: copy.deepcopy(v) for k, v in widget.items() if k not in _SERVERS}
    source = widget.get("source")
    if isinstance(source, Mapping) and isinstance(source.get("name"), str):
        out["source"] = source["name"]
    return out


def _results(results: Sequence[tuple[str, str]]) -> str:
    # The router wraps what it returns unless a tool is first-party and pure; anything
    # it did not wrap is wrapped here, so every result arrives as data.
    parts = [text if text.startswith(_WRAPPED) else wrap(tool, text) for tool, text in results]
    return "\n\n".join(parts) or "(no results)"


def parse(text: str) -> dict[str, Any] | None:
    """The JSON object in an answer, through code fences or words around it."""
    if len(text) > MAX_CHARS:
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
    results: Sequence[tuple[str, str]],
    *,
    timeout_s: float = TIMEOUT_S,
) -> tuple[dict[str, Any] | None, Usage]:
    """The widget the model answers with, or None, and what the call used: every paid
    call is recorded (BRN-4), even one whose answer is not kept."""
    text: list[str] = []
    usage = Usage()
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
            max_tokens=MAX_TOKENS,
        ):
            if isinstance(ev, TextDelta):
                size += len(ev.text)
                # Capped as it comes: past the cap it is no answer, but its usage counts.
                if size <= MAX_CHARS:
                    text.append(ev.text)
                else:
                    text.clear()
                    text.append(" " * (MAX_CHARS + 1))
            elif isinstance(ev, Done):
                usage = usage + ev.usage

    try:
        await asyncio.wait_for(ask(), timeout_s)
    except Exception as e:  # best effort: the old data stays
        logger.info("refresh fill-in failed: %s", type(e).__name__)
        return None, usage
    return parse("".join(text)), usage


def merge(old: Mapping[str, Any], answer: Mapping[str, Any]) -> Widget:
    """The refreshed widget, without an id or `live`: the data from the answer, what she
    decided from the widget as it was, checked by the kit (`BoardError` if refused)."""
    raw: dict[str, Any] = {k: v for k, v in answer.items() if k not in (*_KEPT, *_SERVERS)}
    for key in _KEPT:
        if key in old:
            raw[key] = copy.deepcopy(old[key])
    source = old.get("source")
    if isinstance(source, Mapping) and isinstance(source.get("name"), str):
        raw["source"] = source["name"]
    return validate_widget(raw, str(old.get("id", "widget")))
