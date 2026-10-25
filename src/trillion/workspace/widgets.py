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

from trillion.workspace import pack_kit, recipe_kit
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

# Her recipes: the core ones, then the packs', with custom (the fallback) last.
_RECIPES = {
    **{k: v for k, v in recipe_kit.RECIPES.items() if k != "custom"},
    **pack_kit.PACK_RECIPES,
    "custom": recipe_kit.RECIPES["custom"],
}
_SIZE = {**recipe_kit.SIZE, **pack_kit.PACK_SIZE}
TYPES = (*BUILDERS, *_RECIPES)

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
    # The packs' widgets, by the names services and models use for them.
    "metrics": "kpis",
    "kpi_row": "kpis",
    "dashboard": "kpis",
    "todo": "tasks",
    "todos": "tasks",
    "to_do": "tasks",
    "task_list": "tasks",
    "payments": "transactions",
    "payment": "transactions",
    "charges": "transactions",
    "bills": "invoices",
    "invoice": "invoices",
    "spending": "breakdown",
    "categories": "breakdown",
    "payout": "payouts",
    "funnel": "pipeline",
    "deals": "pipeline",
    "opportunity": "deal",
    "person": "contact",
    "customer": "contact",
    "lead": "contact",
    "email_campaign": "campaign",
    "newsletter": "campaign",
    "tickets": "support_queue",
    "support": "support_queue",
    "queue": "support_queue",
    "ci": "checks",
    "builds": "checks",
    "build": "checks",
    "tests": "checks",
    "issues": "errors",
    "exceptions": "errors",
    "crashes": "errors",
    "deployments": "deploys",
    "deployment": "deploys",
    "releases": "deploys",
    "status_page": "services",
    "uptime": "services",
    "health": "services",
    "board": "kanban",
    "burndown": "sprint",
    "cycle": "sprint",
    "iteration": "sprint",
    "deadlines": "due_soon",
    "upcoming": "due_soon",
    "due": "due_soon",
}
# With no usable type, the fields say which widget it is, most telling first: a
# decision has options too, and a goal a value.
_BY_FIELD: tuple[tuple[tuple[str, ...], str], ...] = (
    (("blocks",), "custom"),
    (("series",), "chart"),
    (("criteria",), "decision"),
    (("options",), "compare"),
    (("slots",), "free_time"),
    (("steps",), "plan"),
    (("unread",), "inbox"),
    (("threads",), "inbox"),
    (("groups",), "mentions"),
    (("files",), "files"),
    (("event",), "next_up"),
    (("target",), "goal"),
    # The packs' fields come after every phase 1 field but the loosest, so what phase 1
    # read from the fields it still reads. A word models use for other things (done,
    # current) tells only beside its partner.
    (("metrics",), "kpis"),
    (("tasks",), "tasks"),
    (("current", "stages"), "deal"),
    (("stages",), "pipeline"),
    (("sent",), "campaign"),
    (("waiting",), "support_queue"),
    (("first_reply",), "support_queue"),
    (("open",), "support_queue"),
    (("issues",), "errors"),
    (("burndown",), "sprint"),
    (("done", "total"), "sprint"),
    (("activity",), "contact"),
    (("company",), "contact"),
    (("role",), "contact"),
    (("subject",), "thread"),
    (("events",), "timeline"),
    (("columns",), "table"),
    (("items",), "list"),
    (("value",), "stat"),
    (("text",), "note"),
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
    "kpis": "metrics",
    "tasks": "tasks",
    "transactions": "items",
    "invoices": "items",
    "breakdown": "items",
    "payouts": "items",
    "pipeline": "stages",
    "deal": "current",
    "contact": "name",
    "campaign": "sent",
    "support_queue": "open",
    "checks": "items",
    "errors": "issues",
    "deploys": "items",
    "services": "items",
    "kanban": "columns",
    "sprint": "total",
    "due_soon": "items",
    "custom": "blocks",
}
# A state word in an item says which kind of list it is.
_STATES = (
    ((*pack_kit.CHECKED, *pack_kit.CHECKED_WORDS), "checks"),
    ((*pack_kit.DEPLOYED, *pack_kit.DEPLOYED_WORDS), "deploys"),
    ((*pack_kit.SERVING, *pack_kit.SERVING_WORDS), "services"),
)


def _entries(raw: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    given = raw.get(key)
    return [e for e in given if isinstance(e, Mapping)] if isinstance(given, list) else []


def _by_items(raw: Mapping[str, Any]) -> str | None:
    """Items shaped like a pack's: money (an amount with who, due or when, and no title),
    or a state word a pack knows. Anything else stays a list, as before the packs."""
    items = _entries(raw, "items")
    money = [
        i for i in items if "amount" in i and "title" not in i and {"who", "due", "when"} & set(i)
    ]
    if money:
        if any("due" in i for i in money):
            return "invoices"
        return "transactions" if any("who" in i for i in money) else "payouts"
    for item in items:
        state = item.get("state")
        if isinstance(state, str):
            word = " ".join(state.lower().replace("_", " ").replace("-", " ").split())
            return next((kind for states, kind in _STATES if word in states), None)
    return None


def _by_field(raw: Mapping[str, Any]) -> str | None:
    kind = next((kind for fields, kind in _BY_FIELD if all(f in raw for f in fields)), None)
    if kind == "table" and _entries(raw, "columns"):
        return "kanban"  # a table's columns are names; a kanban's are objects
    if kind == "list":
        return _by_items(raw) or kind
    return kind


def _issues(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    """Issues with event counts are errors (Sentry); without, they are tasks (Linear)."""
    issues = _entries(raw, "issues")
    if not issues or any("events" in i for i in issues):
        return {**raw, "type": "errors"}
    rest = {k: v for k, v in raw.items() if k != "issues"}
    return {**rest, "type": "tasks", "tasks": raw["issues"]}


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
    if name == "funnel" and "sent" in raw:
        return {**raw, "type": "campaign"}
    if "issues" in raw and "tasks" not in raw and (name == "issues" or name not in _ALIASES):
        return _issues(raw)
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
        build = BUILDERS.get(kind) or _RECIPES.get(kind)
    if build is None:
        what = f"unknown type {kind!r}" if kind is not None else "no type"
        raise fail(named, f"{what}; use one of {', '.join(TYPES)}")
    where = f"widget {at} ({kind})" if isinstance(at, int) else f"{kind} {at}"
    size = one_of(raw, "size", where, SIZES, default=_SIZE.get(kind, "medium"))
    out: Widget = {"type": kind, "size": size}
    # Every widget may have a title; today's types check theirs among their fields.
    put(out, "title", text_field(raw, "title", where, TITLE, need=False))
    out.update(build(raw, where))
    put(out, "source", recipe_kit.source(raw, where))
    put(out, "asks", recipe_kit.asks(raw, where))
    return out
