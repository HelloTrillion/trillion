"""The packs' widgets as she writes them (the widget packs spec): what a connector holds,
checked by the kit's rules, as `recipe_kit` checks the core recipes.

Pure, no I/O. `widgets.validate_widget` is the one way in; `pack_recipes.py` turns what
is kept into blocks. State words are read leniently: the words services use for the
same thing ("succeeded", "in_progress") mean the kit's own.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast

from trillion.workspace.blocks import (
    MAX_BARS,
    MAX_BLOCKS,
    MAX_FUNNEL,
    MAX_SPARK,
    MAX_SPLIT,
    MAX_STEPS,
    MIN_FUNNEL,
    MIN_SPLIT,
    MIN_STEPS,
    Card,
    clock_time,
    count_field,
    current_step,
    facts_block,
    iso_field,
    tone_field,
    word_list,
)
from trillion.workspace.kit import (
    LABEL,
    TEXT,
    TITLE,
    VALUE,
    Widget,
    fail,
    finite_number,
    list_field,
    put,
    text_field,
)
from trillion.workspace.recipe_kit import _done, _list, _numbers, _whole, is_date

# A kanban column lists a few cards; its count says how many there are in all.
MAX_CARDS = 5


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
    # Stripe's words for a payment still waiting on the customer, and one called off.
    "requires payment method": "pending",
    "requires action": "pending",
    "requires confirmation": "pending",
    "canceled": "failed",
    "cancelled": "failed",
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
    # GitHub's conclusions: a check that did not run is not failing.
    "completed": "passing",
    "timed out": "failing",
    "skipped": "pending",
    "cancelled": "pending",
    "canceled": "pending",
    "action required": "pending",
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
    "maintenance": "degraded",
    "under maintenance": "degraded",
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
    """The day a due item is grouped under: an ISO date's day, else the words in any
    case ("Next week" and "next week" are one day)."""
    return due[:10] if is_date(due) else " ".join(due.split()).casefold()


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


PACK_RECIPES: dict[str, Callable[[Mapping[str, Any], str], Widget]] = {
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
}

PACK_SIZE = {"kpis": "wide", "kanban": "wide"}
