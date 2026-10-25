"""The widget kit: today's seven widget types checked field by field (D1, D5), and the
field checks every widget and block is built from.

Pure, no I/O. `widgets.validate_widget` is the way in. A widget comes in as the model
wrote it and leaves as the JSON that is saved: the spec's field names only, strings
trimmed, put on one line (a note's text keeps its lines) and capped, optional fields
left out when absent, anything else dropped. Display-only text (stat fields, chart x
labels, list meta, metric labels) also takes a finite number and writes it as text;
everywhere else a wrong kind is refused, so the page never meets a surprise.

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
# The block tones, and the stat's first words for good and bad.
TONES = ("good", "bad", "attention", "neutral", "up", "down")
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


def fail(where: str, problem: str) -> BoardError:
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


def text_field(
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
            raise fail(where, f"{key} is required")
        return None
    if loose and _shown(value) is not None:
        value = _shown(value)
    if not isinstance(value, str):
        raise fail(where, f"{key} must be a string")
    if not value.strip():
        if need:
            raise fail(where, f"{key} is blank")
        return None
    return cap(value, limit)


def one_of(
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
            raise fail(where, f"{key} is required ({', '.join(options)})")
        return default
    if value not in options:
        raise fail(where, f"{key} must be one of {', '.join(options)}, not {value!r}")
    return str(value)


def list_field(
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
        raise fail(where, f"{key} must be a list")
    one, many = nouns
    if len(value) < low:
        raise fail(where, f"{key} is empty" if low == 1 else f"{key} needs at least {low} {many}")
    if len(value) > limit:
        raise fail(where, f"{len(value)} {one if len(value) == 1 else many}, {limit} at most")
    return value


def as_object(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise fail(where, "must be an object")
    return value


def finite_number(value: Any, where: str) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise fail(where, f"{value!r} is not a number")
    if not math.isfinite(value):
        raise fail(where, "values must be finite numbers")
    return value


def put(out: Widget, key: str, value: Any) -> None:
    if value is not None:
        out[key] = value


# One function per type: (raw, where) -> the type's own fields.


def _stat(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {
        "label": text_field(raw, "label", where, LABEL, loose=True),
        "value": text_field(raw, "value", where, VALUE, loose=True),
    }
    put(out, "delta", text_field(raw, "delta", where, VALUE, need=False, loose=True))
    put(out, "hint", text_field(raw, "hint", where, TEXT, need=False, loose=True))
    put(out, "tone", one_of(raw, "tone", where, TONES, need=False))
    return out


def check_chart(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {}
    put(out, "title", text_field(raw, "title", where, TITLE, need=False))
    kind = one_of(raw, "kind", where, KINDS)
    x = list_field(raw, "x", where, MAX_POINTS, ("point", "points"))
    labels = [
        text_field({"x": v}, "x", f"{where}: x label {i}", LABEL, loose=True)
        for i, v in enumerate(x, 1)
    ]
    series = list_field(raw, "series", where, MAX_SERIES, ("series", "series"))
    if kind == "donut" and len(series) != 1:
        raise fail(where, f"a donut takes one series, not {len(series)}")
    drawn = []
    for i, s in enumerate(series, 1):
        s = as_object(s, f"{where}: series {i}")
        name = text_field(s, "name", f"{where}: series {i}", LABEL)
        values = s.get("values")
        if not isinstance(values, list):
            raise fail(where, f"series '{name}' values must be a list")
        if len(values) != len(x):
            raise fail(where, f"series '{name}' has {len(values)} values but x has {len(x)}")
        drawn.append(
            {
                "name": name,
                "values": [finite_number(v, f"{where}: series '{name}'") for v in values],
            }
        )
    out.update(kind=kind, x=labels, series=drawn)
    put(out, "unit", text_field(raw, "unit", where, VALUE, need=False))
    return out


def _list(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {}
    put(out, "title", text_field(raw, "title", where, TITLE, need=False))
    items = []
    for i, item in enumerate(list_field(raw, "items", where, MAX_ITEMS, ("item", "items")), 1):
        at = f"{where}: item {i}"
        item = as_object(item, at)
        one: Widget = {"title": text_field(item, "title", at, TITLE)}
        put(one, "subtitle", text_field(item, "subtitle", at, TEXT, need=False))
        put(one, "meta", text_field(item, "meta", at, TEXT, need=False, loose=True))
        done = item.get("done")
        if done is not None and not isinstance(done, bool):
            raise fail(at, "done must be true or false")
        put(one, "done", done)
        items.append(one)
    out["items"] = items
    return out


def _cell(value: Any, where: str) -> str | float | int:
    if isinstance(value, str):
        return cap(value, LABEL)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise fail(
            where, f'a cell must be a string or a number, not {value!r}; use "" for an empty cell'
        )
    return finite_number(value, where)


def check_table(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {}
    put(out, "title", text_field(raw, "title", where, TITLE, need=False))
    columns = list_field(raw, "columns", where, MAX_COLUMNS, ("column", "columns"))
    heads = [
        text_field({"column": c}, "column", f"{where}: column {i}", LABEL)
        for i, c in enumerate(columns, 1)
    ]
    rows = list_field(raw, "rows", where, MAX_ROWS, ("row", "rows"), low=0)
    cells = []
    for i, row in enumerate(rows, 1):
        if not isinstance(row, list):
            raise fail(where, f"row {i} must be a list")
        if len(row) != len(heads):
            raise fail(where, f"row {i} has {len(row)} cells but there are {len(heads)} columns")
        cells.append([_cell(c, f"{where}: row {i}") for c in row])
    out.update(columns=heads, rows=cells)
    return out


def long_text(raw: Mapping[str, Any], where: str, limit: int, key: str = "text") -> str:
    """Text that keeps its lines. Refused, not cut: a clipped paragraph would mislead."""
    text = raw.get(key)
    if not isinstance(text, str) or not text.strip():
        raise fail(where, f"{key} is required")
    text = "\n".join(line.strip() for line in text.strip().splitlines())
    if len(text) > limit:
        raise fail(where, f"{key} is {len(text)} characters, {limit} at most")
    return text


def _note(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {}
    put(out, "title", text_field(raw, "title", where, TITLE, need=False))
    out["text"] = long_text(raw, where, NOTE)
    return out


def _time(raw: Mapping[str, Any], key: str, where: str, *, need: bool = True) -> str | None:
    value = text_field(raw, key, where, VALUE, need=need)
    if value is None:
        return None
    match = _HHMM.match(value)
    if not match or int(match[1]) > 23:
        raise fail(where, f"{key} {value!r} must be HH:MM (00:00 to 23:59)")
    # 9:30 becomes 09:30, so times compare and line up as text.
    return f"{int(match[1]):02d}:{match[2]}"


def check_timeline(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {}
    put(out, "title", text_field(raw, "title", where, TITLE, need=False))
    events = []
    for i, event in enumerate(list_field(raw, "events", where, MAX_EVENTS, ("event", "events")), 1):
        at = f"{where}: event {i}"
        event = as_object(event, at)
        one: Widget = {
            "title": text_field(event, "title", at, TITLE),
            "start": _time(event, "start", at),
        }
        end = _time(event, "end", at, need=False)
        if end is not None and end < one["start"]:
            raise fail(at, f"end {end} is before start {one['start']} (one day only)")
        put(one, "end", end)
        put(one, "meta", text_field(event, "meta", at, TEXT, need=False))
        events.append(one)
    out["events"] = events
    return out


def _option(option: Any, at: str) -> Widget:
    if isinstance(option, str):
        raise fail(
            at, "an option is {name, metrics}; for options scored against criteria use decision"
        )
    option = as_object(option, at)
    one: Widget = {"name": text_field(option, "name", at, LABEL)}
    put(one, "note", text_field(option, "note", at, TEXT, need=False))
    metrics = []
    raw_metrics = option.get("metrics")
    raw_metrics = [] if raw_metrics is None else raw_metrics
    for j, metric in enumerate(
        list_field(
            {"metrics": raw_metrics}, "metrics", at, MAX_METRICS, ("metric", "metrics"), low=0
        ),
        1,
    ):
        m = as_object(metric, f"{at}: metric {j}")
        label = text_field(m, "label", f"{at}: metric {j}", LABEL, loose=True)
        score = m.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 10:
            raise fail(at, f"metric '{label}' score must be a number from 0 to 10, not {score!r}")
        metrics.append({"label": label, "score": score})
    one["metrics"] = metrics
    if option.get("tags") is not None:
        tags = list_field(option, "tags", at, MAX_TAGS, ("tag", "tags"), low=0)
        one["tags"] = [
            text_field({"tag": tag}, "tag", f"{at}: tag {k}", LABEL)
            for k, tag in enumerate(tags, 1)
        ]
    return one


def _compare(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {}
    put(out, "title", text_field(raw, "title", where, TITLE, need=False))
    options = raw.get("options")
    if not isinstance(options, list):
        raise fail(where, "options must be a list")
    if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
        noun = "option" if len(options) == 1 else "options"
        raise fail(where, f"{len(options)} {noun}; a compare takes 2 to 4")
    out["options"] = [_option(o, f"{where}: option {i}") for i, o in enumerate(options, 1)]
    pick = text_field(raw, "pick", where, LABEL, need=False)
    names = [o["name"] for o in out["options"]]
    twice = sorted({name for name in names if names.count(name) > 1})
    if twice:
        raise fail(where, f"option name {twice[0]!r} is used twice; names must differ")
    if pick is not None and pick not in names:
        raise fail(where, f"pick {pick!r} is not an option; use one of {', '.join(names)}")
    put(out, "pick", pick)
    put(out, "reason", text_field(raw, "reason", where, TEXT, need=False))
    return out


BUILDERS: dict[str, Callable[[Mapping[str, Any], str], Widget]] = {
    "stat": _stat,
    "chart": check_chart,
    "list": _list,
    "table": check_table,
    "note": _note,
    "timeline": check_timeline,
    "compare": _compare,
}
