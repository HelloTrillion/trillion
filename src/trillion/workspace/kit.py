"""The widget kit: what a board may hold, checked field by field (D1, D5).

Pure, no I/O. A widget comes in as the model wrote it and leaves as the exact JSON
the page draws: the spec's field names only, strings trimmed, put on one line (a
note's text keeps its lines) and capped, optional fields left out when absent,
anything else dropped. Display-only text (stat fields, chart x labels, list meta,
metric labels) also takes a finite number and writes it as text; everywhere else a
wrong kind is refused, so the page never meets a surprise.

The first problem raises `BoardError` in words the model can act on, naming the
widget ("widget 2 (chart)" for a new one, "chart w2" for one on the board), the
field and the limit.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from typing import Any

MAX_WIDGETS = 12
SIZES = ("small", "medium", "wide", "full")
TONES = ("up", "down", "neutral")
KINDS = ("line", "bar", "area", "donut")

# Character caps (the spec): titles, labels, values, any other text, a note's text.
TITLE, LABEL, VALUE, TEXT, NOTE = 80, 60, 40, 400, 2000

MAX_SERIES, MAX_POINTS = 5, 200
MAX_ITEMS = 30
MAX_COLUMNS, MAX_ROWS = 8, 50
MAX_EVENTS = 20
MIN_OPTIONS, MAX_OPTIONS, MAX_METRICS = 2, 4, 5
# Not in the spec's table; a bound so a compare card stays a card.
MAX_TAGS = 6

_HHMM = re.compile(r"^(\d{1,2}):([0-5]\d)$")

Widget = dict[str, Any]


class BoardError(ValueError):
    """A change the kit refuses; the message is meant for the model to fix its call."""


def _fail(where: str, problem: str) -> BoardError:
    return BoardError(f"{where}: {problem}.")


def cap(text: str, limit: int) -> str:
    """Trimmed, on one line, and at most `limit` characters (a cut ends in "…")."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _shown(value: Any) -> str | None:
    """A finite number written as text, or None. Display-only fields take these: the
    model often writes a value as 41.2, and refusing it would cost a turn for nothing."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _str(
    raw: Mapping[str, Any],
    key: str,
    where: str,
    limit: int,
    *,
    need: bool = True,
    loose: bool = False,
) -> str | None:
    value = raw.get(key)
    if value is None:
        if need:
            raise _fail(where, f"{key} is required")
        return None
    if loose and _shown(value) is not None:
        value = _shown(value)
    if not isinstance(value, str):
        raise _fail(where, f"{key} must be a string")
    if not value.strip():
        if need:
            raise _fail(where, f"{key} is blank")
        return None
    return cap(value, limit)


def _choice(
    raw: Mapping[str, Any],
    key: str,
    where: str,
    options: tuple[str, ...],
    *,
    need: bool = True,
    default: str | None = None,
) -> str | None:
    value = raw.get(key)
    if value is None:
        if need and default is None:
            raise _fail(where, f"{key} is required ({', '.join(options)})")
        return default
    if value not in options:
        raise _fail(where, f"{key} must be one of {', '.join(options)}, not {value!r}")
    return str(value)


def _array(
    raw: Mapping[str, Any],
    key: str,
    where: str,
    limit: int,
    nouns: tuple[str, str],
    *,
    low: int = 1,
) -> list[Any]:
    """A list of `low` to `limit` entries; `nouns` is (one, many) for the message."""
    value = raw.get(key)
    if not isinstance(value, list):
        raise _fail(where, f"{key} must be a list")
    one, many = nouns
    if len(value) < low:
        raise _fail(where, f"{key} is empty" if low == 1 else f"{key} needs at least {low} {many}")
    if len(value) > limit:
        raise _fail(where, f"{len(value)} {one if len(value) == 1 else many}, {limit} at most")
    return value


def _obj(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(where, "must be an object")
    return value


def _number(value: Any, where: str) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(where, f"{value!r} is not a number")
    if not math.isfinite(value):
        raise _fail(where, "values must be finite numbers")
    return value


def _put(out: Widget, key: str, value: Any) -> None:
    if value is not None:
        out[key] = value


# One function per type: (raw, where) -> the type's own fields.


def _stat(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {
        "label": _str(raw, "label", where, LABEL, loose=True),
        "value": _str(raw, "value", where, VALUE, loose=True),
    }
    _put(out, "delta", _str(raw, "delta", where, VALUE, need=False, loose=True))
    _put(out, "hint", _str(raw, "hint", where, TEXT, need=False, loose=True))
    _put(out, "tone", _choice(raw, "tone", where, TONES, need=False))
    return out


def _chart(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {}
    _put(out, "title", _str(raw, "title", where, TITLE, need=False))
    kind = _choice(raw, "kind", where, KINDS)
    x = _array(raw, "x", where, MAX_POINTS, ("point", "points"))
    labels = [
        _str({"x": v}, "x", f"{where}: x label {i}", LABEL, loose=True) for i, v in enumerate(x, 1)
    ]
    series = _array(raw, "series", where, MAX_SERIES, ("series", "series"))
    if kind == "donut" and len(series) != 1:
        raise _fail(where, f"a donut takes one series, not {len(series)}")
    drawn = []
    for i, s in enumerate(series, 1):
        s = _obj(s, f"{where}: series {i}")
        name = _str(s, "name", f"{where}: series {i}", LABEL)
        values = s.get("values")
        if not isinstance(values, list):
            raise _fail(where, f"series '{name}' values must be a list")
        if len(values) != len(x):
            raise _fail(where, f"series '{name}' has {len(values)} values but x has {len(x)}")
        drawn.append(
            {"name": name, "values": [_number(v, f"{where}: series '{name}'") for v in values]}
        )
    out.update(kind=kind, x=labels, series=drawn)
    _put(out, "unit", _str(raw, "unit", where, VALUE, need=False))
    return out


def _list(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {}
    _put(out, "title", _str(raw, "title", where, TITLE, need=False))
    items = []
    for i, item in enumerate(_array(raw, "items", where, MAX_ITEMS, ("item", "items")), 1):
        at = f"{where}: item {i}"
        item = _obj(item, at)
        one: Widget = {"title": _str(item, "title", at, TITLE)}
        _put(one, "subtitle", _str(item, "subtitle", at, TEXT, need=False))
        _put(one, "meta", _str(item, "meta", at, TEXT, need=False, loose=True))
        done = item.get("done")
        if done is not None and not isinstance(done, bool):
            raise _fail(at, "done must be true or false")
        _put(one, "done", done)
        items.append(one)
    out["items"] = items
    return out


def _cell(value: Any, where: str) -> str | float | int:
    if isinstance(value, str):
        return cap(value, LABEL)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(
            where, f'a cell must be a string or a number, not {value!r}; use "" for an empty cell'
        )
    return _number(value, where)


def _table(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {}
    _put(out, "title", _str(raw, "title", where, TITLE, need=False))
    columns = _array(raw, "columns", where, MAX_COLUMNS, ("column", "columns"))
    heads = [
        _str({"column": c}, "column", f"{where}: column {i}", LABEL)
        for i, c in enumerate(columns, 1)
    ]
    rows = _array(raw, "rows", where, MAX_ROWS, ("row", "rows"), low=0)
    cells = []
    for i, row in enumerate(rows, 1):
        if not isinstance(row, list):
            raise _fail(where, f"row {i} must be a list")
        if len(row) != len(heads):
            raise _fail(where, f"row {i} has {len(row)} cells but there are {len(heads)} columns")
        cells.append([_cell(c, f"{where}: row {i}") for c in row])
    out.update(columns=heads, rows=cells)
    return out


def _note(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {}
    _put(out, "title", _str(raw, "title", where, TITLE, need=False))
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        raise _fail(where, "text is required")
    text = text.strip()
    # Refused rather than cut: a note clipped mid-sentence would mislead.
    if len(text) > NOTE:
        raise _fail(where, f"text is {len(text)} characters, {NOTE} at most")
    out["text"] = text
    return out


def _time(raw: Mapping[str, Any], key: str, where: str, *, need: bool = True) -> str | None:
    value = _str(raw, key, where, VALUE, need=need)
    if value is None:
        return None
    match = _HHMM.match(value)
    if not match or int(match[1]) > 23:
        raise _fail(where, f"{key} {value!r} must be HH:MM (00:00 to 23:59)")
    # 9:30 becomes 09:30, so times compare and line up as text.
    return f"{int(match[1]):02d}:{match[2]}"


def _timeline(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {}
    _put(out, "title", _str(raw, "title", where, TITLE, need=False))
    events = []
    for i, event in enumerate(_array(raw, "events", where, MAX_EVENTS, ("event", "events")), 1):
        at = f"{where}: event {i}"
        event = _obj(event, at)
        one: Widget = {"title": _str(event, "title", at, TITLE), "start": _time(event, "start", at)}
        end = _time(event, "end", at, need=False)
        if end is not None and end < one["start"]:
            raise _fail(at, f"end {end} is before start {one['start']} (one day only)")
        _put(one, "end", end)
        _put(one, "meta", _str(event, "meta", at, TEXT, need=False))
        events.append(one)
    out["events"] = events
    return out


def _option(option: Any, at: str) -> Widget:
    option = _obj(option, at)
    one: Widget = {"name": _str(option, "name", at, LABEL)}
    _put(one, "note", _str(option, "note", at, TEXT, need=False))
    metrics = []
    raw_metrics = option.get("metrics")
    raw_metrics = [] if raw_metrics is None else raw_metrics
    for j, metric in enumerate(
        _array({"metrics": raw_metrics}, "metrics", at, MAX_METRICS, ("metric", "metrics"), low=0),
        1,
    ):
        m = _obj(metric, f"{at}: metric {j}")
        label = _str(m, "label", f"{at}: metric {j}", LABEL, loose=True)
        score = m.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 10:
            raise _fail(at, f"metric '{label}' score must be a number from 0 to 10, not {score!r}")
        metrics.append({"label": label, "score": score})
    one["metrics"] = metrics
    if option.get("tags") is not None:
        tags = _array(option, "tags", at, MAX_TAGS, ("tag", "tags"), low=0)
        one["tags"] = [
            _str({"tag": tag}, "tag", f"{at}: tag {k}", LABEL) for k, tag in enumerate(tags, 1)
        ]
    return one


def _compare(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {}
    _put(out, "title", _str(raw, "title", where, TITLE, need=False))
    options = raw.get("options")
    if not isinstance(options, list):
        raise _fail(where, "options must be a list")
    if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
        noun = "option" if len(options) == 1 else "options"
        raise _fail(where, f"{len(options)} {noun}; a compare takes 2 to 4")
    out["options"] = [_option(o, f"{where}: option {i}") for i, o in enumerate(options, 1)]
    pick = _str(raw, "pick", where, LABEL, need=False)
    names = [o["name"] for o in out["options"]]
    twice = sorted({name for name in names if names.count(name) > 1})
    if twice:
        raise _fail(where, f"option name {twice[0]!r} is used twice; names must differ")
    if pick is not None and pick not in names:
        raise _fail(where, f"pick {pick!r} is not an option; use one of {', '.join(names)}")
    _put(out, "pick", pick)
    _put(out, "reason", _str(raw, "reason", where, TEXT, need=False))
    return out


_TYPES: dict[str, Callable[[Mapping[str, Any], str], Widget]] = {
    "stat": _stat,
    "chart": _chart,
    "list": _list,
    "table": _table,
    "note": _note,
    "timeline": _timeline,
    "compare": _compare,
}
# Today's seven, then the recipes built on blocks (`recipe_kit.RECIPES`, which a test
# keeps in step with this list).
TYPES = (
    *_TYPES,
    "next_up",
    "free_time",
    "inbox",
    "thread",
    "mentions",
    "files",
    "quote",
    "plan",
    "decision",
    "facts",
    "callout",
    "goal",
    "sources",
    "custom",
)


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
# With no usable type, the fields say which widget it is, most telling first.
_BY_FIELD = (
    ("blocks", "custom"),
    ("series", "chart"),
    ("criteria", "decision"),
    ("options", "compare"),
    ("slots", "free_time"),
    ("steps", "plan"),
    ("events", "timeline"),
    ("columns", "table"),
    ("items", "list"),
    ("value", "stat"),
    ("text", "note"),
)


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
        return {**raw, "type": _ALIASES[name]}
    if name == "text":
        return {**raw, "type": "note"}
    for field, kind in _BY_FIELD:
        if field in raw:
            return {**raw, "type": kind}
    return raw


def validate_widget(raw: Any, at: int | str) -> Widget:
    """The widget as it is saved, without an id. `at` is the position in the call
    (1-based) for a new widget, or the id of the widget it replaces."""
    # Imported here: the recipes are built on blocks, and blocks on this module.
    from trillion.workspace import recipe_kit
    from trillion.workspace.blocks import _scrub

    named = f"widget {at}" if isinstance(at, int) else at
    raw = _typed(_scrub(_obj(raw, named)))
    kind = raw.get("type")
    build = None
    if isinstance(kind, str):
        build = _TYPES.get(kind) or recipe_kit.RECIPES.get(kind)
    if build is None:
        what = f"unknown type {kind!r}" if kind is not None else "no type"
        raise _fail(named, f"{what}; use one of {', '.join(TYPES)}")
    where = f"widget {at} ({kind})" if isinstance(at, int) else f"{kind} {at}"
    size = _choice(raw, "size", where, SIZES, default=recipe_kit.SIZE.get(kind, "medium"))
    out: Widget = {"type": kind, "size": size}
    # Every widget may have a title; today's types check theirs among their fields.
    _put(out, "title", _str(raw, "title", where, TITLE, need=False))
    out.update(build(raw, where))
    _put(out, "source", recipe_kit.source(raw, where))
    _put(out, "asks", recipe_kit.asks(raw, where))
    return out
