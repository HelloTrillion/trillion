"""The packs' recipes: a saved pack widget turned into the blocks the page draws, as
`recipes.py` does for the core ones (the widget packs spec, "New widgets").

Pure, no I/O, and nothing here refuses: the input is a widget `pack_kit` already took.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta

from trillion.workspace import blocks
from trillion.workspace.blocks import Block
from trillion.workspace.kit import Widget, cap
from trillion.workspace.pack_kit import CAMPAIGN, due_key
from trillion.workspace.recipe_kit import is_date
from trillion.workspace.wording import amount as _amount
from trillion.workspace.wording import day as _day
from trillion.workspace.wording import deadline as _deadline
from trillion.workspace.wording import with_fields as _with

# Money as she writes it: "$1,200", "-€85.50", "4200".
# Cents are one or two digits, so "€1.200" (a thousand, written the European way) is not
# read as 1.2.
_MONEY = re.compile(r"^(-)?\s*([$€£])?\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d{1,2})?$")


def _sum_of(amounts: Iterable[str]) -> str | None:
    """Amounts added up and written as they were ("$2,050"), or None when one is not a
    plain amount or they are in different currencies: a wrong total is worse than none."""
    total, units, cents = 0.0, set(), False
    for text in amounts:
        match = _MONEY.match(text.strip())
        if match is None:
            return None
        sign, unit, whole, part = match.groups()
        units.add(unit or "")
        cents = cents or bool(part)
        total += (-1 if sign else 1) * float(whole.replace(",", "") + (part or ""))
    if len(units) != 1:
        return None
    (unit,) = units
    if cents:
        body = f"{abs(total):,.2f}"
        return f"{'-' if total < 0 else ''}{unit}{body}"
    return _amount(round(total), unit or None)


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else many or one + 's'}"


def _kpis(w: Widget) -> list[Block]:
    columns = []
    for m in w["metrics"]:
        value = _with({"type": "value", "value": m["value"], "label": m["label"]})
        column = [_with(value, delta=m.get("delta"), tone=m.get("tone"))]
        if len(m.get("trend", [])) >= 2:
            column.append(_with({"type": "sparkline", "values": m["trend"]}, tone=m.get("tone")))
        columns.append(column)
    return [{"type": "columns", "columns": columns}]


def _words(text: str) -> str:
    return " ".join(text.lower().replace("_", " ").replace("-", " ").split())


# A task's status, in the words trackers use, as a tone.
_DONE = {"done", "complete", "completed", "closed", "shipped", "merged", "resolved"}
_STUCK = {"blocked", "stuck", "overdue", "late", "at risk", "failed", "on hold"}
_MOVING = {"in progress", "doing", "started", "in review", "review", "active", "underway"}


def _task_tone(status: str | None) -> str | None:
    word = _words(status or "")
    if word in _DONE:
        return "good"
    if word in _STUCK:
        return "bad"
    if word in _MOVING:
        return "attention"
    return None


def _tasks(w: Widget) -> list[Block]:
    items = []
    for t in w["tasks"]:
        due = t.get("due")
        done = bool(t.get("done")) or _words(t.get("status", "")) in _DONE
        item = _with(
            {"title": t["title"]},
            detail=t.get("status"),
            person=t.get("owner"),
            status=_task_tone(t.get("status")),
            when=due if due and is_date(due) else None,
            meta=f"Due {due}" if due and not is_date(due) else None,
            chip=t.get("priority"),
        )
        item["done"] = done
        items.append(item)
    return [{"type": "rows", "items": items}]


def _total_value(w: Widget) -> list[Block]:
    return [{"type": "value", "value": w["total"], "label": "Total"}] if "total" in w else []


_PAID_TONE = {"refunded": "bad", "failed": "bad", "pending": "attention"}


def _transactions(w: Widget) -> list[Block]:
    items = []
    for t in w["items"]:
        status = t.get("status")
        said = [t.get("what"), status.capitalize() if status in _PAID_TONE else None]
        detail = " · ".join(part for part in said if part) or None
        items.append(
            _with(
                {"title": t["who"], "person": t["who"], "amount": t["amount"]},
                detail=detail,
                amount_tone=_PAID_TONE.get(status or ""),
                when=t.get("when"),
            )
        )
    return [*_total_value(w), {"type": "rows", "items": items}]


_BILL_WORDS = {"overdue": "Overdue", "paid": "Paid"}


def _invoices(w: Widget) -> list[Block]:
    out: list[Block] = []
    late = [i for i in w["items"] if i.get("status") == "overdue"]
    if late:
        total = _sum_of(i["amount"] for i in late)
        value = {"type": "value", "value": total or str(len(late)), "label": "Overdue"}
        if total:
            value["hint"] = _plural(len(late), "invoice")
        out.append(value)
    items = []
    for i in w["items"]:
        due, status = i["due"], i.get("status")
        # "Due" goes without saying; late and paid are worth the words.
        said = [None if is_date(due) else f"Due {due}", _BILL_WORDS.get(status or "")]
        items.append(
            _with(
                {"title": i["who"], "amount": i["amount"]},
                detail=" · ".join(part for part in said if part) or None,
                amount_tone="bad" if status == "overdue" else None,
                when=due if is_date(due) else None,
            )
        )
    out.append({"type": "rows", "items": items})
    return out


def _breakdown(w: Widget) -> list[Block]:
    return [_with({"type": "bars", "items": w["items"]}, unit=w.get("unit"))]


def _payouts(w: Widget) -> list[Block]:
    items = [
        _with({"title": _deadline(p["when"]), "amount": p["amount"]}, detail=p.get("note"))
        for p in w["items"]
    ]
    return [*_total_value(w), {"type": "rows", "items": items}]


def _pipeline(w: Widget) -> list[Block]:
    out: list[Block] = [{"type": "funnel", "stages": w["stages"]}]
    values = [s["value"] for s in w["stages"] if "value" in s]
    total = _sum_of(values) if values else None
    if total:
        out.append({"type": "facts", "items": [{"label": "Total value", "value": total}]})
    return out


def _deal(w: Widget) -> list[Block]:
    out: list[Block] = []
    if "amount" in w:
        out.append({"type": "value", "value": w["amount"]})
    out.append({"type": "stages", "steps": w["stages"], "current": w["current"]})
    facts = [
        {"label": label, "value": value}
        for label, value in (
            ("Owner", w.get("owner")),
            ("Next step", w.get("next")),
            ("Closes", _deadline(w["close"]) if "close" in w else None),
        )
        if value
    ]
    if facts:
        out.append({"type": "facts", "items": facts})
    return out


def _contact(w: Widget) -> list[Block]:
    role, company = w.get("role"), w.get("company")
    detail = f"{role} at {company}" if role and company else role or company
    out: list[Block] = [_with({"type": "person", "name": w["name"]}, detail=detail)]
    if w.get("facts"):
        out.append({"type": "facts", "items": w["facts"]})
    if w.get("activity"):
        items = [_with({"title": a["title"]}, when=a.get("when")) for a in w["activity"]]
        out.append({"type": "rows", "label": "Recent activity", "items": items})
    return out


def _campaign(w: Widget) -> list[Block]:
    stages = [{"label": key.capitalize(), "count": w[key]} for key in CAMPAIGN if key in w]
    return [{"type": "funnel", "stages": stages}]


def _moment(text: str) -> tuple[int, float]:
    """For putting times in order: as an instant (a time without a zone is the user's,
    read here as local), and anything that is not a time last."""
    try:
        return 0, datetime.fromisoformat(text).timestamp()
    except (ValueError, OverflowError, OSError):
        return 1, 0.0


def _support_queue(w: Widget) -> list[Block]:
    out: list[Block] = [{"type": "value", "value": f"{w['open']:,}", "label": "Open"}]
    if w.get("waiting"):
        oldest_first = sorted(w["waiting"], key=lambda t: _moment(t["since"]))
        items = [
            {"title": t["who"], "person": t["who"], "detail": t["subject"], "when": t["since"]}
            for t in oldest_first
        ]
        out.append({"type": "rows", "label": "Waiting longest", "items": items})
    if "first_reply" in w:
        out.append(
            {"type": "facts", "items": [{"label": "First reply", "value": w["first_reply"]}]}
        )
    return out


_CHECK_TONE = {"passing": "good", "failing": "bad", "running": "attention", "pending": "attention"}


def _checks(w: Widget) -> list[Block]:
    items = [
        _with(
            {"title": c["title"], "status": _CHECK_TONE[c["state"]], "detail": c["state"].title()},
            person=c.get("who"),
            when=c.get("when"),
        )
        for c in w["items"]
    ]
    return [{"type": "rows", "items": items}]


def _errors(w: Widget) -> list[Block]:
    out: list[Block] = []
    if len(w.get("trend", [])) >= 2:
        out.append({"type": "sparkline", "values": w["trend"]})
    most_first = sorted(w["issues"], key=lambda i: -i["events"])
    bars = [
        _with(
            {"label": i["title"], "value": i["events"]},
            note=_plural(i["users"], "user") if "users" in i else None,
        )
        for i in most_first
    ]
    out.append({"type": "bars", "items": bars, "unit": "events"})
    return out


_DEPLOY_TONE = {"ready": "good", "building": "attention", "failed": "bad", "cancelled": "neutral"}


def _deploys(w: Widget) -> list[Block]:
    items = [
        _with(
            {"title": d["title"], "status": _DEPLOY_TONE[d["state"]], "detail": d["state"].title()},
            chip=d.get("branch"),
            when=d.get("when"),
        )
        for d in w["items"]
    ]
    return [{"type": "rows", "items": items}]


_SERVICE_TONE = {"up": "good", "degraded": "attention", "down": "bad"}


def _services(w: Widget) -> list[Block]:
    states = [s["state"] for s in w["items"]]
    down, slow = states.count("down"), states.count("degraded")
    counts = [f"{down} down" if down else "", f"{slow} degraded" if slow else ""]
    text = ", ".join(part for part in counts if part) or "All up"
    tone = "bad" if down else "attention" if slow else "good"
    items = []
    for s in w["items"]:
        said = [None if s["state"] == "up" else s["state"].title(), s.get("note")]
        items.append(
            _with(
                {"title": s["name"], "status": _SERVICE_TONE[s["state"]]},
                detail=" · ".join(part for part in said if part) or None,
            )
        )
    return [{"type": "status", "tone": tone, "text": text}, {"type": "rows", "items": items}]


def _kanban(w: Widget) -> list[Block]:
    columns = []
    for c in w["columns"]:
        label = cap(f"{c['name']} · {c.get('count', len(c['items'])):,}", 60)
        if c["items"]:
            column: list[Block] = [
                {"type": "rows", "label": label, "items": [{"title": t} for t in c["items"]]}
            ]
        else:
            # No cards listed: the count says it, and an empty column says so plainly.
            empty = "" if c.get("count") else "\nNothing here"
            column = [{"type": "text", "text": f"**{label}**{empty}"}]
        columns.append(column)
    return [{"type": "columns", "columns": columns}]


def _sprint(w: Widget) -> list[Block]:
    done, total = w["done"], w["total"]
    out: list[Block] = [
        {"type": "meter", "value": done, "max": total, "label": f"{done:,} of {total:,} done"}
    ]
    burndown = w.get("burndown", [])
    if len(burndown) >= 2:
        out.append(
            {
                "type": "chart",
                "kind": "line",
                "x": [f"Day {n}" for n in range(1, len(burndown) + 1)],
                "series": [{"name": "Remaining", "values": burndown}],
            }
        )
    if "ends" in w:
        out.append({"type": "facts", "items": [{"label": "Ends", "value": _deadline(w["ends"])}]})
    return out


def _day_name(key: str) -> str:
    """A due day as she would say it: Today, Tomorrow, Yesterday, else 'Thu 29 Oct'.
    Words she wrote ("Next week") stay hers."""
    if not is_date(key):
        return key
    day, now = date.fromisoformat(key), blocks.today()
    near = {now: "Today", now + timedelta(days=1): "Tomorrow", now - timedelta(days=1): "Yesterday"}
    return near.get(day) or _day(key)


def _due_soon(w: Widget) -> list[Block]:
    now = blocks.today().isoformat()
    groups: dict[str, list[Block]] = {}
    # A day in words is labelled as she first wrote it.
    labels: dict[str, str] = {}
    # Dated days in order, then days in words as she gave them.
    order = sorted(
        w["items"],
        key=lambda i: (not is_date(i["due"]), due_key(i["due"]) if is_date(i["due"]) else ""),
    )
    for i in order:
        key = due_key(i["due"])
        labels.setdefault(key, i["due"])
        late = is_date(key) and key < now
        groups.setdefault(key, []).append(
            _with(
                {"title": i["title"]},
                person=i.get("owner"),
                when=i["due"] if "T" in i["due"] and is_date(i["due"]) else None,
                status="bad" if late else None,
            )
        )
    return [
        {"type": "rows", "label": _day_name(k if is_date(k) else labels[k]), "items": v}
        for k, v in groups.items()
    ]


PACK_RECIPES: dict[str, Callable[[Widget], list[Block]]] = {
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

# What a pack card is called when she gave it no title.
PACK_TITLES = {
    "kpis": "Key numbers",
    "tasks": "Tasks",
    "transactions": "Payments",
    "invoices": "Invoices",
    "breakdown": "Breakdown",
    "payouts": "Payouts",
    "pipeline": "Pipeline",
    "contact": "Contact",
    "campaign": "Campaign",
    "support_queue": "Support queue",
    "checks": "Checks",
    "errors": "Errors",
    "deploys": "Deploys",
    "services": "Services",
    "kanban": "Board",
    "sprint": "Sprint",
    "due_soon": "Due soon",
}
# Or the widget's own field that names it (the default above when it has none).
PACK_TITLED_BY = {"deal": "name", "sprint": "name"}
