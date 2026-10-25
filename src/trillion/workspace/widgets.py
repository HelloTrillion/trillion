"""A widget as she wrote it, checked: the one way in for every type (D1, D5).

Pure, no I/O. The widget's type is read leniently (a near miss means what it plainly
means), its strings lose their emoji, and then its type's own check runs: today's
seven in `kit.py`, the recipes built on blocks in `recipe_kit.py`. Every widget may
also carry a title, a size, a source and asks. The first problem raises `BoardError`
naming the widget, the field and the limit.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from trillion.workspace import recipe_kit
from trillion.workspace.kit import (
    BUILDERS,
    KINDS,
    SIZES,
    TITLE,
    Widget,
    as_object,
    fail,
    one_of,
    put,
    text_field,
)
from trillion.workspace.text import scrub

TYPES = (*BUILDERS, *recipe_kit.RECIPES)

# Names a model reaches for instead of the kit's own: a chart kind given as the type,
# or a near synonym. Small local models do this often, and the meaning is plain.
_ALIASES = {
    "pie": "chart",
    "graph": "chart",
    "metric": "stat",
    "kpi": "stat",
    "number": "stat",
    "markdown": "note",
    "paragraph": "note",
    "checklist": "plan",
    "steps": "plan",
    "bullets": "list",
    "grid": "table",
    "agenda": "timeline",
    "schedule": "timeline",
    "comparison": "compare",
    "next": "next_up",
    "next_meeting": "next_up",
    "inbox_summary": "inbox",
    "email": "inbox",
    "alert": "callout",
    "banner": "callout",
    "progress": "goal",
}
# With no usable type, the fields say which widget it is, most telling first: a
# decision has options too, and a goal a value.
_BY_FIELD = (
    ("blocks", "custom"),
    ("series", "chart"),
    ("criteria", "decision"),
    ("options", "compare"),
    ("slots", "free_time"),
    ("steps", "plan"),
    ("unread", "inbox"),
    ("threads", "inbox"),
    ("groups", "mentions"),
    ("files", "files"),
    ("event", "next_up"),
    ("target", "goal"),
    ("subject", "thread"),
    ("events", "timeline"),
    ("columns", "table"),
    ("items", "list"),
    ("value", "stat"),
    ("text", "note"),
)
# The field each type cannot do without. A near synonym whose type lacks it but has
# another type's field means that type: a "checklist" of items is a list.
_KEY = {
    "stat": "value",
    "chart": "series",
    "list": "items",
    "table": "columns",
    "note": "text",
    "timeline": "events",
    "compare": "options",
    "next_up": "event",
    "free_time": "slots",
    "inbox": "unread",
    "thread": "subject",
    "mentions": "groups",
    "files": "files",
    "quote": "text",
    "plan": "steps",
    "decision": "criteria",
    "facts": "items",
    "callout": "text",
    "goal": "target",
    "sources": "items",
    "custom": "blocks",
}


def _by_field(raw: Mapping[str, Any]) -> str | None:
    return next((kind for field, kind in _BY_FIELD if field in raw), None)


def _typed(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    """The widget with the type it means: a chart kind as its type ("line") becomes a
    chart of that kind, a near synonym becomes the kit's name, and a missing or unknown
    type is read from the fields. What cannot be read is left for the refusal."""
    given = raw.get("type")
    # "Next up" and "next-up" mean next_up.
    name = re.sub(r"[\s-]+", "_", given.strip().lower()) if isinstance(given, str) else ""
    if name in TYPES:
        return raw if name == given else {**raw, "type": name}
    if name in KINDS or name == "pie":
        kind = "donut" if name == "pie" else name
        return {**raw, "type": "chart", "kind": raw.get("kind") or kind}
    if name == "text" and "value" in raw and "text" not in raw:
        return {**raw, "type": "stat"}
    if name in _ALIASES:
        meant = _ALIASES[name]
        if _KEY[meant] not in raw:
            meant = _by_field(raw) or meant
        return {**raw, "type": meant}
    if name == "text":
        return {**raw, "type": "note"}
    guess = _by_field(raw)
    return {**raw, "type": guess} if guess else raw


def validate_widget(raw: Any, at: int | str) -> Widget:
    """The widget as it is saved, without an id. `at` is the position in the call
    (1-based) for a new widget, or the id of the widget it replaces."""
    named = f"widget {at}" if isinstance(at, int) else at
    raw = recipe_kit.asked(_typed(scrub(as_object(raw, named))))
    kind = raw.get("type")
    build = None
    if isinstance(kind, str):
        build = BUILDERS.get(kind) or recipe_kit.RECIPES.get(kind)
    if build is None:
        what = f"unknown type {kind!r}" if kind is not None else "no type"
        raise fail(named, f"{what}; use one of {', '.join(TYPES)}")
    where = f"widget {at} ({kind})" if isinstance(at, int) else f"{kind} {at}"
    size = one_of(raw, "size", where, SIZES, default=recipe_kit.SIZE.get(kind, "medium"))
    out: Widget = {"type": kind, "size": size}
    # Every widget may have a title; today's types check theirs among their fields.
    put(out, "title", text_field(raw, "title", where, TITLE, need=False))
    out.update(build(raw, where))
    put(out, "source", recipe_kit.source(raw, where))
    put(out, "asks", recipe_kit.asks(raw, where))
    return out
