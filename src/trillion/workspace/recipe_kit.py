"""Her newer widget types, and what every widget may carry (the widget design system
spec, "Recipes").

Pure, no I/O, and the kit's rules: what she wrote is checked and kept under the spec's
field names, anything else dropped, and the first problem raises `BoardError` naming
the widget, the field and the limit. `recipes.py` turns what is kept into blocks.
`widgets.validate_widget` is the one way in.

Times are ISO 8601, as in blocks. A thread's `due` and a goal's `by` may also be words
("Friday", "30 Nov"), kept as written: she often knows a deadline only as a day.
`is_date` is the one rule that tells the two apart, here and in `recipes.py`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any, cast

from trillion.workspace.blocks import (
    MAX_BLOCKS,
    MAX_PEOPLE,
    QUOTE,
    Card,
    ends_before,
    facts_block,
    iso_field,
    number_field,
    quote_block,
    tone_field,
    validate_blocks,
    word_list,
)
from trillion.workspace.kit import (
    LABEL,
    MAX_COLUMNS,
    MAX_ITEMS,
    TEXT,
    TITLE,
    VALUE,
    Widget,
    as_object,
    fail,
    list_field,
    long_text,
    put,
    text_field,
)

SOURCE = 30
ASK, MAX_ASKS = 40, 2
CALLOUT = 200
# A decision is drawn as a table: one row per option, and the option and the total
# take two of its columns.
MIN_CHOICES, MAX_CHOICES = 2, 8
MAX_CRITERIA = MAX_COLUMNS - 2

# Starts like an ISO date (2026-…), so it must be one; anything else is words.
_DATE = re.compile(r"^\d{4}-\d")
# A free-time slot written with Z or an offset.
_ZONED = re.compile(r"(Z|[+-]\d{2}:\d{2})$")


def is_date(text: str) -> bool:
    """Whether a deadline is an ISO date (checked, then shown as a date) rather than
    words (kept as written)."""
    return _DATE.match(text) is not None


def source(raw: Mapping[str, Any], where: str) -> Widget | None:
    """Where the widget's facts came from: a name she gives. When is the server's to
    say, so an `at` she sends is dropped."""
    value = raw.get("source")
    if value is None:
        return None
    if isinstance(value, str):
        value = {"name": value}
    if not isinstance(value, Mapping):
        raise fail(where, 'source must be a name, like "Calendar", or {name}')
    name = text_field(
        value, "name", f"{where}: source", SOURCE, need=isinstance(raw["source"], Mapping)
    )
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
        raise fail(where, "asks must be a list")
    kept = []
    for i, ask in enumerate(value, 1):
        if not isinstance(ask, str):
            raise fail(where, f"ask {i} must be a string")
        ask = " ".join(ask.split())
        if len(ask) > ASK:
            raise fail(where, f"ask {i} is {len(ask)} characters, {ASK} at most")
        if ask:
            kept.append(ask)
    if len(kept) > MAX_ASKS:
        raise fail(where, f"{len(kept)} asks, {MAX_ASKS} at most")
    return kept or None


def _done(raw: Mapping[str, Any], where: str) -> bool | None:
    done = raw.get("done")
    if done is not None and not isinstance(done, bool):
        raise fail(where, "done must be true or false")
    return done


def _whole(raw: Mapping[str, Any], key: str, where: str, *, need: bool = True) -> int | None:
    value = raw.get(key)
    if value is None:
        if need:
            raise fail(where, f"{key} is required")
        return None
    whole = isinstance(value, int) or (isinstance(value, float) and value.is_integer())
    if isinstance(value, bool) or not whole or value < 0:
        raise fail(where, f"{key} must be a whole number, 0 or more, not {value!r}")
    return int(value)


def _day_or_words(raw: Mapping[str, Any], key: str, where: str) -> str | None:
    value = text_field(raw, key, where, VALUE, need=False)
    if value is not None and is_date(value):
        return iso_field(raw, key, where)
    return value


def _list(
    raw: Mapping[str, Any], key: str, where: str, noun: str, limit: int = MAX_ITEMS
) -> list[tuple[str, Mapping[str, Any]]]:
    """Each entry of a list of objects, with the words that name it ("slot 2")."""
    out = []
    for i, entry in enumerate(list_field(raw, key, where, limit, (noun, key)), 1):
        at = f"{where}: {noun} {i}"
        out.append((at, as_object(entry, at)))
    return out


# One function per type: (raw, where) -> the type's own fields.


def _next_up(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {
        "event": text_field(raw, "event", where, LABEL),
        "start": iso_field(raw, "start", where, need=True),
    }
    end = iso_field(raw, "end", where)
    if end is not None and ends_before(end, out["start"]):
        raise fail(where, f"end {end} is before start {out['start']}")
    put(out, "end", end)
    put(out, "where", text_field(raw, "where", where, LABEL, need=False))
    if raw.get("people"):
        out["people"] = word_list(raw, "people", where, MAX_PEOPLE, ("person", "people"), LABEL)
    put(out, "note", text_field(raw, "note", where, TEXT, need=False))
    return out


def _free_time(raw: Mapping[str, Any], where: str) -> Widget:
    slots = []
    for at, slot in _list(raw, "slots", where, "slot"):
        start = cast(str, iso_field(slot, "start", at, need=True))
        end = cast(str, iso_field(slot, "end", at, need=True))
        if "T" not in start or "T" not in end:
            raise fail(at, "start and end need a time, like 2026-10-28T09:00")
        # Shown as written, so a zone would be shown wrong.
        if _ZONED.search(start) or _ZONED.search(end):
            raise fail(at, "write slots in the user's local time, like 2026-10-28T09:00")
        # Grouped by the day as written, so a slot belongs to one day.
        if start[:10] != end[:10]:
            raise fail(at, "it ends on another day; give one slot per day")
        if end[11:] <= start[11:]:
            raise fail(at, f"end {end} is not after start {start}")
        slots.append({"start": start, "end": end})
    return {"slots": slots}


def _inbox(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {"unread": _whole(raw, "unread", where)}
    put(out, "needs_reply", _whole(raw, "needs_reply", where, need=False))
    if raw.get("threads"):
        threads = []
        for at, thread in _list(raw, "threads", where, "thread"):
            one: Widget = {
                "from": text_field(thread, "from", at, LABEL),
                "subject": text_field(thread, "subject", at, TITLE),
            }
            put(one, "when", iso_field(thread, "when", at))
            put(one, "flag", tone_field(thread, at, key="flag"))
            threads.append(one)
        out["threads"] = threads
    return out


def _thread(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {
        "from": text_field(raw, "from", where, LABEL),
        "subject": text_field(raw, "subject", where, TITLE),
        "ask": text_field(raw, "ask", where, TEXT),
    }
    put(out, "due", _day_or_words(raw, "due", where))
    put(out, "when", iso_field(raw, "when", where))
    if raw.get("excerpt") is not None:
        out["excerpt"] = long_text(raw, where, QUOTE, key="excerpt")
    return out


def _mentions(raw: Mapping[str, Any], where: str) -> Widget:
    groups, total = [], 0
    for at, group in _list(raw, "groups", where, "group", MAX_BLOCKS):
        items = []
        for item_at, item in _list(group, "items", at, "item"):
            one: Widget = {
                "from": text_field(item, "from", item_at, LABEL),
                "text": text_field(item, "text", item_at, TEXT),
            }
            put(one, "when", iso_field(item, "when", item_at))
            items.append(one)
        total += len(items)
        if total > MAX_ITEMS:
            raise fail(where, f"{total} messages across the groups, {MAX_ITEMS} at most")
        groups.append({"channel": text_field(group, "channel", at, LABEL), "items": items})
    return {"groups": groups}


def _files(raw: Mapping[str, Any], where: str) -> Widget:
    files = []
    for at, file in _list(raw, "files", where, "file"):
        one: Widget = {"name": text_field(file, "name", at, TITLE)}
        put(one, "kind", text_field(file, "kind", at, LABEL, need=False))
        put(one, "edited", iso_field(file, "edited", at))
        put(one, "by", text_field(file, "by", at, LABEL, need=False))
        files.append(one)
    return {"files": files}


def _quote_recipe(raw: Mapping[str, Any], where: str) -> Widget:
    return quote_block(raw, where, Card())


def _plan(raw: Mapping[str, Any], where: str) -> Widget:
    steps = []
    for at, step in _list(raw, "steps", where, "step"):
        one: Widget = {"title": text_field(step, "title", at, TITLE)}
        put(one, "done", _done(step, at))
        put(one, "detail", text_field(step, "detail", at, TEXT, need=False))
        steps.append(one)
    return {"steps": steps}


def _choices(raw: Mapping[str, Any], where: str) -> Mapping[str, Any]:
    """Options as names. `{name}` objects are taken; a compare's options are not."""
    given = raw.get("options")
    if not isinstance(given, list):
        return raw
    names = []
    for i, option in enumerate(given, 1):
        if isinstance(option, Mapping):
            if "metrics" in option:
                raise fail(
                    where,
                    f"option {i} has metrics, a compare's shape; a decision takes options as"
                    " names, criteria, and scores with one row per option",
                )
            option = option.get("name")
        names.append(option)
    return {**raw, "options": names}


def _decision(raw: Mapping[str, Any], where: str) -> Widget:
    raw = _choices(raw, where)
    given = raw.get("options")
    if isinstance(given, list) and not MIN_CHOICES <= len(given) <= MAX_CHOICES:
        noun = "option" if len(given) == 1 else "options"
        raise fail(where, f"{len(given)} {noun}; a decision takes {MIN_CHOICES} to {MAX_CHOICES}")
    options = word_list(raw, "options", where, MAX_CHOICES, ("option", "options"), LABEL)
    twice = sorted({o for o in options if options.count(o) > 1})
    if twice:
        raise fail(where, f"option {twice[0]!r} is named twice; names must differ")
    criteria = word_list(raw, "criteria", where, MAX_CRITERIA, ("criterion", "criteria"), LABEL)
    scores = raw.get("scores")
    if not isinstance(scores, list):
        raise fail(where, "scores must be a list")
    if len(scores) != len(options):
        rows = "row" if len(scores) == 1 else "rows"
        raise fail(
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
            raise fail(where, f"{at} {got} but there {there}")
        for criterion, score in zip(criteria, row, strict=True):
            bad = isinstance(score, bool) or not isinstance(score, (int, float))
            if bad or not 0 <= score <= 10:
                raise fail(where, f"{at}: {criterion} must be a number from 0 to 10, not {score!r}")
        table.append(list(row))
    out: Widget = {"options": options, "criteria": criteria, "scores": table}
    pick = text_field(raw, "pick", where, LABEL, need=False)
    if pick is not None and pick not in options:
        raise fail(where, f"pick {pick!r} is not an option; use one of {', '.join(options)}")
    put(out, "pick", pick)
    put(out, "reason", text_field(raw, "reason", where, TEXT, need=False))
    return out


def _facts_recipe(raw: Mapping[str, Any], where: str) -> Widget:
    return facts_block(raw, where, Card())


def _callout(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {"text": long_text(raw, where, CALLOUT)}
    put(out, "tone", tone_field(raw, where))
    return out


def _goal(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {
        "label": text_field(raw, "label", where, LABEL, loose=True),
        "value": number_field(raw, "value", where),
        "target": number_field(raw, "target", where),
    }
    if out["target"] <= 0:
        raise fail(where, f"target must be above 0, not {out['target']}")
    put(out, "unit", text_field(raw, "unit", where, VALUE, need=False))
    put(out, "by", _day_or_words(raw, "by", where))
    return out


def _sources(raw: Mapping[str, Any], where: str) -> Widget:
    items = []
    for at, item in _list(raw, "items", where, "item"):
        one: Widget = {
            "title": text_field(item, "title", at, TITLE),
            "site": text_field(item, "site", at, LABEL),
        }
        put(one, "note", text_field(item, "note", at, TEXT, need=False))
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
