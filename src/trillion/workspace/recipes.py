"""Recipes: a saved widget, as she wrote it, turned into the blocks the page draws (the
widget design system spec, "Recipes").

Pure, no I/O. The input is a widget `widgets.validate_widget` already took, so nothing
here refuses; each type has one function that picks the blocks and the emphasis
(the content is hers, the layout the system's). What a recipe writes is already in
the blocks' own form, so `blocks.validate_blocks` would leave it unchanged.

Times are shown as written, with no zone conversion: free-time slots are in the user's
local time (the kit refuses an offset), and a deadline is shown as its own date.
`expand_board` never fails a whole board for one widget.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from datetime import date
from typing import Any

from trillion.workspace.blocks import Block
from trillion.workspace.kit import VALUE, Widget, cap
from trillion.workspace.recipe_kit import is_date

logger = logging.getLogger(__name__)

Board = dict[str, Any]

# What the page gets of a widget besides its blocks; the recipe's own fields stay home.
KEPT = ("id", "type", "size", "title", "source", "asks")

_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_TONE = {"up": "good", "down": "bad"}
_DASH = "\u2013"  # an en dash, as a range of times is printed
# Written before the amount ("$4,200"); every other unit after it ("42 km").
_PREFIX = ("$", "€", "£")


def _with(out: Block, **fields: Any) -> Block:
    """The block with the fields that have a value."""
    out.update({k: v for k, v in fields.items() if v is not None})
    return out


# Words for times and numbers


def _day(text: str) -> str:
    """'2026-10-28…' → 'Wed 28 Oct'."""
    d = date.fromisoformat(text[:10])
    return f"{_DAYS[d.weekday()]} {d.day} {_MONTHS[d.month - 1]}"


def _clock(text: str) -> tuple[str, str]:
    """'…T14:05' → ('2:05', 'pm')."""
    hour, minute = int(text[11:13]), text[14:16]
    return f"{hour % 12 or 12}:{minute}", "am" if hour < 12 else "pm"


def _span(start: str, end: str) -> str:
    """9:00 to 10:30 am, 2:00 to 3:00 pm, 11:30 am to 12:15 pm, joined by an en dash:
    the half of the day said once when both ends share it, as the page's ranges are."""
    (a, a_half), (b, b_half) = _clock(start), _clock(end)
    if a_half != b_half:
        return f"{a} {a_half}{_DASH}{b} {b_half}"
    return f"{a}{_DASH}{b} {b_half}"


def _deadline(text: str) -> str:
    """An ISO date as 'Sat 31 Oct' (with ', 5:00 pm' when it has a time); words as written."""
    if not is_date(text):
        return text
    if "T" not in text:
        return _day(text)
    clock, half = _clock(text)
    return f"{_day(text)}, {clock} {half}"


def _by(text: str) -> str:
    """A goal's date as 'Nov 30'; words as written."""
    if not is_date(text):
        return text
    d = date.fromisoformat(text[:10])
    return f"{_MONTHS[d.month - 1]} {d.day}"


def _figure(n: float | int) -> str:
    """4200 → '4,200'; 42.5 → '42.5'; 1/3 → '0.33'."""
    if float(n).is_integer():
        return f"{int(n):,}"
    return f"{n:,.2f}".rstrip("0").rstrip(".")


def _amount(n: float | int, unit: str | None) -> str:
    """'$4,200', '-$5', '42 km', '12%'."""
    if not unit:
        return _figure(n)
    if unit in _PREFIX:
        sign = "-" if n < 0 else ""
        return f"{sign}{unit}{_figure(abs(n))}"
    return f"{_figure(n)}{'' if unit == '%' else ' '}{unit}"


def _total(scores: list[float | int]) -> float | int:
    # Rounded so 0.1 + 0.2 shows as 0.3.
    total = round(sum(scores), 6)
    return int(total) if float(total).is_integer() else total


# One function per type: the saved widget -> its blocks.


def _stat(w: Widget) -> list[Block]:
    tone = _TONE.get(w["tone"], w["tone"]) if "tone" in w else None
    value = {"type": "value", "value": w["value"], "label": w["label"]}
    return [_with(value, delta=w.get("delta"), hint=w.get("hint"), tone=tone)]


def _chart(w: Widget) -> list[Block]:
    chart = {"type": "chart", "kind": w["kind"], "x": w["x"], "series": w["series"]}
    return [_with(chart, unit=w.get("unit"))]


def _list(w: Widget) -> list[Block]:
    items = [
        _with(
            {"title": i["title"]}, detail=i.get("subtitle"), meta=i.get("meta"), done=i.get("done")
        )
        for i in w["items"]
    ]
    return [{"type": "rows", "items": items}]


def _table(w: Widget) -> list[Block]:
    return [{"type": "table", "columns": w["columns"], "rows": w["rows"]}]


def _note(w: Widget) -> list[Block]:
    return [{"type": "text", "text": w["text"]}]


def _timeline(w: Widget) -> list[Block]:
    return [{"type": "timeline", "events": w["events"]}]


def _compare(w: Widget) -> list[Block]:
    columns = []
    for option in w["options"]:
        picked = "Her pick" if option["name"] == w.get("pick") else None
        column = [_with({"type": "value", "value": cap(option["name"], VALUE)}, label=picked)]
        column += [
            {"type": "meter", "value": m["score"], "max": 10, "label": m["label"]}
            for m in option["metrics"]
        ]
        if option.get("tags"):
            column.append({"type": "chips", "items": option["tags"]})
        if "note" in option:
            column.append({"type": "text", "text": option["note"]})
        columns.append(column)
    out: list[Block] = [{"type": "columns", "columns": columns}]
    if "reason" in w:
        out.append({"type": "text", "text": w["reason"]})
    return out


def _next_up(w: Widget) -> list[Block]:
    out = [_with({"type": "time", "at": w["start"]}, end=w.get("end"), label=w["event"])]
    if "people" in w:
        out.append({"type": "people", "names": w["people"]})
    if "where" in w:
        out.append({"type": "chips", "items": [w["where"]]})
    if "note" in w:
        out.append({"type": "text", "text": w["note"]})
    return out


def _free_time(w: Widget) -> list[Block]:
    days: dict[str, list[dict[str, str]]] = {}
    for slot in sorted(w["slots"], key=lambda s: (s["start"][:10], s["start"][11:16])):
        days.setdefault(slot["start"][:10], []).append(slot)
    items = [
        {"title": _day(day), "detail": ", ".join(_span(s["start"], s["end"]) for s in slots)}
        for day, slots in days.items()
    ]
    return [{"type": "rows", "items": items}]


def _inbox(w: Widget) -> list[Block]:
    hint = None
    if "needs_reply" in w:
        n = w["needs_reply"]
        hint = "None need a reply" if n == 0 else f"{n} need{'s' if n == 1 else ''} a reply"
    out = [_with({"type": "value", "value": str(w["unread"]), "label": "Unread"}, hint=hint)]
    if w.get("threads"):
        items = [
            _with(
                {"title": t["from"], "detail": t["subject"], "person": t["from"]},
                status=t.get("flag"),
                when=t.get("when"),
            )
            for t in w["threads"]
        ]
        out.append({"type": "rows", "items": items})
    return out


def _thread(w: Widget) -> list[Block]:
    # The subject once: under the sender, unless the card's title already says it.
    subject = None if w.get("title") == w["subject"] else w["subject"]
    out: list[Block] = [
        _with({"type": "person", "name": w["from"]}, detail=subject),
        # Threads saved before `request` was its name kept it as `ask`.
        {"type": "text", "text": w.get("request", w.get("ask"))},
    ]
    facts = []
    if "due" in w:
        facts.append({"label": "Due", "value": _deadline(w["due"])})
    # When it was sent rides on the excerpt; with none, it is a fact of its own.
    if "when" in w and "excerpt" not in w:
        facts.append({"label": "Sent", "value": _deadline(w["when"])})
    if facts:
        out.append({"type": "facts", "items": facts})
    if "excerpt" in w:
        out.append(_with({"type": "quote", "text": w["excerpt"]}, when=w.get("when")))
    return out


def _mentions(w: Widget) -> list[Block]:
    return [
        {
            "type": "rows",
            "label": group["channel"],
            "items": [
                _with(
                    {"title": m["from"], "detail": m["text"], "person": m["from"]},
                    when=m.get("when"),
                )
                for m in group["items"]
            ],
        }
        for group in w["groups"]
    ]


def _files(w: Widget) -> list[Block]:
    items = [
        _with(
            {"title": f["name"]},
            detail=f"by {f['by']}" if "by" in f else None,
            when=f.get("edited"),
            chip=f.get("kind"),
        )
        for f in w["files"]
    ]
    return [{"type": "rows", "items": items}]


def _quote(w: Widget) -> list[Block]:
    return [_with({"type": "quote", "text": w["text"]}, by=w.get("by"), when=w.get("when"))]


def _plan(w: Widget) -> list[Block]:
    steps = w["steps"]
    done = sum(1 for s in steps if s.get("done"))
    progress = {"type": "meter", "value": done, "max": len(steps)}
    progress["label"] = f"{done} of {len(steps)} done"
    # Every step shows its box, ticked or not.
    items = [
        _with({"title": s["title"]}, detail=s.get("detail"), done=bool(s.get("done")))
        for s in steps
    ]
    return [progress, {"type": "rows", "items": items}]


def _decision(w: Widget) -> list[Block]:
    rows = [
        [option, *scores, _total(scores)]
        for option, scores in zip(w["options"], w["scores"], strict=True)
    ]
    table = {"type": "table", "columns": ["Option", *w["criteria"], "Total"], "rows": rows}
    if "pick" in w:
        table["highlight"] = w["options"].index(w["pick"])
    out: list[Block] = [table]
    if "reason" in w:
        out.append({"type": "text", "text": w["reason"]})
    return out


def _facts(w: Widget) -> list[Block]:
    return [{"type": "facts", "items": w["items"]}]


def _callout(w: Widget) -> list[Block]:
    # Always toned: the tinted edge is what makes it a callout.
    return [{"type": "text", "text": w["text"], "tone": w.get("tone", "neutral")}]


def _goal(w: Widget) -> list[Block]:
    unit = w.get("unit")
    hint = f"of {_amount(w['target'], unit)}"
    if "by" in w:
        hint += f" by {_by(w['by'])}"
    # The label once: under the ring, unless the card's title already says it.
    label = None if w.get("title") == w["label"] else w["label"]
    ring = _with({"type": "meter", "value": w["value"], "max": w["target"]}, label=label)
    ring["shape"] = "ring"
    value = {"type": "value", "value": cap(_amount(w["value"], unit), VALUE), "hint": hint}
    return [ring, value]


def _sources(w: Widget) -> list[Block]:
    items = [_with({"title": s["title"]}, detail=s.get("note"), meta=s["site"]) for s in w["items"]]
    return [{"type": "rows", "items": items}]


def _custom(w: Widget) -> list[Block]:
    return list(w["blocks"])


_RECIPES: dict[str, Callable[[Widget], list[Block]]] = {
    "stat": _stat,
    "chart": _chart,
    "list": _list,
    "table": _table,
    "note": _note,
    "timeline": _timeline,
    "compare": _compare,
    "next_up": _next_up,
    "free_time": _free_time,
    "inbox": _inbox,
    "thread": _thread,
    "mentions": _mentions,
    "files": _files,
    "quote": _quote,
    "plan": _plan,
    "decision": _decision,
    "facts": _facts,
    "callout": _callout,
    "goal": _goal,
    "sources": _sources,
    "custom": _custom,
}


# What a card is called when she gave it no title. Types whose first block already
# names them (a stat's label, a note's words, a chart) stay untitled.
_TITLES = {
    "next_up": "Next up",
    "free_time": "Free time",
    "inbox": "Inbox",
    "mentions": "Mentions",
    "files": "Files",
    "plan": "Plan",
    "decision": "Decision",
    "sources": "Sources",
    "facts": "Details",
}
# Or the widget's own field that names it.
_TITLED_BY = {"goal": "label", "thread": "subject"}


def title_of(widget: Widget) -> str | None:
    """The card's title: hers, else the type's default. Not saved: a later kit may
    choose better words, and the saved widget stays what she wrote."""
    if widget.get("title"):
        return str(widget["title"])
    field = _TITLED_BY.get(widget["type"])
    named = widget.get(field) if field else _TITLES.get(widget["type"])
    return named if isinstance(named, str) and named else None


def _titled(widget: Widget) -> Widget:
    title = title_of(widget)
    return widget if title is None or widget.get("title") == title else {**widget, "title": title}


def expand(widget: Widget) -> list[Block]:
    """The blocks a saved widget is drawn with, under the title it is shown with. A
    copy: the page's widget never shares a list with the saved board."""
    return copy.deepcopy(_RECIPES[widget["type"]](_titled(widget)))


def _safe(widget: Widget) -> list[Block]:
    """The widget's blocks, or none: a widget saved by an older kit, or a recipe bug,
    must not take the whole board down on every send. The card still shows its title."""
    try:
        return expand(widget)
    except Exception:
        logger.warning(
            "could not expand widget %s (%s)", widget.get("id"), widget.get("type"), exc_info=True
        )
        return []


def expand_board(board: Board) -> Board:
    """The board as the page gets it: each widget its name, size, source and asks, and
    its blocks. The saved board is left as it was."""
    widgets = []
    for w in board["widgets"]:
        shown = _titled(w) if w.get("type") in _RECIPES else w
        widgets.append({**{k: shown[k] for k in KEPT if k in shown}, "blocks": _safe(w)})
    return {**{k: v for k, v in board.items() if k != "widgets"}, "widgets": widgets}
