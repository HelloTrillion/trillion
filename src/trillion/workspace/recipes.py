"""Recipes: a saved widget, as she wrote it, turned into the blocks the page draws (the
widget design system spec, "Recipes").

Pure, no I/O. The input is a widget `kit.validate_widget` already took, so nothing
here refuses; each type has one function that picks the blocks and the emphasis
(the content is hers, the layout the system's). What a recipe writes is already in
the blocks' own form, so `blocks.validate_blocks` would leave it unchanged.

Times are shown as written: a free-time slot at 09:00 is "9:00" whatever its offset,
because the user's clock is the one she read them from.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import date
from typing import Any

from trillion.workspace.blocks import Block
from trillion.workspace.kit import VALUE, Widget, cap

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
    """9:00 to 10:30, 2:00 to 3:00 pm, 11:30 am to 12:15 pm, joined by an en dash. A
    morning goes without "am", as people write free time; the afternoon says so."""
    (a, a_half), (b, b_half) = _clock(start), _clock(end)
    if a_half != b_half:
        return f"{a} {a_half}{_DASH}{b} {b_half}"
    return f"{a}{_DASH}{b}" + (" pm" if b_half == "pm" else "")


def _deadline(text: str) -> str:
    """An ISO date as 'Sat 31 Oct' (with ', 5:00 pm' when it has a time); words as written."""
    if not text[:1].isdigit():
        return text
    if "T" not in text:
        return _day(text)
    clock, half = _clock(text)
    return f"{_day(text)}, {clock} {half}"


def _by(text: str) -> str:
    """A goal's date as 'Nov 30'; words as written."""
    if not text[:1].isdigit():
        return text
    d = date.fromisoformat(text[:10])
    return f"{_MONTHS[d.month - 1]} {d.day}"


def _figure(n: float | int) -> str:
    """4200 → '4,200'; 42.5 → '42.5'; 1/3 → '0.33'."""
    if float(n).is_integer():
        return f"{int(n):,}"
    return f"{n:,.2f}".rstrip("0").rstrip(".")


def _amount(n: float | int, unit: str | None) -> str:
    if not unit:
        return _figure(n)
    if unit in _PREFIX:
        return f"{unit}{_figure(n)}"
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
    out: list[Block] = [
        {"type": "person", "name": w["from"], "detail": w["subject"]},
        {"type": "text", "text": w["ask"]},
    ]
    if "due" in w:
        out.append({"type": "facts", "items": [{"label": "Due", "value": _deadline(w["due"])}]})
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
    ring = {"type": "meter", "value": w["value"], "max": w["target"], "label": w["label"]}
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


def expand(widget: Widget) -> list[Block]:
    """The blocks a saved widget is drawn with. A copy: the page's widget never shares
    a list with the saved board."""
    return copy.deepcopy(_RECIPES[widget["type"]](widget))


def expand_board(board: Board) -> Board:
    """The board as the page gets it: each widget its name, size, source and asks, and
    its blocks. The saved board is left as it was."""
    widgets = [{**{k: w[k] for k in KEPT if k in w}, "blocks": expand(w)} for w in board["widgets"]]
    return {**{k: v for k, v in board.items() if k != "widgets"}, "widgets": widgets}
