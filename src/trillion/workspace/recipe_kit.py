"""Her newer widget types, and what every widget may carry (the widget design system
spec, "Recipes").

Pure, no I/O, and the kit's rules: what she wrote is checked and kept under the spec's
field names, anything else dropped, and the first problem raises `BoardError` naming
the widget, the field and the limit. `recipes.py` turns what is kept into blocks.
`kit.validate_widget` is the one way in; this module is its second half, apart only
because the new types are built on blocks, and blocks on the kit's helpers.

Times are ISO 8601, as in blocks. A thread's `due` and a goal's `by` may also be words
("Friday"), kept as written: she often knows a deadline only as a day.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any, cast

from trillion.workspace.blocks import (
    MAX_BLOCKS,
    MAX_PEOPLE,
    QUOTE,
    _before,
    _Card,
    _count,
    _facts,
    _long,
    _quote,
    _tone,
    _when,
    _words,
    validate_blocks,
)
from trillion.workspace.kit import (
    LABEL,
    MAX_COLUMNS,
    MAX_ITEMS,
    TEXT,
    TITLE,
    VALUE,
    Widget,
    _array,
    _fail,
    _obj,
    _put,
    _str,
)

SOURCE = 30
ASK, MAX_ASKS = 40, 2
CALLOUT = 200
# A decision is drawn as a table: one row per option, and the option and the total
# take two of its columns.
MIN_CHOICES, MAX_CHOICES = 2, 8
MAX_CRITERIA = MAX_COLUMNS - 2

# Looks like a date, so it must be one; anything else is a deadline in words.
_DATEISH = re.compile(r"^\d{4}-\d")


def source(raw: Mapping[str, Any], where: str) -> Widget | None:
    """Where the widget's facts came from: a name she gives. When is the server's to
    say, so an `at` she sends is dropped."""
    value = raw.get("source")
    if value is None:
        return None
    if isinstance(value, str):
        value = {"name": value}
    if not isinstance(value, Mapping):
        raise _fail(where, 'source must be a name, like "Calendar", or {name}')
    name = _str(value, "name", f"{where}: source", SOURCE, need=isinstance(raw["source"], Mapping))
    return None if name is None else {"name": name}


def asks(raw: Mapping[str, Any], where: str) -> list[str] | None:
    """Up to two questions the user can tap to send. Refused rather than cut: a tap
    sends exactly these words, so a clipped one would ask something else."""
    value = raw.get("asks")
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise _fail(where, "asks must be a list")
    kept = []
    for i, ask in enumerate(value, 1):
        if not isinstance(ask, str):
            raise _fail(where, f"ask {i} must be a string")
        ask = " ".join(ask.split())
        if len(ask) > ASK:
            raise _fail(where, f"ask {i} is {len(ask)} characters, {ASK} at most")
        if ask:
            kept.append(ask)
    if len(kept) > MAX_ASKS:
        raise _fail(where, f"{len(kept)} asks, {MAX_ASKS} at most")
    return kept or None


def _done(raw: Mapping[str, Any], where: str) -> bool | None:
    done = raw.get("done")
    if done is not None and not isinstance(done, bool):
        raise _fail(where, "done must be true or false")
    return done


def _whole(raw: Mapping[str, Any], key: str, where: str, *, need: bool = True) -> int | None:
    value = raw.get(key)
    if value is None:
        if need:
            raise _fail(where, f"{key} is required")
        return None
    whole = isinstance(value, int) or (isinstance(value, float) and value.is_integer())
    if isinstance(value, bool) or not whole or value < 0:
        raise _fail(where, f"{key} must be a whole number, 0 or more, not {value!r}")
    return int(value)


def _day_or_words(raw: Mapping[str, Any], key: str, where: str) -> str | None:
    value = _str(raw, key, where, VALUE, need=False)
    if value is not None and _DATEISH.match(value):
        return _when(raw, key, where)
    return value


def _passage(raw: Mapping[str, Any], key: str, where: str, limit: int) -> str | None:
    """Text that keeps its lines, as a block's; refused, not cut, when too long."""
    text = raw.get(key)
    if text is None:
        return None
    if not isinstance(text, str):
        raise _fail(where, f"{key} must be a string")
    text = "\n".join(line.strip() for line in text.strip().splitlines())
    if len(text) > limit:
        raise _fail(where, f"{key} is {len(text)} characters, {limit} at most")
    return text or None


def _list(
    raw: Mapping[str, Any], key: str, where: str, noun: str, limit: int = MAX_ITEMS
) -> list[tuple[str, Mapping[str, Any]]]:
    """Each entry of a list of objects, with the words that name it ("slot 2")."""
    out = []
    for i, entry in enumerate(_array(raw, key, where, limit, (noun, key)), 1):
        at = f"{where}: {noun} {i}"
        out.append((at, _obj(entry, at)))
    return out


# One function per type: (raw, where) -> the type's own fields.


def _next_up(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {
        "event": _str(raw, "event", where, LABEL),
        "start": _when(raw, "start", where, need=True),
    }
    end = _when(raw, "end", where)
    if end is not None and _before(end, out["start"]):
        raise _fail(where, f"end {end} is before start {out['start']}")
    _put(out, "end", end)
    _put(out, "where", _str(raw, "where", where, LABEL, need=False))
    if raw.get("people"):
        out["people"] = _words(raw, "people", where, MAX_PEOPLE, ("person", "people"), LABEL)
    _put(out, "note", _str(raw, "note", where, TEXT, need=False))
    return out


def _free_time(raw: Mapping[str, Any], where: str) -> Widget:
    slots = []
    for at, slot in _list(raw, "slots", where, "slot"):
        start = cast(str, _when(slot, "start", at, need=True))
        end = cast(str, _when(slot, "end", at, need=True))
        if "T" not in start or "T" not in end:
            raise _fail(at, "start and end need a time, like 2026-10-28T09:00")
        # Grouped by the day as written, so a slot belongs to one day.
        if start[:10] != end[:10]:
            raise _fail(at, "it ends on another day; give one slot per day")
        if end[11:16] < start[11:16]:
            raise _fail(at, f"end {end} is before start {start}")
        slots.append({"start": start, "end": end})
    return {"slots": slots}


def _inbox(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {"unread": _whole(raw, "unread", where)}
    _put(out, "needs_reply", _whole(raw, "needs_reply", where, need=False))
    if raw.get("threads"):
        threads = []
        for at, thread in _list(raw, "threads", where, "thread"):
            one: Widget = {
                "from": _str(thread, "from", at, LABEL),
                "subject": _str(thread, "subject", at, TITLE),
            }
            _put(one, "when", _when(thread, "when", at))
            _put(one, "flag", _tone(thread, at, key="flag"))
            threads.append(one)
        out["threads"] = threads
    return out


def _thread(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {
        "from": _str(raw, "from", where, LABEL),
        "subject": _str(raw, "subject", where, TITLE),
        "ask": _str(raw, "ask", where, TEXT),
    }
    _put(out, "due", _day_or_words(raw, "due", where))
    _put(out, "when", _when(raw, "when", where))
    _put(out, "excerpt", _passage(raw, "excerpt", where, QUOTE))
    return out


def _mentions(raw: Mapping[str, Any], where: str) -> Widget:
    groups, total = [], 0
    for at, group in _list(raw, "groups", where, "group", MAX_BLOCKS):
        items = []
        for item_at, item in _list(group, "items", at, "item"):
            one: Widget = {
                "from": _str(item, "from", item_at, LABEL),
                "text": _str(item, "text", item_at, TEXT),
            }
            _put(one, "when", _when(item, "when", item_at))
            items.append(one)
        total += len(items)
        if total > MAX_ITEMS:
            raise _fail(where, f"{total} messages across the groups, {MAX_ITEMS} at most")
        groups.append({"channel": _str(group, "channel", at, LABEL), "items": items})
    return {"groups": groups}


def _files(raw: Mapping[str, Any], where: str) -> Widget:
    files = []
    for at, file in _list(raw, "files", where, "file"):
        one: Widget = {"name": _str(file, "name", at, TITLE)}
        _put(one, "kind", _str(file, "kind", at, LABEL, need=False))
        _put(one, "edited", _when(file, "edited", at))
        _put(one, "by", _str(file, "by", at, LABEL, need=False))
        files.append(one)
    return {"files": files}


def _quote_recipe(raw: Mapping[str, Any], where: str) -> Widget:
    return _quote(raw, where, _Card())


def _plan(raw: Mapping[str, Any], where: str) -> Widget:
    steps = []
    for at, step in _list(raw, "steps", where, "step"):
        one: Widget = {"title": _str(step, "title", at, TITLE)}
        _put(one, "done", _done(step, at))
        _put(one, "detail", _str(step, "detail", at, TEXT, need=False))
        steps.append(one)
    return {"steps": steps}


def _decision(raw: Mapping[str, Any], where: str) -> Widget:
    given = raw.get("options")
    if isinstance(given, list) and not MIN_CHOICES <= len(given) <= MAX_CHOICES:
        noun = "option" if len(given) == 1 else "options"
        raise _fail(where, f"{len(given)} {noun}; a decision takes {MIN_CHOICES} to {MAX_CHOICES}")
    options = _words(raw, "options", where, MAX_CHOICES, ("option", "options"), LABEL)
    twice = sorted({o for o in options if options.count(o) > 1})
    if twice:
        raise _fail(where, f"option {twice[0]!r} is named twice; names must differ")
    criteria = _words(raw, "criteria", where, MAX_CRITERIA, ("criterion", "criteria"), LABEL)
    scores = raw.get("scores")
    if not isinstance(scores, list):
        raise _fail(where, "scores must be a list")
    if len(scores) != len(options):
        rows = "row" if len(scores) == 1 else "rows"
        raise _fail(
            where,
            f"scores has {len(scores)} {rows} but there are {len(options)} options;"
            " give one row per option",
        )
    table = []
    for i, (option, row) in enumerate(zip(options, scores, strict=True), 1):
        at = f"scores row {i} ({option})"
        if not isinstance(row, list) or len(row) != len(criteria):
            got = f"has {len(row)} scores" if isinstance(row, list) else "is not a list"
            there = "is 1 criterion" if len(criteria) == 1 else f"are {len(criteria)} criteria"
            raise _fail(where, f"{at} {got} but there {there}")
        for criterion, score in zip(criteria, row, strict=True):
            bad = isinstance(score, bool) or not isinstance(score, (int, float))
            if bad or not 0 <= score <= 10:
                raise _fail(
                    where, f"{at}: {criterion} must be a number from 0 to 10, not {score!r}"
                )
        table.append(list(row))
    out: Widget = {"options": options, "criteria": criteria, "scores": table}
    pick = _str(raw, "pick", where, LABEL, need=False)
    if pick is not None and pick not in options:
        raise _fail(where, f"pick {pick!r} is not an option; use one of {', '.join(options)}")
    _put(out, "pick", pick)
    _put(out, "reason", _str(raw, "reason", where, TEXT, need=False))
    return out


def _facts_recipe(raw: Mapping[str, Any], where: str) -> Widget:
    return _facts(raw, where, _Card())


def _callout(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {"text": _long(raw, where, CALLOUT)}
    _put(out, "tone", _tone(raw, where))
    return out


def _goal(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {
        "label": _str(raw, "label", where, LABEL, loose=True),
        "value": _count(raw, "value", where),
        "target": _count(raw, "target", where),
    }
    if out["target"] <= 0:
        raise _fail(where, f"target must be above 0, not {out['target']}")
    _put(out, "unit", _str(raw, "unit", where, VALUE, need=False))
    _put(out, "by", _day_or_words(raw, "by", where))
    return out


def _sources(raw: Mapping[str, Any], where: str) -> Widget:
    items = []
    for at, item in _list(raw, "items", where, "item"):
        one: Widget = {
            "title": _str(item, "title", at, TITLE),
            "site": _str(item, "site", at, LABEL),
        }
        _put(one, "note", _str(item, "note", at, TEXT, need=False))
        items.append(one)
    return {"items": items}


def _custom(raw: Mapping[str, Any], where: str) -> Widget:
    return {"blocks": validate_blocks(raw.get("blocks"), where)}


RECIPES: dict[str, Callable[[Mapping[str, Any], str], Widget]] = {
    "next_up": _next_up,
    "free_time": _free_time,
    "inbox": _inbox,
    "thread": _thread,
    "mentions": _mentions,
    "files": _files,
    "quote": _quote_recipe,
    "plan": _plan,
    "decision": _decision,
    "facts": _facts_recipe,
    "callout": _callout,
    "goal": _goal,
    "sources": _sources,
    "custom": _custom,
}

# The page's grid: a glance is small, a table of choices or several channels wide.
SIZE = {
    "next_up": "small",
    "goal": "small",
    "callout": "small",
    "facts": "small",
    "quote": "small",
    "decision": "wide",
    "mentions": "wide",
}
