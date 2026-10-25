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
from datetime import datetime
from typing import Any

from trillion.workspace.kit import (
    LABEL,
    MAX_ITEMS,
    NOTE,
    TEXT,
    TITLE,
    VALUE,
    _array,
    _chart,
    _fail,
    _number,
    _obj,
    _put,
    _str,
    _table,
    _timeline,
)

Block = dict[str, Any]

BLOCK_TONES = ("good", "bad", "attention", "neutral")
SHAPES = ("bar", "ring")

MAX_BLOCKS = 8
MIN_COLUMNS, MAX_COLUMNS = 2, 4
MIN_SPARK, MAX_SPARK = 2, 60
MAX_PEOPLE, MAX_CHIPS, MAX_FACTS = 8, 6, 10
QUOTE = 600

# Emoji and pictographs. Text symbols that carry data ($ € £ % ° → ✓ ▲ ▼) stay.
_EMOJI = re.compile(
    "["
    "\U0001f000-\U0001faff"  # cards, enclosed symbols, flags, pictographs, skin tones
    "\U000e0020-\U000e007f"  # tag characters (flag sequences)
    "☀-✒✙-➿"  # misc symbols and dingbats, except the ✓ ✗ family
    "⬀-⯿"  # stars, big squares, circles
    "⌚⌛⌨⏏⏩-⏺Ⓜ▪▫▶◀◻-◾"
    "⤴⤵〰〽㊗㊙‼⁉"
    "]"
)
# What emoji leave behind: variation selectors, joiners, the keycap ring.
_LEFTOVER = re.compile("[︎️‍⃣]")
_GAPS = re.compile(r"[ \t]{2,}")

# ISO 8601: a date, or a date and time with optional seconds and a Z or offset.
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})?)?$")


def strip_emoji(text: str) -> str:
    """The text without emoji, pictographs or the joiners and selectors they leave."""
    clean = _LEFTOVER.sub("", _EMOJI.sub("", text))
    return text if clean == text else _GAPS.sub(" ", clean)


def _scrub(value: Any) -> Any:
    """Every string in the block, however deep, without emoji."""
    if isinstance(value, str):
        return strip_emoji(value)
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, Mapping):
        return {k: _scrub(v) for k, v in value.items()}
    return value


@dataclass
class _Card:
    """What a whole card counts across its blocks."""

    items: int = 0


def _tone(
    raw: Mapping[str, Any], where: str, *, need: bool = False, key: str = "tone"
) -> str | None:
    value = raw.get(key)
    if value is None:
        if need:
            raise _fail(where, f"{key} is required ({', '.join(BLOCK_TONES)})")
        return None
    # The widgets' old words still work.
    value = {"up": "good", "down": "bad"}.get(value, value) if isinstance(value, str) else value
    if value not in BLOCK_TONES:
        raise _fail(where, f"{key} must be one of {', '.join(BLOCK_TONES)}, not {value!r}")
    return str(value)


def _when(raw: Mapping[str, Any], key: str, where: str, *, need: bool = False) -> str | None:
    value = _str(raw, key, where, VALUE, need=need)
    if value is None:
        return None
    ok = _ISO.match(value) is not None
    if ok:
        try:
            datetime.fromisoformat(value)
        except ValueError:
            ok = False
    if not ok:
        raise _fail(
            where,
            f"{key} {value!r} must be ISO 8601, like 2026-10-25T14:30 or 2026-10-25",
        )
    return value


def _count(raw: Mapping[str, Any], key: str, where: str) -> float | int:
    if raw.get(key) is None:
        raise _fail(where, f"{key} is required")
    return _number(raw[key], f"{where}: {key}")


def _words(
    raw: Mapping[str, Any], key: str, where: str, limit: int, nouns: tuple[str, str], cap: int
) -> list[str]:
    """A list of short strings, as `names` or `items`."""
    got = _array(raw, key, where, limit, nouns)
    return [
        str(_str({key: v}, key, f"{where}: {nouns[0]} {i}", cap, loose=True))
        for i, v in enumerate(got, 1)
    ]


def _value(raw: Mapping[str, Any], where: str, card: _Card) -> Block:
    out: Block = {"value": _str(raw, "value", where, VALUE, loose=True)}
    _put(out, "label", _str(raw, "label", where, LABEL, need=False, loose=True))
    _put(out, "delta", _str(raw, "delta", where, VALUE, need=False, loose=True))
    _put(out, "hint", _str(raw, "hint", where, TEXT, need=False, loose=True))
    _put(out, "tone", _tone(raw, where))
    return out


def _sparkline(raw: Mapping[str, Any], where: str, card: _Card) -> Block:
    values = _array(raw, "values", where, MAX_SPARK, ("value", "values"), low=MIN_SPARK)
    out: Block = {"values": [_number(v, f"{where}: values") for v in values]}
    _put(out, "tone", _tone(raw, where))
    return out


def _meter(raw: Mapping[str, Any], where: str, card: _Card) -> Block:
    out: Block = {"value": _count(raw, "value", where), "max": _count(raw, "max", where)}
    if out["max"] <= 0:
        raise _fail(where, f"max must be above 0, not {out['max']}")
    _put(out, "label", _str(raw, "label", where, LABEL, need=False, loose=True))
    shape = raw.get("shape")
    if shape is not None and shape not in SHAPES:
        raise _fail(where, f"shape must be one of {', '.join(SHAPES)}, not {shape!r}")
    _put(out, "shape", shape)
    _put(out, "tone", _tone(raw, where))
    return out


def _time(raw: Mapping[str, Any], where: str, card: _Card) -> Block:
    out: Block = {"at": _when(raw, "at", where, need=True)}
    _put(out, "end", _when(raw, "end", where))
    _put(out, "label", _str(raw, "label", where, LABEL, need=False))
    return out


def _chart_block(raw: Mapping[str, Any], where: str, card: _Card) -> Block:
    out = _chart(raw, where)
    out.pop("title", None)  # a card has one title; the block has none
    return out


def _table_block(raw: Mapping[str, Any], where: str, card: _Card) -> Block:
    out = _table(raw, where)
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
            raise _fail(
                where,
                f"highlight {highlight!r} is not a row; the table has {rows} {noun}"
                " (0 is the first)",
            )
        out["highlight"] = highlight
    return out


def _timeline_block(raw: Mapping[str, Any], where: str, card: _Card) -> Block:
    out = _timeline(raw, where)
    out.pop("title", None)
    return out


def _item(raw: Any, where: str) -> Block:
    raw = _obj(raw, where)
    out: Block = {"title": _str(raw, "title", where, TITLE)}
    _put(out, "detail", _str(raw, "detail", where, TEXT, need=False))
    _put(out, "person", _str(raw, "person", where, LABEL, need=False))
    _put(out, "status", _tone(raw, where, key="status"))
    _put(out, "when", _when(raw, "when", where))
    _put(out, "meta", _str(raw, "meta", where, TEXT, need=False, loose=True))
    _put(out, "chip", _str(raw, "chip", where, LABEL, need=False))
    done = raw.get("done")
    if done is not None and not isinstance(done, bool):
        raise _fail(where, "done must be true or false")
    _put(out, "done", done)
    return out


def _rows(raw: Mapping[str, Any], where: str, card: _Card) -> Block:
    out: Block = {}
    _put(out, "label", _str(raw, "label", where, LABEL, need=False))
    items = _array(raw, "items", where, MAX_ITEMS, ("item", "items"))
    card.items += len(items)
    if card.items > MAX_ITEMS:
        raise _fail(where, f"{card.items} items across the card, {MAX_ITEMS} at most")
    out["items"] = [_item(item, f"{where}: item {i}") for i, item in enumerate(items, 1)]
    return out


def _person(raw: Mapping[str, Any], where: str, card: _Card) -> Block:
    out: Block = {"name": _str(raw, "name", where, LABEL)}
    _put(out, "detail", _str(raw, "detail", where, TEXT, need=False))
    return out


def _people(raw: Mapping[str, Any], where: str, card: _Card) -> Block:
    return {"names": _words(raw, "names", where, MAX_PEOPLE, ("name", "names"), LABEL)}


def _status(raw: Mapping[str, Any], where: str, card: _Card) -> Block:
    return {"tone": _tone(raw, where, need=True), "text": _str(raw, "text", where, TEXT)}


def _chips(raw: Mapping[str, Any], where: str, card: _Card) -> Block:
    out: Block = {"items": _words(raw, "items", where, MAX_CHIPS, ("item", "items"), LABEL)}
    _put(out, "tone", _tone(raw, where))
    return out


def _long(raw: Mapping[str, Any], where: str, limit: int) -> str:
    """Text that keeps its lines. Refused, not cut: a clipped paragraph would mislead."""
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        raise _fail(where, "text is required")
    text = "\n".join(line.rstrip() for line in text.strip().splitlines())
    if len(text) > limit:
        raise _fail(where, f"text is {len(text)} characters, {limit} at most")
    return text


def _text(raw: Mapping[str, Any], where: str, card: _Card) -> Block:
    out: Block = {"text": _long(raw, where, NOTE)}
    _put(out, "tone", _tone(raw, where))
    return out


def _quote(raw: Mapping[str, Any], where: str, card: _Card) -> Block:
    out: Block = {"text": _long(raw, where, QUOTE)}
    _put(out, "by", _str(raw, "by", where, LABEL, need=False))
    _put(out, "when", _when(raw, "when", where))
    return out


def _facts(raw: Mapping[str, Any], where: str, card: _Card) -> Block:
    items = []
    for i, item in enumerate(_array(raw, "items", where, MAX_FACTS, ("item", "items")), 1):
        at = f"{where}: item {i}"
        item = _obj(item, at)
        items.append(
            {
                "label": _str(item, "label", at, LABEL, loose=True),
                "value": _str(item, "value", at, VALUE, loose=True),
            }
        )
    return {"items": items}


def _columns(raw: Mapping[str, Any], where: str, card: _Card) -> Block:
    columns = _array(raw, "columns", where, MAX_COLUMNS, ("column", "columns"), low=MIN_COLUMNS)
    drawn = []
    for i, column in enumerate(columns, 1):
        at = f"{where}: column {i}"
        if not isinstance(column, list):
            raise _fail(where, f"column {i} must be a list of blocks")
        drawn.append(_blocks(column, at, card, inside=True))
    return {"columns": drawn}


_BLOCKS: dict[str, Callable[[Mapping[str, Any], str, _Card], Block]] = {
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
    "quote": _quote,
    "facts": _facts,
    "columns": _columns,
}
BLOCK_TYPES = tuple(_BLOCKS)


def _blocks(raw: Any, where: str, card: _Card, *, inside: bool) -> list[Block]:
    if not isinstance(raw, list):
        raise _fail(where, "blocks must be a list")
    listed = _array({"blocks": raw}, "blocks", where, MAX_BLOCKS, ("block", "blocks"))
    out = []
    for i, given in enumerate(listed, 1):
        given = _obj(given, f"{where}: block {i}")
        kind = given.get("type")
        name = kind.strip().lower() if isinstance(kind, str) else kind
        build = _BLOCKS.get(name) if isinstance(name, str) else None
        if build is None:
            what = f"unknown type {kind!r}" if kind is not None else "no type"
            raise _fail(f"{where}: block {i}", f"{what}; use one of {', '.join(BLOCK_TYPES)}")
        at = f"{where}: block {i} ({name})"
        if inside and name == "columns":
            raise _fail(at, "columns cannot hold columns")
        out.append({"type": name, **build(_scrub(given), at, card)})
    return out


def validate_blocks(raw: Any, where: str) -> list[Block]:
    """The page-ready blocks of one card. `where` names the widget, as the kit does
    ("widget 2 (custom)"), and leads every message."""
    return _blocks(raw, where, _Card(), inside=False)
