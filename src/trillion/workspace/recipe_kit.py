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

from trillion.tools.web import has_address
from trillion.workspace.blocks import (
    MAX_BARS,
    MAX_BLOCKS,
    MAX_FUNNEL,
    MAX_PEOPLE,
    MAX_SPARK,
    MAX_SPLIT,
    MAX_STEPS,
    MIN_FUNNEL,
    MIN_SPLIT,
    MIN_STEPS,
    QUOTE,
    Card,
    clock_time,
    count_field,
    current_step,
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
    finite_number,
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
# A number written as text, as small models often send one: "12", "4,200", "2.5".
_NUMERIC = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
# A free-time slot written with Z or an offset.
_ZONED = re.compile(r"(Z|[+-]\d{2}:\d{2})$")
# A kanban column lists a few cards; its count says how many there are in all.
MAX_CARDS = 5


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


def asked(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    """The widget with a lone `ask` read as she meant it. Models write one ask in the
    singular ("ask": "Draft a reply to Sam"); it is an entry of `asks`. On a thread an
    `ask` with no `request` is what they want (as threads were first saved)."""
    ask = raw.get("ask")
    if ask is None:
        return raw
    rest = {k: v for k, v in raw.items() if k != "ask"}
    if raw.get("type") == "thread" and raw.get("request") is None:
        return {**rest, "request": ask}
    extra = [ask] if isinstance(ask, str) else ask if isinstance(ask, list) else None
    if extra is None:
        return rest
    given = raw.get("asks")
    if given is None:
        given = []
    elif isinstance(given, str):
        given = [given]
    elif not isinstance(given, list):
        return rest  # `asks` refuses its own shape
    return {**rest, "asks": [*given, *extra]}


def asks(raw: Mapping[str, Any], where: str) -> list[str] | None:
    """Up to two questions the user can tap to send. Refused rather than cut: a tap
    sends exactly these words, so a clipped one would ask something else. An ask holds
    no web address and never starts like a "[host]" line."""
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
        # A tap sends these words as the user's. An address in them would read as one
        # the user gave, so she could open it (and a page could have chosen it).
        if has_address(ask):
            raise fail(where, f"ask {i} can't hold a web address; put links in sources")
        # A line starting "[host]" is the app speaking; an ask is never that.
        if ask.startswith("["):
            raise fail(where, f'ask {i} can\'t start with "["; write it as the user would say it')
        if ask:
            kept.append(ask)
    if len(kept) > MAX_ASKS:
        raise fail(where, f"{len(kept)} asks, {MAX_ASKS} at most")
    return kept or None


def _numbers(raw: Mapping[str, Any], *keys: str) -> Mapping[str, Any]:
    """The widget with each of `keys` that is a number written as text ("12") read as
    the number. Anything else is left for the field's own check to refuse."""
    out = dict(raw)
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and _NUMERIC.match(value.strip()):
            plain = value.strip().replace(",", "")
            out[key] = float(plain) if "." in plain else int(plain)
    return out


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
        # A slot is on a day, and a bare time would put it on today without saying so.
        for key in ("start", "end"):
            given = slot.get(key)
            if isinstance(given, str) and clock_time(given) is not None:
                raise fail(
                    at,
                    f"{key} {given.strip()!r} has no date; a slot is on a day, so write it"
                    " like 2026-10-28T09:00",
                )
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
    raw = _numbers(raw, "unread", "needs_reply")
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
        "request": text_field(raw, "request", where, TEXT),
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
    raw = _numbers(raw, "value", "target")
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


# The packs' widgets (the widget packs spec): what a connector holds, as she writes it.


def _state(
    raw: Mapping[str, Any],
    key: str,
    where: str,
    states: tuple[str, ...],
    synonyms: Mapping[str, str],
    *,
    need: bool = True,
) -> str | None:
    """One of a few state words, read leniently: any case, and the words services use
    for the same thing ("succeeded" is paid, "in_progress" running)."""
    value = raw.get(key)
    if value is None:
        if need:
            raise fail(where, f"{key} is required ({', '.join(states)})")
        return None
    word = None
    if isinstance(value, str):
        word = " ".join(value.lower().replace("_", " ").replace("-", " ").split())
        word = synonyms.get(word, word)
    if word not in states:
        raise fail(where, f"{key} must be one of {', '.join(states)}, not {value!r}")
    return word


def _series(raw: Mapping[str, Any], key: str, where: str) -> list[float | int] | None:
    """A short run of numbers for a sparkline or a burndown. Fewer than two is kept
    and simply not drawn: a trend of one point is no trend, not a mistake."""
    if raw.get(key) is None:
        return None
    given = list_field(raw, key, where, MAX_SPARK, ("point", "points"), low=0)
    return [finite_number(v, f"{where}: {key}") for v in given]


def _when(raw: Mapping[str, Any], key: str, where: str, *, need: bool = False) -> str | None:
    """A day or a time: ISO 8601, a bare clock time (today, as everywhere), or words
    ("Friday", "end of month") kept as written."""
    value = text_field(raw, key, where, VALUE, need=need)
    if value is not None and (is_date(value) or clock_time(value) is not None):
        return iso_field(raw, key, where)
    return value


def _counted(entry: Mapping[str, Any], key: str, where: str, *, need: bool = True) -> int | None:
    return _whole(_numbers(entry, key), key, where, need=need)


def _kpis(raw: Mapping[str, Any], where: str) -> Widget:
    given = raw.get("metrics")
    if isinstance(given, list) and len(given) == 1:
        raise fail(where, "1 metric; kpis takes 2 to 4, and one number is a stat")
    metrics = []
    for at, metric in _list(raw, "metrics", where, "metric", MAX_SPLIT):
        one: Widget = {
            "label": text_field(metric, "label", at, LABEL, loose=True),
            "value": text_field(metric, "value", at, VALUE, loose=True),
        }
        put(one, "delta", text_field(metric, "delta", at, VALUE, need=False, loose=True))
        put(one, "tone", tone_field(metric, at))
        put(one, "trend", _series(metric, "trend", at))
        metrics.append(one)
    if len(metrics) < MIN_SPLIT:
        raise fail(where, f"metrics needs at least {MIN_SPLIT} metrics")
    return {"metrics": metrics}


def _tasks(raw: Mapping[str, Any], where: str) -> Widget:
    tasks = []
    for at, task in _list(raw, "tasks", where, "task"):
        one: Widget = {"title": text_field(task, "title", at, TITLE)}
        put(one, "status", text_field(task, "status", at, LABEL, need=False))
        put(one, "due", _when(task, "due", at))
        put(one, "owner", text_field(task, "owner", at, LABEL, need=False))
        put(one, "priority", text_field(task, "priority", at, LABEL, need=False, loose=True))
        put(one, "done", _done(task, at))
        tasks.append(one)
    return {"tasks": tasks}


PAID = ("paid", "failed", "refunded", "pending")
PAID_WORDS = {
    "succeeded": "paid",
    "success": "paid",
    "complete": "paid",
    "completed": "paid",
    "settled": "paid",
    "declined": "failed",
    "refund": "refunded",
    "processing": "pending",
}


def _transactions(raw: Mapping[str, Any], where: str) -> Widget:
    items = []
    for at, item in _list(raw, "items", where, "item"):
        one: Widget = {
            "who": text_field(item, "who", at, LABEL),
            "amount": text_field(item, "amount", at, VALUE, loose=True),
        }
        put(one, "what", text_field(item, "what", at, TEXT, need=False))
        put(one, "status", _state(item, "status", at, PAID, PAID_WORDS, need=False))
        put(one, "when", iso_field(item, "when", at))
        items.append(one)
    out: Widget = {"items": items}
    put(out, "total", text_field(raw, "total", where, VALUE, need=False, loose=True))
    return out


BILLED = ("overdue", "due", "paid")
BILLED_WORDS = {"late": "overdue", "past due": "overdue", "open": "due", "unpaid": "due"}


def _invoices(raw: Mapping[str, Any], where: str) -> Widget:
    items = []
    for at, item in _list(raw, "items", where, "item"):
        one: Widget = {
            "who": text_field(item, "who", at, LABEL),
            "amount": text_field(item, "amount", at, VALUE, loose=True),
            "due": _when(item, "due", at, need=True),
        }
        put(one, "status", _state(item, "status", at, BILLED, BILLED_WORDS, need=False))
        items.append(one)
    return {"items": items}


def _breakdown(raw: Mapping[str, Any], where: str) -> Widget:
    items = []
    for at, item in _list(raw, "items", where, "item", MAX_BARS):
        item = _numbers(item, "value")
        items.append(
            {
                "label": text_field(item, "label", at, LABEL, loose=True),
                "value": count_field(item, "value", at),
            }
        )
    out: Widget = {"items": items}
    put(out, "unit", text_field(raw, "unit", where, VALUE, need=False))
    return out


def _payouts(raw: Mapping[str, Any], where: str) -> Widget:
    items = []
    for at, item in _list(raw, "items", where, "item"):
        one: Widget = {
            "amount": text_field(item, "amount", at, VALUE, loose=True),
            "when": _when(item, "when", at, need=True),
        }
        put(one, "note", text_field(item, "note", at, TEXT, need=False))
        items.append(one)
    out: Widget = {"items": items}
    put(out, "total", text_field(raw, "total", where, VALUE, need=False, loose=True))
    return out


def _pipeline(raw: Mapping[str, Any], where: str) -> Widget:
    given = raw.get("stages")
    if isinstance(given, list) and len(given) < MIN_FUNNEL:
        raise fail(where, f"stages needs at least {MIN_FUNNEL} stages")
    stages = []
    for at, stage in _list(raw, "stages", where, "stage", MAX_FUNNEL):
        if isinstance(stage.get("name"), str) and stage.get("label") is None:
            stage = {**stage, "label": stage["name"]}
        one: Widget = {
            "label": text_field(stage, "label", at, LABEL, loose=True),
            "count": _counted(stage, "count", at),
        }
        put(one, "value", text_field(stage, "value", at, VALUE, need=False, loose=True))
        stages.append(one)
    return {"stages": stages}


def _deal(raw: Mapping[str, Any], where: str) -> Widget:
    given = raw.get("stages")
    if isinstance(given, list) and len(given) < MIN_STEPS:
        raise fail(where, f"stages needs at least {MIN_STEPS} stages")
    stages = word_list(raw, "stages", where, MAX_STEPS, ("stage", "stages"), LABEL)
    out: Widget = {"name": text_field(raw, "name", where, TITLE)}
    put(out, "amount", text_field(raw, "amount", where, VALUE, need=False, loose=True))
    out["stages"] = stages
    out["current"] = current_step(raw, stages, where)
    put(out, "owner", text_field(raw, "owner", where, LABEL, need=False))
    put(out, "next", text_field(raw, "next", where, VALUE, need=False))
    put(out, "close", _when(raw, "close", where))
    return out


def _contact(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {"name": text_field(raw, "name", where, LABEL)}
    put(out, "role", text_field(raw, "role", where, LABEL, need=False))
    put(out, "company", text_field(raw, "company", where, LABEL, need=False))
    if raw.get("facts"):
        out["facts"] = facts_block({"items": raw["facts"]}, f"{where}: facts", Card())["items"]
    if raw.get("activity"):
        activity = []
        for at, entry in _list(raw, "activity", where, "entry"):
            one: Widget = {"title": text_field(entry, "title", at, TITLE)}
            put(one, "when", iso_field(entry, "when", at))
            activity.append(one)
        out["activity"] = activity
    return out


# A campaign's numbers, in the order its funnel is drawn.
CAMPAIGN = ("sent", "delivered", "opened", "clicked", "replied")


def _campaign(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {}
    for key in CAMPAIGN:
        put(out, key, _counted(raw, key, where, need=key == "sent"))
    if len(out) < MIN_FUNNEL:
        raise fail(where, "give sent and at least one of delivered, opened, clicked, replied")
    return out


def _support_queue(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {"open": _counted(raw, "open", where)}
    if raw.get("waiting"):
        waiting = []
        for at, entry in _list(raw, "waiting", where, "ticket"):
            waiting.append(
                {
                    "who": text_field(entry, "who", at, LABEL),
                    "subject": text_field(entry, "subject", at, TITLE),
                    "since": iso_field(entry, "since", at, need=True),
                }
            )
        out["waiting"] = waiting
    put(out, "first_reply", text_field(raw, "first_reply", where, VALUE, need=False, loose=True))
    return out


CHECKED = ("passing", "failing", "running", "pending")
CHECKED_WORDS = {
    "pass": "passing",
    "passed": "passing",
    "success": "passing",
    "succeeded": "passing",
    "green": "passing",
    "fail": "failing",
    "failed": "failing",
    "failure": "failing",
    "error": "failing",
    "red": "failing",
    "in progress": "running",
    "queued": "pending",
    "waiting": "pending",
}


def _checks(raw: Mapping[str, Any], where: str) -> Widget:
    items = []
    for at, item in _list(raw, "items", where, "item"):
        one: Widget = {
            "title": text_field(item, "title", at, TITLE),
            "state": _state(item, "state", at, CHECKED, CHECKED_WORDS),
        }
        put(one, "who", text_field(item, "who", at, LABEL, need=False))
        put(one, "when", iso_field(item, "when", at))
        items.append(one)
    return {"items": items}


def _errors(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {}
    put(out, "trend", _series(raw, "trend", where))
    issues = []
    for at, issue in _list(raw, "issues", where, "issue", MAX_BARS):
        one: Widget = {
            "title": text_field(issue, "title", at, LABEL),
            "events": _counted(issue, "events", at),
        }
        put(one, "users", _counted(issue, "users", at, need=False))
        issues.append(one)
    out["issues"] = issues
    return out


DEPLOYED = ("ready", "building", "failed", "cancelled")
DEPLOYED_WORDS = {
    "success": "ready",
    "succeeded": "ready",
    "deployed": "ready",
    "live": "ready",
    "queued": "building",
    "deploying": "building",
    "in progress": "building",
    "initializing": "building",
    "error": "failed",
    "failure": "failed",
    "canceled": "cancelled",
}


def _deploys(raw: Mapping[str, Any], where: str) -> Widget:
    items = []
    for at, item in _list(raw, "items", where, "item"):
        one: Widget = {
            "title": text_field(item, "title", at, TITLE),
            "state": _state(item, "state", at, DEPLOYED, DEPLOYED_WORDS),
        }
        put(one, "branch", text_field(item, "branch", at, LABEL, need=False))
        put(one, "when", iso_field(item, "when", at))
        items.append(one)
    return {"items": items}


SERVING = ("up", "degraded", "down")
SERVING_WORDS = {
    "operational": "up",
    "ok": "up",
    "healthy": "up",
    "online": "up",
    "partial outage": "degraded",
    "degraded performance": "degraded",
    "slow": "degraded",
    "major outage": "down",
    "outage": "down",
    "offline": "down",
}


def _services(raw: Mapping[str, Any], where: str) -> Widget:
    items = []
    for at, item in _list(raw, "items", where, "item"):
        one: Widget = {
            "name": text_field(item, "name", at, LABEL),
            "state": _state(item, "state", at, SERVING, SERVING_WORDS),
        }
        put(one, "note", text_field(item, "note", at, TEXT, need=False))
        items.append(one)
    return {"items": items}


def _card_title(card: Any, at: str) -> str:
    """A kanban card: its title, or the card itself when she wrote just the words."""
    if isinstance(card, Mapping):
        return cast(str, text_field(card, "title", at, TITLE, loose=True))
    return cast(str, text_field({"title": card}, "title", at, TITLE, loose=True))


def _kanban(raw: Mapping[str, Any], where: str) -> Widget:
    given = raw.get("columns")
    if isinstance(given, list) and len(given) == 1:
        raise fail(where, f"columns needs at least {MIN_SPLIT} columns")
    columns = []
    for at, column in _list(raw, "columns", where, "column", MAX_SPLIT):
        one: Widget = {"name": text_field(column, "name", at, LABEL, loose=True)}
        put(one, "count", _counted(column, "count", at, need=False))
        cards = column.get("items")
        cards = [] if cards is None else cards
        if not isinstance(cards, list):
            raise fail(at, "items must be a list of titles")
        if len(cards) > MAX_CARDS:
            raise fail(
                at, f"{len(cards)} items, {MAX_CARDS} at most; give count for how many in all"
            )
        one["items"] = [_card_title(c, f"{at}: item {j}") for j, c in enumerate(cards, 1)]
        columns.append(one)
    return {"columns": columns}


def _sprint(raw: Mapping[str, Any], where: str) -> Widget:
    out: Widget = {}
    put(out, "name", text_field(raw, "name", where, TITLE, need=False))
    done, total = _counted(raw, "done", where), _counted(raw, "total", where)
    if not total:
        raise fail(where, "total must be above 0")
    if cast(int, done) > total:
        raise fail(where, f"done {done} is more than total {total}")
    out.update(done=done, total=total)
    put(out, "ends", _when(raw, "ends", where))
    put(out, "burndown", _series(raw, "burndown", where))
    return out


def due_key(due: str) -> str:
    """The day a due item is grouped under: an ISO date's day, else the words as written."""
    return due[:10] if is_date(due) else due


def _due_soon(raw: Mapping[str, Any], where: str) -> Widget:
    items = []
    for at, item in _list(raw, "items", where, "item"):
        one: Widget = {"title": text_field(item, "title", at, TITLE)}
        one["due"] = _when(item, "due", at, need=True)
        put(one, "owner", text_field(item, "owner", at, LABEL, need=False))
        items.append(one)
    days = len({due_key(i["due"]) for i in items})
    if days > MAX_BLOCKS:
        raise fail(where, f"the items fall on {days} days, {MAX_BLOCKS} at most")
    return {"items": items}


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
    "kpis": _kpis,
    "tasks": _tasks,
    "transactions": _transactions,
    "invoices": _invoices,
    "breakdown": _breakdown,
    "payouts": _payouts,
    "pipeline": _pipeline,
    "deal": _deal,
    "contact": _contact,
    "campaign": _campaign,
    "support_queue": _support_queue,
    "checks": _checks,
    "errors": _errors,
    "deploys": _deploys,
    "services": _services,
    "kanban": _kanban,
    "sprint": _sprint,
    "due_soon": _due_soon,
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
    "kpis": "wide",
    "kanban": "wide",
}
