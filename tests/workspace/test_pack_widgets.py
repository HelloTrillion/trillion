"""The packs' widgets (the widget packs spec): what she may write for money, customers,
building and work, the blocks each becomes, and the names that lead to them."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from trillion.workspace import blocks as blocks_mod
from trillion.workspace import board as b
from trillion.workspace.blocks import validate_blocks
from trillion.workspace.kit import BoardError
from trillion.workspace.recipes import expand, expand_board
from trillion.workspace.widgets import validate_widget

MINIMAL: list[dict[str, Any]] = [
    {
        "type": "kpis",
        "metrics": [{"label": "Revenue", "value": "$48k"}, {"label": "Churn", "value": "2.1%"}],
    },
    {"type": "tasks", "tasks": [{"title": "Write the launch post"}]},
    {"type": "transactions", "items": [{"who": "Sam", "amount": "$120"}]},
    {"type": "invoices", "items": [{"who": "Maple Park Dental", "amount": "$1,200", "due": "Fri"}]},
    {"type": "breakdown", "items": [{"label": "Rent", "value": 1950}]},
    {"type": "payouts", "items": [{"amount": "$3,400", "when": "2026-10-27"}]},
    {
        "type": "pipeline",
        "stages": [{"label": "Leads", "count": 40}, {"label": "Won", "count": 6}],
    },
    {"type": "deal", "name": "Maple Park Dental", "stages": ["Lead", "Won"], "current": "Lead"},
    {"type": "contact", "name": "Jo"},
    {"type": "campaign", "sent": 1200, "opened": 540},
    {"type": "support_queue", "open": 7},
    {"type": "checks", "items": [{"title": "Unit tests", "state": "passing"}]},
    {"type": "errors", "issues": [{"title": "TypeError in checkout", "events": 120}]},
    {"type": "deploys", "items": [{"title": "Fix the header", "state": "ready"}]},
    {"type": "services", "items": [{"name": "API", "state": "up"}]},
    {
        "type": "kanban",
        "columns": [{"name": "To do", "items": ["Header"]}, {"name": "Done", "items": []}],
    },
    {"type": "sprint", "done": 12, "total": 20},
    {"type": "due_soon", "items": [{"title": "Send the quote", "due": "2026-10-26"}]},
]

FULL: list[dict[str, Any]] = [
    {
        "type": "kpis",
        "title": "This month",
        "metrics": [
            {
                "label": "Revenue",
                "value": "$48,200",
                "delta": "+8%",
                "tone": "good",
                "trend": [40, 42, 45, 48.2],
            },
            {"label": "New customers", "value": 37, "delta": "-3", "tone": "down", "trend": [4]},
            {"label": "Churn", "value": "2.1%"},
        ],
    },
    {
        "type": "tasks",
        "title": "Launch",
        "tasks": [
            {
                "title": "Write the launch post",
                "status": "In progress",
                "due": "2026-10-28",
                "owner": "Jo",
                "priority": "High",
            },
            {"title": "Fix the header", "status": "Blocked", "due": "Friday", "owner": "Sam"},
            {"title": "Book the venue", "status": "Done"},
            {"title": "Order badges", "done": True, "priority": 2},
        ],
    },
    {
        "type": "transactions",
        "title": "Payments today",
        "items": [
            {
                "who": "Maple Park Dental",
                "amount": "$1,200",
                "what": "Annual plan",
                "status": "paid",
                "when": "2026-10-25T09:12",
            },
            {"who": "Jo", "amount": "$85", "what": "Monthly plan", "status": "Refunded"},
            {"who": "Alex", "amount": 40, "status": "succeeded"},
            {"who": "Sam", "amount": "$120", "status": "pending"},
        ],
        "total": "$1,445",
    },
    {
        "type": "invoices",
        "title": "Unpaid",
        "items": [
            {
                "who": "Maple Park Dental",
                "amount": "$1,200",
                "due": "2026-10-10",
                "status": "overdue",
            },
            {
                "who": "Elm Street Bakery",
                "amount": "$420.50",
                "due": "2026-10-20",
                "status": "late",
            },
            {"who": "Alex Rivera Design", "amount": "$850", "due": "2026-11-05", "status": "due"},
            {"who": "Jo", "amount": "$90", "due": "Friday", "status": "paid"},
        ],
    },
    {
        "type": "breakdown",
        "title": "Spend by category",
        "items": [{"label": "Rent", "value": 1950}, {"label": "Food", "value": "612"}],
        "unit": "$",
    },
    {
        "type": "payouts",
        "title": "Next payouts",
        "items": [
            {"amount": "$3,400", "when": "2026-10-27", "note": "To the account ending 0042"},
            {"amount": "$1,150", "when": "Friday"},
        ],
        "total": "$4,550",
    },
    {
        "type": "pipeline",
        "title": "Sales this quarter",
        "stages": [
            {"label": "Leads", "count": 48, "value": "$212,000"},
            {"label": "Proposal", "count": "9", "value": "$64,500"},
            {"name": "Won", "count": 4, "value": "$31,200"},
        ],
    },
    {
        "type": "deal",
        "name": "Maple Park Dental, 40 seats",
        "amount": "$18,000",
        "stages": ["Lead", "Qualified", "Proposal", "Won"],
        "current": "proposal",
        "owner": "Sam",
        "next": "Send the revised quote",
        "close": "2026-11-20",
    },
    {
        "type": "contact",
        "name": "Jo Park",
        "role": "Head of operations",
        "company": "Maple Park Dental",
        "facts": [{"label": "Plan", "value": "Annual"}, {"label": "Seats", "value": 40}],
        "activity": [
            {"title": "Opened the proposal", "when": "2026-10-24T16:20"},
            {"title": "Call with Sam"},
        ],
    },
    {
        "type": "campaign",
        "title": "October newsletter",
        "sent": "4,200",
        "delivered": 4150,
        "opened": 1890,
        "clicked": 412,
        "replied": 23,
    },
    {
        "type": "support_queue",
        "title": "Support",
        "open": "14",
        "waiting": [
            {"who": "Alex", "subject": "Can't log in", "since": "2026-10-25T08:00"},
            {"who": "Jo", "subject": "Refund for October", "since": "2026-10-24T15:30"},
        ],
        "first_reply": "2h 10m",
    },
    {
        "type": "checks",
        "title": "Pull request 214",
        "items": [
            {"title": "Unit tests", "state": "passing", "who": "Sam", "when": "2026-10-25T09:40"},
            {"title": "Lint", "state": "FAILED"},
            {"title": "Build", "state": "in_progress"},
            {"title": "Preview", "state": "queued"},
        ],
    },
    {
        "type": "errors",
        "title": "Errors this week",
        "trend": [12, 18, 9, 40],
        "issues": [
            {"title": "TypeError in checkout", "events": 120, "users": 45},
            {"title": "Timeout on /search", "events": "1,340", "users": 1},
            {"title": "Null avatar", "events": 8},
        ],
    },
    {
        "type": "deploys",
        "title": "Deploys",
        "items": [
            {"title": "Fix the header", "state": "ready", "branch": "main", "when": "9:12"},
            {"title": "New pricing page", "state": "building", "branch": "pricing"},
            {"title": "Try the new font", "state": "error"},
            {"title": "Old preview", "state": "canceled"},
        ],
    },
    {
        "type": "services",
        "title": "Status",
        "items": [
            {"name": "API", "state": "up"},
            {"name": "Database", "state": "degraded", "note": "Slow writes since 9:00"},
            {"name": "Email", "state": "Major outage"},
        ],
    },
    {
        "type": "kanban",
        "title": "Website",
        "columns": [
            {"name": "To do", "count": 12, "items": ["Header", {"title": "Footer links"}]},
            {"name": "Doing", "items": ["Pricing page"]},
            {"name": "Review", "count": 3, "items": []},
            {"name": "Done", "items": []},
        ],
    },
    {
        "type": "sprint",
        "name": "Sprint 42",
        "done": "12",
        "total": 20,
        "ends": "2026-10-30",
        "burndown": [20, 18, 15, 12, 8],
    },
    {
        "type": "due_soon",
        "title": "Due soon",
        "items": [
            {"title": "Send the quote", "due": "2026-10-26", "owner": "Sam"},
            {"title": "Pay the rent", "due": "2026-10-25"},
            {"title": "Renew the domain", "due": "2026-10-29T17:00"},
            {"title": "Call the bank", "due": "2026-10-24"},
            {"title": "Plan the offsite", "due": "Next week"},
            {"title": "Review the budget", "due": "2026-10-25T15:00", "owner": "Jo"},
        ],
    },
]

MIN_OF = {w["type"]: w for w in MINIMAL}
FULL_OF = {w["type"]: w for w in FULL}
EVERY = MINIMAL + FULL


def refused(raw: object, at: int | str = 1) -> str:
    with pytest.raises(BoardError) as err:
        validate_widget(raw, at)
    return str(err.value)


def blocks_of(raw: dict[str, Any]) -> list[dict[str, Any]]:
    return expand(validate_widget(raw, 1))


def kinds(raw: dict[str, Any]) -> list[str]:
    return [blk["type"] for blk in blocks_of(raw)]


@pytest.fixture
def on_oct_25(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(blocks_mod, "today", lambda: date(2026, 10, 25))


pytestmark = pytest.mark.usefixtures("on_oct_25")


# The kit takes them


def test_fixtures_cover_every_new_type() -> None:
    assert set(MIN_OF) == set(FULL_OF) and len(MIN_OF) == 18


@pytest.mark.parametrize("raw", EVERY, ids=lambda w: w["type"])
def test_each_is_accepted_and_expands_to_valid_blocks(raw: dict[str, Any]) -> None:
    widget = validate_widget(raw, 1)
    assert widget["type"] == raw["type"]
    drawn = expand(widget)
    assert drawn and validate_blocks(drawn, "w1") == drawn


def test_sizes() -> None:
    assert validate_widget(MIN_OF["kpis"], 1)["size"] == "wide"
    assert validate_widget(MIN_OF["kanban"], 1)["size"] == "wide"
    assert validate_widget(MIN_OF["deal"], 1)["size"] == "medium"


# What each becomes


def test_kpis() -> None:
    (columns,) = blocks_of(FULL_OF["kpis"])
    assert columns["type"] == "columns"
    first, second, third = columns["columns"]
    assert first == [
        {"type": "value", "value": "$48,200", "label": "Revenue", "delta": "+8%", "tone": "good"},
        {"type": "sparkline", "values": [40, 42, 45, 48.2], "tone": "good"},
    ]
    # One point is no trend: no sparkline. A number is written as text.
    assert second == [
        {"type": "value", "value": "37", "label": "New customers", "delta": "-3", "tone": "bad"}
    ]
    assert third == [{"type": "value", "value": "2.1%", "label": "Churn"}]


def test_kpis_limits() -> None:
    one = {"type": "kpis", "metrics": [{"label": "Revenue", "value": "$48k"}]}
    assert refused(one) == "widget 1 (kpis): 1 metric; kpis takes 2 to 4, and one number is a stat."
    five = {"type": "kpis", "metrics": one["metrics"] * 5}
    assert refused(five) == "widget 1 (kpis): 5 metrics, 4 at most."
    assert "metric 2: value is required" in refused(
        {"type": "kpis", "metrics": [{"label": "a", "value": 1}, {"label": "b"}]}
    )


def test_tasks() -> None:
    (rows,) = blocks_of(FULL_OF["tasks"])
    assert rows["items"] == [
        {
            "title": "Write the launch post",
            "detail": "In progress",
            "person": "Jo",
            "status": "attention",
            "when": "2026-10-28",
            "chip": "High",
            "done": False,
        },
        {
            "title": "Fix the header",
            "detail": "Blocked",
            "person": "Sam",
            "status": "bad",
            "meta": "Due Friday",
            "done": False,
        },
        {"title": "Book the venue", "detail": "Done", "status": "good", "done": True},
        {"title": "Order badges", "chip": "2", "done": True},
    ]


def test_transactions() -> None:
    total, rows = blocks_of(FULL_OF["transactions"])
    assert total == {"type": "value", "value": "$1,445", "label": "Total"}
    paid, refunded, succeeded, pending = rows["items"]
    assert paid == {
        "title": "Maple Park Dental",
        "person": "Maple Park Dental",
        "amount": "$1,200",
        "detail": "Annual plan",
        "when": "2026-10-25T09:12",
    }
    assert refunded["detail"] == "Monthly plan · Refunded" and refunded["amount_tone"] == "bad"
    assert succeeded["amount"] == "40" and "amount_tone" not in succeeded
    assert pending["detail"] == "Pending" and pending["amount_tone"] == "attention"
    assert kinds(MIN_OF["transactions"]) == ["rows"]


def test_transaction_status_words() -> None:
    bad = {"type": "transactions", "items": [{"who": "Sam", "amount": 5, "status": "lost"}]}
    assert refused(bad) == (
        "widget 1 (transactions): item 1: status must be one of paid, failed, refunded,"
        " pending, not 'lost'."
    )


def test_invoices() -> None:
    late, rows = blocks_of(FULL_OF["invoices"])
    assert late == {"type": "value", "value": "$1,620.50", "label": "Overdue", "hint": "2 invoices"}
    first, second, third, fourth = rows["items"]
    assert first == {
        "title": "Maple Park Dental",
        "amount": "$1,200",
        "detail": "Overdue",
        "amount_tone": "bad",
        "when": "2026-10-10",
    }
    assert second["amount_tone"] == "bad"
    assert third == {"title": "Alex Rivera Design", "amount": "$850", "when": "2026-11-05"}
    assert fourth == {"title": "Jo", "amount": "$90", "detail": "Due Friday · Paid"}
    # Nothing overdue: no total.
    assert kinds(MIN_OF["invoices"]) == ["rows"]


def test_an_overdue_total_is_only_added_up_when_it_can_be() -> None:
    mixed = {
        "type": "invoices",
        "items": [
            {"who": "Sam", "amount": "$100", "due": "Fri", "status": "overdue"},
            {"who": "Jo", "amount": "€80", "due": "Fri", "status": "overdue"},
        ],
    }
    assert blocks_of(mixed)[0] == {"type": "value", "value": "2", "label": "Overdue"}
    plain = {"type": "invoices", "items": [{**mixed["items"][0], "amount": "1,250"}]}
    assert blocks_of(plain)[0]["value"] == "1,250"


def test_breakdown() -> None:
    assert blocks_of(FULL_OF["breakdown"]) == [
        {
            "type": "bars",
            "items": [{"label": "Rent", "value": 1950}, {"label": "Food", "value": 612}],
            "unit": "$",
        }
    ]
    assert "item 1: value must be 0 or more, not -5" in refused(
        {"type": "breakdown", "items": [{"label": "Refunds", "value": -5}]}
    )
    many = {"type": "breakdown", "items": [{"label": "a", "value": 1}] * 11}
    assert refused(many) == "widget 1 (breakdown): 11 items, 10 at most."


def test_payouts() -> None:
    total, rows = blocks_of(FULL_OF["payouts"])
    assert total == {"type": "value", "value": "$4,550", "label": "Total"}
    assert rows["items"] == [
        {"title": "Tue 27 Oct", "amount": "$3,400", "detail": "To the account ending 0042"},
        {"title": "Friday", "amount": "$1,150"},
    ]
    assert "item 1: when is required" in refused({"type": "payouts", "items": [{"amount": 5}]})


def test_pipeline() -> None:
    funnel, facts = blocks_of(FULL_OF["pipeline"])
    assert funnel == {
        "type": "funnel",
        "stages": [
            {"label": "Leads", "count": 48, "value": "$212,000"},
            {"label": "Proposal", "count": 9, "value": "$64,500"},
            {"label": "Won", "count": 4, "value": "$31,200"},
        ],
    }
    assert facts == {"type": "facts", "items": [{"label": "Total value", "value": "$307,700"}]}
    assert kinds(MIN_OF["pipeline"]) == ["funnel"]


def test_pipeline_limits() -> None:
    one = {"type": "pipeline", "stages": [{"label": "Leads", "count": 4}]}
    assert refused(one) == "widget 1 (pipeline): stages needs at least 2 stages."
    assert refused({"type": "pipeline", "stages": one["stages"] * 7}) == (
        "widget 1 (pipeline): 7 stages, 6 at most."
    )
    half = {"type": "pipeline", "stages": [one["stages"][0], {"label": "Won", "count": 1.5}]}
    assert refused(half) == (
        "widget 1 (pipeline): stage 2: count must be a whole number, 0 or more, not 1.5."
    )


def test_deal() -> None:
    value, stages, facts = blocks_of(FULL_OF["deal"])
    assert value == {"type": "value", "value": "$18,000"}
    assert stages == {
        "type": "stages",
        "steps": ["Lead", "Qualified", "Proposal", "Won"],
        "current": "Proposal",
    }
    assert facts["items"] == [
        {"label": "Owner", "value": "Sam"},
        {"label": "Next step", "value": "Send the revised quote"},
        {"label": "Closes", "value": "Fri 20 Nov"},
    ]
    assert kinds(MIN_OF["deal"]) == ["stages"]
    assert "current 'Signed' is not a step" in refused({**MIN_OF["deal"], "current": "Signed"})
    assert validate_widget({**MIN_OF["deal"], "current": 1}, 1)["current"] == 1


def test_contact() -> None:
    person, facts, rows = blocks_of(FULL_OF["contact"])
    assert person == {
        "type": "person",
        "name": "Jo Park",
        "detail": "Head of operations at Maple Park Dental",
    }
    assert facts["items"][1] == {"label": "Seats", "value": "40"}
    assert rows["label"] == "Recent activity"
    assert rows["items"] == [
        {"title": "Opened the proposal", "when": "2026-10-24T16:20"},
        {"title": "Call with Sam"},
    ]
    assert blocks_of({**MIN_OF["contact"], "company": "Maple Park"}) == [
        {"type": "person", "name": "Jo", "detail": "Maple Park"}
    ]


def test_campaign() -> None:
    (funnel,) = blocks_of(FULL_OF["campaign"])
    assert [(s["label"], s["count"]) for s in funnel["stages"]] == [
        ("Sent", 4200),
        ("Delivered", 4150),
        ("Opened", 1890),
        ("Clicked", 412),
        ("Replied", 23),
    ]
    assert blocks_of(MIN_OF["campaign"])[0]["stages"] == [
        {"label": "Sent", "count": 1200},
        {"label": "Opened", "count": 540},
    ]
    assert refused({"type": "campaign", "sent": 100}) == (
        "widget 1 (campaign): give sent and at least one of delivered, opened, clicked, replied."
    )
    assert refused({"type": "campaign", "opened": 100}) == "widget 1 (campaign): sent is required."


def test_support_queue() -> None:
    value, rows, facts = blocks_of(FULL_OF["support_queue"])
    assert value == {"type": "value", "value": "14", "label": "Open"}
    # Oldest first.
    assert [i["title"] for i in rows["items"]] == ["Jo", "Alex"]
    assert rows["items"][0] == {
        "title": "Jo",
        "person": "Jo",
        "detail": "Refund for October",
        "when": "2026-10-24T15:30",
    }
    assert facts == {"type": "facts", "items": [{"label": "First reply", "value": "2h 10m"}]}
    assert kinds(MIN_OF["support_queue"]) == ["value"]


def test_checks() -> None:
    (rows,) = blocks_of(FULL_OF["checks"])
    assert [(i["status"], i["detail"]) for i in rows["items"]] == [
        ("good", "Passing"),
        ("bad", "Failing"),
        ("attention", "Running"),
        ("attention", "Pending"),
    ]
    assert rows["items"][0]["person"] == "Sam"
    assert "state is required" in refused({"type": "checks", "items": [{"title": "Lint"}]})
    assert refused({"type": "checks", "items": [{"title": "Lint", "state": "purple"}]}) == (
        "widget 1 (checks): item 1: state must be one of passing, failing, running, pending,"
        " not 'purple'."
    )


def test_errors() -> None:
    spark, bars = blocks_of(FULL_OF["errors"])
    assert spark == {"type": "sparkline", "values": [12, 18, 9, 40]}
    # Most events first; a count written as text is the number.
    assert bars == {
        "type": "bars",
        "items": [
            {"label": "Timeout on /search", "value": 1340, "note": "1 user"},
            {"label": "TypeError in checkout", "value": 120, "note": "45 users"},
            {"label": "Null avatar", "value": 8},
        ],
        "unit": "events",
    }
    assert kinds(MIN_OF["errors"]) == ["bars"]


def test_deploys() -> None:
    (rows,) = blocks_of(FULL_OF["deploys"])
    first, building, failed, cancelled = rows["items"]
    assert first == {
        "title": "Fix the header",
        "status": "good",
        "detail": "Ready",
        "chip": "main",
        "when": "2026-10-25T09:12",
    }
    assert (building["status"], failed["status"], cancelled["status"]) == (
        "attention",
        "bad",
        "neutral",
    )
    assert cancelled["detail"] == "Cancelled"


def test_services() -> None:
    status, rows = blocks_of(FULL_OF["services"])
    assert status == {"type": "status", "tone": "bad", "text": "1 down, 1 degraded"}
    assert rows["items"] == [
        {"title": "API", "status": "good"},
        {"title": "Database", "status": "attention", "detail": "Degraded · Slow writes since 9:00"},
        {"title": "Email", "status": "bad", "detail": "Down"},
    ]
    assert blocks_of(MIN_OF["services"])[0] == {"type": "status", "tone": "good", "text": "All up"}
    slow = {"type": "services", "items": [{"name": "API", "state": "slow"}]}
    assert blocks_of(slow)[0] == {"type": "status", "tone": "attention", "text": "1 degraded"}


def test_kanban() -> None:
    (columns,) = blocks_of(FULL_OF["kanban"])
    todo, doing, review, done = columns["columns"]
    assert todo == [
        {
            "type": "rows",
            "label": "To do · 12",
            "items": [{"title": "Header"}, {"title": "Footer links"}],
        }
    ]
    assert doing[0]["label"] == "Doing · 1"
    # Only a count: said, with no cards made up.
    assert review == [{"type": "text", "text": "**Review · 3**"}]
    assert done == [{"type": "text", "text": "**Done · 0**\nNothing here"}]


def test_kanban_limits() -> None:
    column = {"name": "To do", "items": ["a"]}
    assert refused({"type": "kanban", "columns": [column]}) == (
        "widget 1 (kanban): columns needs at least 2 columns."
    )
    assert refused({"type": "kanban", "columns": [column] * 5}) == (
        "widget 1 (kanban): 5 columns, 4 at most."
    )
    six = {"name": "To do", "items": list("abcdef")}
    assert refused({"type": "kanban", "columns": [six, column]}) == (
        "widget 1 (kanban): column 1: 6 items, 5 at most; give count for how many in all."
    )


def test_sprint() -> None:
    meter, chart, facts = blocks_of(FULL_OF["sprint"])
    assert meter == {"type": "meter", "value": 12, "max": 20, "label": "12 of 20 done"}
    assert chart == {
        "type": "chart",
        "kind": "line",
        "x": ["Day 1", "Day 2", "Day 3", "Day 4", "Day 5"],
        "series": [{"name": "Remaining", "values": [20, 18, 15, 12, 8]}],
    }
    assert facts == {"type": "facts", "items": [{"label": "Ends", "value": "Fri 30 Oct"}]}
    assert kinds(MIN_OF["sprint"]) == ["meter"]


def test_sprint_limits() -> None:
    assert refused({"type": "sprint", "done": 25, "total": 20}) == (
        "widget 1 (sprint): done 25 is more than total 20."
    )
    assert refused({"type": "sprint", "done": 0, "total": 0}) == (
        "widget 1 (sprint): total must be above 0."
    )


def test_due_soon_groups_by_day() -> None:
    drawn = blocks_of(FULL_OF["due_soon"])
    assert [(r["label"], [i["title"] for i in r["items"]]) for r in drawn] == [
        ("Yesterday", ["Call the bank"]),
        ("Today", ["Pay the rent", "Review the budget"]),
        ("Tomorrow", ["Send the quote"]),
        ("Thu 29 Oct", ["Renew the domain"]),
        ("Next week", ["Plan the offsite"]),
    ]
    yesterday, today, tomorrow, thursday, _ = drawn
    assert yesterday["items"][0] == {"title": "Call the bank", "status": "bad"}
    assert today["items"][1] == {
        "title": "Review the budget",
        "person": "Jo",
        "when": "2026-10-25T15:00",
    }
    assert tomorrow["items"][0] == {"title": "Send the quote", "person": "Sam"}
    assert thursday["items"][0]["when"] == "2026-10-29T17:00"


def test_due_soon_limits() -> None:
    days = [{"title": "a", "due": f"2026-11-{d:02d}"} for d in range(1, 10)]
    assert refused({"type": "due_soon", "items": days}) == (
        "widget 1 (due_soon): the items fall on 9 days, 8 at most."
    )
    assert "item 1: due is required" in refused({"type": "due_soon", "items": [{"title": "a"}]})


def test_a_bare_time_is_today_in_the_packs_too() -> None:
    raw = {"type": "due_soon", "items": [{"title": "Call Sam", "due": "5pm"}]}
    assert validate_widget(raw, 1)["items"][0]["due"] == "2026-10-25T17:00"


# Default titles


@pytest.mark.parametrize(
    ("kind", "title"),
    [
        ("kpis", "Key numbers"),
        ("tasks", "Tasks"),
        ("transactions", "Payments"),
        ("invoices", "Invoices"),
        ("breakdown", "Breakdown"),
        ("payouts", "Payouts"),
        ("pipeline", "Pipeline"),
        ("deal", "Maple Park Dental"),
        ("contact", "Contact"),
        ("campaign", "Campaign"),
        ("support_queue", "Support queue"),
        ("checks", "Checks"),
        ("errors", "Errors"),
        ("deploys", "Deploys"),
        ("services", "Services"),
        ("kanban", "Board"),
        ("sprint", "Sprint"),
        ("due_soon", "Due soon"),
    ],
)
def test_untitled_pack_widgets_get_a_title_when_sent(kind: str, title: str) -> None:
    saved = b.show(None, "Day", [MIN_OF[kind]])
    assert "title" not in saved["widgets"][0]
    assert expand_board(saved)["widgets"][0]["title"] == title


def test_a_named_sprint_is_titled_by_its_name() -> None:
    saved = b.show(None, "Day", [{**MIN_OF["sprint"], "name": "Sprint 42"}])
    assert expand_board(saved)["widgets"][0]["title"] == "Sprint 42"


def test_named_to_her() -> None:
    saved = b.show(None, "Day", [MIN_OF["deal"], MIN_OF["contact"], MIN_OF["checks"]])
    assert b.summary(saved) == (
        'Board (open): "Day": w1 deal "Maple Park Dental", w2 contact "Jo", w3 checks.'
    )


# Names that lead to them


@pytest.mark.parametrize(
    ("given", "meant"),
    [
        ("payments", "transactions"),
        ("charges", "transactions"),
        ("bills", "invoices"),
        ("funnel", "pipeline"),
        ("ci", "checks"),
        ("builds", "checks"),
        ("board", "kanban"),
        ("burndown", "sprint"),
        ("cycle", "sprint"),
        ("todo", "tasks"),
        ("todos", "tasks"),
        ("metrics", "kpis"),
        ("deployments", "deploys"),
        ("tickets", "support_queue"),
        ("customer", "contact"),
        ("uptime", "services"),
        ("issues", "errors"),
        ("Due soon", "due_soon"),
        ("support-queue", "support_queue"),
    ],
)
def test_near_miss_names(given: str, meant: str) -> None:
    assert validate_widget({**MIN_OF[meant], "type": given}, 1)["type"] == meant


def test_a_funnel_with_sent_is_a_campaign() -> None:
    assert validate_widget({**MIN_OF["campaign"], "type": "funnel"}, 1)["type"] == "campaign"


def test_issues_without_events_are_tasks() -> None:
    raw = {"type": "issues", "issues": [{"title": "Fix the header", "status": "In progress"}]}
    assert validate_widget(raw, 1) == {
        "type": "tasks",
        "size": "medium",
        "tasks": [{"title": "Fix the header", "status": "In progress"}],
    }
    untyped = {"issues": [{"title": "Fix the header"}]}
    assert validate_widget(untyped, 1)["type"] == "tasks"


# Items of {label, value} or {title, due} stay what they were before the packs (a list).
@pytest.mark.parametrize("kind", [k for k in FULL_OF if k not in ("breakdown", "due_soon")])
def test_read_from_their_fields(kind: str) -> None:
    untyped = {k: v for k, v in FULL_OF[kind].items() if k != "type"}
    assert validate_widget(untyped, 1)["type"] == kind


def test_a_kanbans_columns_are_objects_and_a_tables_are_names() -> None:
    assert validate_widget({"columns": ["Day"], "rows": [["Mon"]]}, 1)["type"] == "table"
    untyped = {k: v for k, v in MIN_OF["kanban"].items() if k != "type"}
    assert validate_widget(untyped, 1)["type"] == "kanban"


@pytest.mark.parametrize(
    ("item", "meant"),
    [
        ({"title": "Lint", "state": "failed"}, "checks"),
        ({"title": "Fix", "state": "building"}, "deploys"),
        ({"name": "API", "state": "operational"}, "services"),
        ({"who": "Sam", "amount": "$5"}, "transactions"),
        ({"who": "Sam", "amount": "$5", "due": "Fri"}, "invoices"),
        ({"amount": "$5", "when": "Fri"}, "payouts"),
        ({"title": "Milk"}, "list"),
    ],
)
def test_items_say_which_list_they_are(item: dict[str, Any], meant: str) -> None:
    assert validate_widget({"items": [item]}, 1)["type"] == meant
