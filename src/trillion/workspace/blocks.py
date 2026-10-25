"""The blocks every widget is made of (the widget design system spec, "Blocks").

Pure, no I/O. A block comes in as the model wrote it and leaves as the exact JSON the
page draws: the spec's field names only, strings trimmed and capped, emoji removed,
anything else dropped. The first problem raises `BoardError` naming the widget, the
block ("block 3 (meter)") and the problem, in words the model can act on.

The size, text and list checks are the kit's own helpers; chart, table and timeline
blocks run the kit's checks for those widgets, so a block and a widget never disagree.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, cast

from trillion.workspace.kit import (
    LABEL,
    MAX_ITEMS,
    NOTE,
    TEXT,
    TITLE,
    VALUE,
    as_object,
    check_chart,
    check_table,
    check_timeline,
    fail,
    finite_number,
    list_field,
    long_text,
    put,
    text_field,
)
from trillion.workspace.text import scrub

Block = dict[str, Any]

BLOCK_TONES = ("good", "bad", "attention", "neutral")
SHAPES = ("bar", "ring")

MAX_BLOCKS = 8
MIN_SPLIT, MAX_SPLIT = 2, 4  # side-by-side columns of a card, not a table's
MIN_SPARK, MAX_SPARK = 2, 60
MAX_PEOPLE, MAX_CHIPS, MAX_FACTS = 8, 6, 10
QUOTE = 600

# ISO 8601: a date, or a date and time with optional seconds and a Z or offset.
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})?)?$")
# A time of day alone, as a model often writes one: 9:12, 09:12, 9:12 am, 3pm, 15:30.
_CLOCK = re.compile(r"^(\d{1,2})(?::([0-5]\d))?\s*(?:([ap])\.?\s*m\.?)?$", re.IGNORECASE)


@dataclass
class Card:
    """What a whole card counts across its blocks."""

    items: int = 0


def tone_field(
    raw: Mapping[str, Any], where: str, *, need: bool = False, key: str = "tone"
) -> str | None:
    value = raw.get(key)
    if value is None:
        if need:
            raise fail(where, f"{key} is required ({', '.join(BLOCK_TONES)})")
        return None
    # The widgets' old words still work.
    value = {"up": "good", "down": "bad"}.get(value, value) if isinstance(value, str) else value
    if value not in BLOCK_TONES:
        raise fail(where, f"{key} must be one of {', '.join(BLOCK_TONES)}, not {value!r}")
    return str(value)


def today() -> date:
    """Today in the server's local time: the day a bare clock time is on. A function so
    tests can fix it."""
    return date.today()


def clock_time(text: str) -> str | None:
    """A bare time of day as 24-hour HH:MM ("9:12 am" is 09:12, "3pm" is 15:00), or
    None when the text is not one. A lone number ("9") is not a time."""
    match = _CLOCK.match(text.strip())
    if match is None:
        return None
    hour, minute, half = int(match[1]), int(match[2] or 0), (match[3] or "").lower()
    if not match[2] and not half:
        return None
    if half:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if half == "p" else 0)
    elif hour > 23:
        return None
    return f"{hour:02d}:{minute:02d}"


def iso_field(raw: Mapping[str, Any], key: str, where: str, *, need: bool = False) -> str | None:
    """An ISO 8601 date or time. A bare clock time ("9:12") is taken as today in the
    server's local time, written without a zone, as the user's local times are."""
    value = text_field(raw, key, where, VALUE, need=need)
    if value is None:
        return None
    clock = clock_time(value)
    if clock is not None:
        return f"{today().isoformat()}T{clock}"
    ok = _ISO.match(value) is not None
    if ok:
        try:
            datetime.fromisoformat(value)
        except ValueError:
            ok = False
    if not ok:
        raise fail(
            where,
            f"{key} {value!r} must be ISO 8601, like 2026-10-25T14:30 or 2026-10-25",
        )
    return value


def ends_before(end: str, start: str) -> bool:
    """Whether end is earlier than start, when the two can be compared (both with an
    offset or both without; otherwise the zone is unknown and nothing is claimed)."""
    a, b = datetime.fromisoformat(end), datetime.fromisoformat(start)
    return (a.tzinfo is None) == (b.tzinfo is None) and a < b


def number_field(raw: Mapping[str, Any], key: str, where: str) -> float | int:
    if raw.get(key) is None:
        raise fail(where, f"{key} is required")
    return finite_number(raw[key], f"{where}: {key}")


def word_list(
    raw: Mapping[str, Any], key: str, where: str, limit: int, nouns: tuple[str, str], cap: int
) -> list[str]:
    """A list of short strings, as `names` or `items`."""
    got = list_field(raw, key, where, limit, nouns)
    return [
        cast(str, text_field({nouns[0]: v}, nouns[0], f"{where}: {nouns[0]} {i}", cap, loose=True))
        for i, v in enumerate(got, 1)
    ]


def _value(raw: Mapping[str, Any], where: str, card: Card) -> Block:
    out: Block = {"value": text_field(raw, "value", where, VALUE, loose=True)}
    put(out, "label", text_field(raw, "label", where, LABEL, need=False, loose=True))
    put(out, "delta", text_field(raw, "delta", where, VALUE, need=False, loose=True))
    put(out, "hint", text_field(raw, "hint", where, TEXT, need=False, loose=True))
    put(out, "tone", tone_field(raw, where))
    return out


def _sparkline(raw: Mapping[str, Any], where: str, card: Card) -> Block:
    values = list_field(raw, "values", where, MAX_SPARK, ("value", "values"), low=MIN_SPARK)
    out: Block = {"values": [finite_number(v, f"{where}: values") for v in values]}
    put(out, "tone", tone_field(raw, where))
    return out


def _meter(raw: Mapping[str, Any], where: str, card: Card) -> Block:
    out: Block = {
        "value": number_field(raw, "value", where),
        "max": number_field(raw, "max", where),
    }
    if out["max"] <= 0:
        raise fail(where, f"max must be above 0, not {out['max']}")
    put(out, "label", text_field(raw, "label", where, LABEL, need=False, loose=True))
    shape = raw.get("shape")
    if shape is not None and shape not in SHAPES:
        raise fail(where, f"shape must be one of {', '.join(SHAPES)}, not {shape!r}")
    put(out, "shape", shape)
    put(out, "tone", tone_field(raw, where))
    return out


def _time(raw: Mapping[str, Any], where: str, card: Card) -> Block:
    out: Block = {"at": iso_field(raw, "at", where, need=True)}
    end = iso_field(raw, "end", where)
    if end is not None and ends_before(end, out["at"]):
        raise fail(where, f"end {end} is before at {out['at']}")
    put(out, "end", end)
    put(out, "label", text_field(raw, "label", where, LABEL, need=False))
    return out


def _chart_block(raw: Mapping[str, Any], where: str, card: Card) -> Block:
    out = check_chart(raw, where)
    out.pop("title", None)  # a card has one title; the block has none
    return out


def _table_block(raw: Mapping[str, Any], where: str, card: Card) -> Block:
    out = check_table(raw, where)
    out.pop("title", None)
    highlight = raw.get("highlight")
    if highlight is not None:
        rows = len(out["rows"])
        if (
            isinstance(highlight, bool)
            or not isinstance(highlight, int)
            or not 0 <= highlight < rows
        ):
            noun = "row" if rows == 1 else "rows"
            raise fail(
                where,
                f"highlight {highlight!r} is not a row; the table has {rows} {noun}"
                " (0 is the first)",
            )
        out["highlight"] = highlight
    return out


def _timeline_block(raw: Mapping[str, Any], where: str, card: Card) -> Block:
    out = check_timeline(raw, where)
    out.pop("title", None)
    return out


def _item(raw: Any, where: str) -> Block:
    raw = as_object(raw, where)
    out: Block = {"title": text_field(raw, "title", where, TITLE)}
    put(out, "detail", text_field(raw, "detail", where, TEXT, need=False))
    put(out, "person", text_field(raw, "person", where, LABEL, need=False))
    put(out, "status", tone_field(raw, where, key="status"))
    put(out, "when", iso_field(raw, "when", where))
    put(out, "meta", text_field(raw, "meta", where, TEXT, need=False, loose=True))
    put(out, "chip", text_field(raw, "chip", where, LABEL, need=False))
    done = raw.get("done")
    if done is not None and not isinstance(done, bool):
        raise fail(where, "done must be true or false")
    put(out, "done", done)
    return out


def _rows(raw: Mapping[str, Any], where: str, card: Card) -> Block:
    out: Block = {}
    put(out, "label", text_field(raw, "label", where, LABEL, need=False))
    items = list_field(raw, "items", where, MAX_ITEMS, ("item", "items"))
    card.items += len(items)
    if card.items > MAX_ITEMS:
        raise fail(where, f"{card.items} items across the card, {MAX_ITEMS} at most")
    out["items"] = [_item(item, f"{where}: item {i}") for i, item in enumerate(items, 1)]
    return out


def _person(raw: Mapping[str, Any], where: str, card: Card) -> Block:
    out: Block = {"name": text_field(raw, "name", where, LABEL)}
    put(out, "detail", text_field(raw, "detail", where, TEXT, need=False))
    return out


def _people(raw: Mapping[str, Any], where: str, card: Card) -> Block:
    return {"names": word_list(raw, "names", where, MAX_PEOPLE, ("name", "names"), LABEL)}


def _status(raw: Mapping[str, Any], where: str, card: Card) -> Block:
    return {"tone": tone_field(raw, where, need=True), "text": text_field(raw, "text", where, TEXT)}


def _chips(raw: Mapping[str, Any], where: str, card: Card) -> Block:
    out: Block = {"items": word_list(raw, "items", where, MAX_CHIPS, ("item", "items"), LABEL)}
    put(out, "tone", tone_field(raw, where))
    return out


def _text(raw: Mapping[str, Any], where: str, card: Card) -> Block:
    out: Block = {"text": long_text(raw, where, NOTE)}
    put(out, "tone", tone_field(raw, where))
    return out


def quote_block(raw: Mapping[str, Any], where: str, card: Card) -> Block:
    out: Block = {"text": long_text(raw, where, QUOTE)}
    put(out, "by", text_field(raw, "by", where, LABEL, need=False))
    put(out, "when", iso_field(raw, "when", where))
    return out


def facts_block(raw: Mapping[str, Any], where: str, card: Card) -> Block:
    items = []
    for i, item in enumerate(list_field(raw, "items", where, MAX_FACTS, ("item", "items")), 1):
        at = f"{where}: item {i}"
        item = as_object(item, at)
        items.append(
            {
                "label": text_field(item, "label", at, LABEL, loose=True),
                "value": text_field(item, "value", at, VALUE, loose=True),
            }
        )
    return {"items": items}


def _columns(raw: Mapping[str, Any], where: str, card: Card) -> Block:
    columns = list_field(raw, "columns", where, MAX_SPLIT, ("column", "columns"), low=MIN_SPLIT)
    drawn = []
    for i, column in enumerate(columns, 1):
        at = f"{where}: column {i}"
        if not isinstance(column, list):
            raise fail(where, f"column {i} must be a list of blocks")
        drawn.append(_blocks(column, at, card, inside=True))
    return {"columns": drawn}


_BLOCKS: dict[str, Callable[[Mapping[str, Any], str, Card], Block]] = {
    "value": _value,
    "sparkline": _sparkline,
    "meter": _meter,
    "time": _time,
    "chart": _chart_block,
    "table": _table_block,
    "timeline": _timeline_block,
    "rows": _rows,
    "person": _person,
    "people": _people,
    "status": _status,
    "chips": _chips,
    "text": _text,
    "quote": quote_block,
    "facts": facts_block,
    "columns": _columns,
}
BLOCK_TYPES = tuple(_BLOCKS)


def _blocks(raw: Any, where: str, card: Card, *, inside: bool) -> list[Block]:
    if not isinstance(raw, list):
        raise fail(where, "blocks must be a list")
    listed = list_field({"blocks": raw}, "blocks", where, MAX_BLOCKS, ("block", "blocks"))
    out = []
    for i, given in enumerate(listed, 1):
        given = as_object(given, f"{where}: block {i}")
        kind = given.get("type")
        name = kind.strip().lower() if isinstance(kind, str) else kind
        build = _BLOCKS.get(name) if isinstance(name, str) else None
        if build is None:
            what = f"unknown type {kind!r}" if kind is not None else "no type"
            raise fail(f"{where}: block {i}", f"{what}; use one of {', '.join(BLOCK_TYPES)}")
        at = f"{where}: block {i} ({name})"
        if inside and name == "columns":
            raise fail(at, "columns cannot hold columns")
        out.append({"type": name, **build(scrub(given), at, card)})
    return out


def validate_blocks(raw: Any, where: str) -> list[Block]:
    """The page-ready blocks of one card. `where` names the widget, as the kit does
    ("widget 2 (custom)"), and leads every message."""
    return _blocks(raw, where, Card(), inside=False)
