"""Her widget types (recipes): what she may write, and the blocks each becomes."""

import copy
import logging
from datetime import date
from typing import Any

import pytest

from trillion.workspace import blocks as blocks_mod
from trillion.workspace import board as b
from trillion.workspace.blocks import validate_blocks
from trillion.workspace.kit import BoardError
from trillion.workspace.recipe_kit import is_date
from trillion.workspace.recipes import expand, expand_board
from trillion.workspace.widgets import TYPES, validate_widget

# Today's seven, as a board saved before recipes holds them.
OLD: list[dict[str, Any]] = [
    {"type": "stat", "label": "Total", "value": "$41.20"},
    {
        "type": "chart",
        "title": "Daily spend",
        "kind": "line",
        "x": ["Oct 1", "Oct 2"],
        "series": [{"name": "Spend", "values": [1.2, 3.4]}],
    },
    {"type": "list", "items": [{"title": "Milk"}]},
    {"type": "table", "columns": ["Day", "Spend"], "rows": [["Mon", 3]]},
    {"type": "note", "text": "One thing."},
    {"type": "timeline", "events": [{"title": "Standup", "start": "09:30"}]},
    {
        "type": "compare",
        "options": [
            {"name": "Hike", "metrics": [{"label": "Fun", "score": 8}]},
            {"name": "Museum", "metrics": [{"label": "Fun", "score": 6}]},
        ],
    },
]

MINIMAL: list[dict[str, Any]] = [
    {"type": "next_up", "event": "Design review", "start": "2026-10-25T14:30"},
    {"type": "free_time", "slots": [{"start": "2026-10-28T09:00", "end": "2026-10-28T10:30"}]},
    {"type": "inbox", "unread": 12},
    {"type": "thread", "from": "Jo", "subject": "Venue contract", "request": "Can you sign it?"},
    {
        "type": "mentions",
        "groups": [{"channel": "#launch", "items": [{"from": "Alex", "text": "Copy ready?"}]}],
    },
    {"type": "files", "files": [{"name": "Q4 plan"}]},
    {"type": "quote", "text": "See you there."},
    {"type": "plan", "steps": [{"title": "Book the room"}]},
    {"type": "decision", "options": ["Hike", "Museum"], "criteria": ["Fun"], "scores": [[8], [6]]},
    {"type": "facts", "items": [{"label": "Plan", "value": "Pro"}]},
    {"type": "callout", "text": "Rent is due tomorrow."},
    {"type": "goal", "label": "Savings", "value": 4200, "target": 6000},
    {"type": "sources", "items": [{"title": "Rail pass guide", "site": "example.org"}]},
    {"type": "custom", "blocks": [{"type": "text", "text": "Hello."}]},
]

FULL: list[dict[str, Any]] = [
    {
        "type": "next_up",
        "title": "Next up",
        "event": "Design review",
        "start": "2026-10-25T14:30",
        "end": "2026-10-25T15:00",
        "where": "Maple Park room 2",
        "people": ["Sam", "Jo"],
        "note": "Bring the mockups.",
    },
    {
        "type": "free_time",
        "title": "Free this week",
        "slots": [
            {"start": "2026-10-28T14:00", "end": "2026-10-28T15:00"},
            {"start": "2026-10-28T09:00", "end": "2026-10-28T10:30"},
            {"start": "2026-10-29T11:30", "end": "2026-10-29T12:15"},
        ],
    },
    {
        "type": "inbox",
        "title": "Inbox",
        "unread": 12,
        "needs_reply": 3,
        "threads": [
            {
                "from": "Sam",
                "subject": "Budget for Q4",
                "when": "2026-10-25T08:10",
                "flag": "attention",
            },
            {"from": "Jo", "subject": "Lunch?"},
        ],
    },
    {
        "type": "thread",
        "title": "Waiting on you",
        "from": "Jo",
        "subject": "Venue contract",
        "request": "Can you sign the contract?",
        "due": "Friday",
        "when": "2026-10-24T16:00",
        "excerpt": "The venue needs it back by Friday to hold the date.",
    },
    {
        "type": "mentions",
        "title": "Mentions",
        "groups": [
            {
                "channel": "#launch",
                "items": [
                    {"from": "Alex", "text": "Can you check the copy?", "when": "2026-10-25T09:00"},
                    {"from": "Sam", "text": "Shipping Friday."},
                ],
            },
            {"channel": "#design", "items": [{"from": "Jo", "text": "New mockups are up."}]},
        ],
    },
    {
        "type": "files",
        "title": "Recent files",
        "files": [
            {"name": "Q4 plan", "kind": "Doc", "edited": "2026-10-24T17:20", "by": "Sam"},
            {"name": "Budget", "kind": "Sheet"},
        ],
    },
    {
        "type": "quote",
        "title": "From Jo",
        "text": "See you there.",
        "by": "Jo",
        "when": "2026-10-20",
    },
    {
        "type": "plan",
        "title": "Launch",
        "steps": [
            {"title": "Book the room", "done": True},
            {"title": "Send invites", "done": True},
            {"title": "Order food", "detail": "For 20 people"},
            {"title": "Print badges", "done": False},
            {"title": "Set up the room"},
        ],
    },
    {
        "type": "decision",
        "title": "Saturday",
        "options": ["Hike", "Museum"],
        "criteria": ["Fun", "Cost"],
        "scores": [[8, 6], [6, 9.5]],
        "pick": "Museum",
        "reason": "Cheaper and still fun.",
    },
    {
        "type": "facts",
        "title": "Your plan",
        "items": [{"label": "Plan", "value": "Pro"}, {"label": "Seats", "value": 5}],
    },
    {"type": "callout", "title": "Heads up", "text": "Rent is due tomorrow.", "tone": "attention"},
    {
        "type": "goal",
        "title": "Savings goal",
        "label": "Savings",
        "value": 4200,
        "target": 6000,
        "unit": "$",
        "by": "2026-11-30",
    },
    {
        "type": "sources",
        "title": "Sources",
        "items": [
            {"title": "Rail pass guide", "site": "example.org", "note": "Prices for 2026"},
            {"title": "Timetables", "site": "example.com"},
        ],
    },
    {
        "type": "custom",
        "title": "Who's on call",
        "blocks": [
            {"type": "rows", "items": [{"title": "Sam", "person": "Sam", "chip": "Primary"}]},
            {"type": "chips", "items": ["Pager"]},
        ],
    },
]

OLD_OF = {w["type"]: w for w in OLD}
MIN_OF = {w["type"]: w for w in MINIMAL}
FULL_OF = {w["type"]: w for w in FULL}
NEW = list(MIN_OF)
EVERY = OLD + MINIMAL + FULL


def refused(raw: object, at: int | str = 1) -> str:
    with pytest.raises(BoardError) as err:
        validate_widget(raw, at)
    return str(err.value)


def blocks_of(raw: dict[str, Any]) -> list[dict[str, Any]]:
    return expand(validate_widget(raw, 1))


# The kit takes them


def test_every_recipe_is_a_kit_type():
    assert set(NEW) | {w["type"] for w in OLD} == set(TYPES)
    assert {w["type"] for w in FULL} == set(NEW)


@pytest.mark.parametrize("raw", MINIMAL + FULL, ids=lambda w: w["type"])
def test_each_recipe_accepted(raw):
    out = validate_widget(raw, 1)
    assert out["type"] == raw["type"]
    assert "id" not in out


def test_full_fields_kept_and_extras_dropped():
    raw = {
        **FULL_OF["next_up"],
        "colour": "red",
        "source": "Calendar",
        "asks": ["Prep me for this"],
    }
    assert validate_widget(raw, 1) == {
        "type": "next_up",
        "size": "small",
        "title": "Next up",
        "event": "Design review",
        "start": "2026-10-25T14:30",
        "end": "2026-10-25T15:00",
        "where": "Maple Park room 2",
        "people": ["Sam", "Jo"],
        "note": "Bring the mockups.",
        "source": {"name": "Calendar"},
        "asks": ["Prep me for this"],
    }


def test_default_sizes():
    sizes = {w["type"]: validate_widget(w, 1)["size"] for w in MINIMAL}
    small = {"next_up", "goal", "callout", "facts", "quote"}
    wide = {"decision", "mentions"}
    for kind, size in sizes.items():
        want = "small" if kind in small else "wide" if kind in wide else "medium"
        assert size == want, kind
    assert validate_widget({**MIN_OF["next_up"], "size": "wide"}, 1)["size"] == "wide"
    assert validate_widget(OLD_OF["stat"], 1)["size"] == "medium"


def test_stat_takes_a_title_like_every_widget():
    assert validate_widget({**OLD_OF["stat"], "title": "October"}, 1)["title"] == "October"


# Source and asks, on every type


@pytest.mark.parametrize("raw", OLD + MINIMAL, ids=lambda w: w["type"])
def test_source_and_asks_on_every_type(raw):
    out = validate_widget({**raw, "source": "Calendar", "asks": ["Prep me for this"]}, 1)
    assert out["source"] == {"name": "Calendar"} and out["asks"] == ["Prep me for this"]


def test_source():
    ok = MIN_OF["next_up"]
    assert validate_widget({**ok, "source": {"name": "Gmail", "at": "2020-01-01T00:00:00Z"}}, 1)[
        "source"
    ] == {"name": "Gmail"}
    assert validate_widget({**ok, "source": "C" * 40}, 1)["source"] == {"name": "C" * 29 + "…"}
    assert "source" not in validate_widget({**ok, "source": "  "}, 1)
    assert "source" not in validate_widget({**ok, "source": None}, 1)
    assert refused({**ok, "source": {"at": "now"}}) == (
        "widget 1 (next_up): source: name is required."
    )
    assert refused({**ok, "source": 3}) == (
        'widget 1 (next_up): source must be a name, like "Calendar", or {name}.'
    )


def test_asks():
    ok = MIN_OF["next_up"]
    out = validate_widget({**ok, "asks": ["  Prep me\nfor this ", " ", "🎉", "Move it"]}, 1)
    assert out["asks"] == ["Prep me for this", "Move it"]
    assert validate_widget({**ok, "asks": "Prep me"}, 1)["asks"] == ["Prep me"]
    assert "asks" not in validate_widget({**ok, "asks": []}, 1)
    assert refused({**ok, "asks": ["a", "b", "c"]}) == "widget 1 (next_up): 3 asks, 2 at most."
    assert refused({**ok, "asks": ["x" * 41]}) == (
        "widget 1 (next_up): ask 1 is 41 characters, 40 at most."
    )
    assert refused({**ok, "asks": [3]}) == "widget 1 (next_up): ask 1 must be a string."
    assert refused({**ok, "asks": {"a": 1}}) == "widget 1 (next_up): asks must be a list."
    assert refused({**ok, "asks": ["Read example.com/a"]}) == (
        "widget 1 (next_up): ask 1 can't hold a web address; put links in sources."
    )
    assert refused({**ok, "asks": ["Prep me", "[host] Yes"]}) == (
        'widget 1 (next_up): ask 2 can\'t start with "["; write it as the user would say it.'
    )


# Emoji


_KEEP_AS_IS = {"type", "kind", "size", "tone", "flag", "start", "end", "when", "edited", "by"}


def _sprinkle(value: Any, key: str = "") -> Any:
    """The widget with an emoji before every free-text string."""
    if isinstance(value, str):
        return value if key in _KEEP_AS_IS else f"🎉 {value}✨"
    if isinstance(value, list):
        return [_sprinkle(v, key) for v in value]
    if isinstance(value, dict):
        return {k: _sprinkle(v, k) for k, v in value.items()}
    return value


@pytest.mark.parametrize("raw", OLD + FULL, ids=lambda w: w["type"])
def test_emoji_stripped_from_every_string(raw):
    noisy = _sprinkle({**raw, "source": "Calendar", "asks": ["Prep me"]})
    assert noisy != raw
    clean = {**raw, "source": "Calendar", "asks": ["Prep me"]}
    assert validate_widget(noisy, 1) == validate_widget(clean, 1)


# Near misses


@pytest.mark.parametrize(
    ("given", "meant"),
    [
        ("agenda", "timeline"),
        ("next", "next_up"),
        ("next_meeting", "next_up"),
        ("Next up", "next_up"),
        ("next-up", "next_up"),
        ("inbox_summary", "inbox"),
        ("email", "inbox"),
        ("checklist", "plan"),
        ("steps", "plan"),
        ("alert", "callout"),
        ("banner", "callout"),
        ("progress", "goal"),
    ],
)
def test_near_miss_names(given, meant):
    fixture = next(w for w in MINIMAL + OLD if w["type"] == meant)
    assert validate_widget({**fixture, "type": given}, 1)["type"] == meant


def test_a_checklist_is_a_plan_now():
    out = validate_widget({"type": "checklist", "steps": [{"title": "Milk", "done": True}]}, 1)
    assert out == {"type": "plan", "size": "medium", "steps": [{"title": "Milk", "done": True}]}


def test_types_read_from_telling_fields():
    assert validate_widget({"slots": MIN_OF["free_time"]["slots"]}, 1)["type"] == "free_time"
    assert validate_widget({"steps": [{"title": "a"}]}, 1)["type"] == "plan"
    decision = {k: v for k, v in MIN_OF["decision"].items() if k != "type"}
    assert validate_widget(decision, 1)["type"] == "decision"
    assert validate_widget({"blocks": [{"type": "text", "text": "a"}]}, 1)["type"] == "custom"


# Refusals name the widget, the field and the limit


def test_time_fields_are_iso():
    assert refused({**MIN_OF["next_up"], "start": "soon"}) == (
        "widget 1 (next_up): start 'soon' must be ISO 8601, like 2026-10-25T14:30 or 2026-10-25."
    )
    assert refused({**MIN_OF["next_up"], "end": "2026-10-25T14:00"}) == (
        "widget 1 (next_up): end 2026-10-25T14:00 is before start 2026-10-25T14:30."
    )
    files = {"type": "files", "files": [{"name": "a", "edited": "yesterday"}]}
    assert refused(files) == (
        "widget 1 (files): file 1: edited 'yesterday' must be ISO 8601, like 2026-10-25T14:30"
        " or 2026-10-25."
    )


def test_next_up_people_limit():
    assert refused({**MIN_OF["next_up"], "people": [f"P{i}" for i in range(9)]}) == (
        "widget 1 (next_up): 9 people, 8 at most."
    )


def test_free_time_slots():
    def slot(start: str, end: str) -> dict[str, Any]:
        return {"type": "free_time", "slots": [{"start": start, "end": end}]}

    assert refused(slot("2026-10-28", "2026-10-28")) == (
        "widget 1 (free_time): slot 1: start and end need a time, like 2026-10-28T09:00."
    )
    assert refused(slot("2026-10-28T22:00", "2026-10-29T01:00")) == (
        "widget 1 (free_time): slot 1: it ends on another day; give one slot per day."
    )
    assert refused(slot("2026-10-28T10:00", "2026-10-28T09:00")) == (
        "widget 1 (free_time): slot 1: end 2026-10-28T09:00 is not after start 2026-10-28T10:00."
    )
    assert refused({"type": "free_time", "slots": []}) == "widget 1 (free_time): slots is empty."


def test_inbox_counts():
    assert refused({"type": "inbox", "unread": -1}) == (
        "widget 1 (inbox): unread must be a whole number, 0 or more, not -1."
    )
    assert refused({"type": "inbox", "unread": 2.5}) == (
        "widget 1 (inbox): unread must be a whole number, 0 or more, not 2.5."
    )
    assert refused({"type": "inbox"}) == "widget 1 (inbox): unread is required."
    bad_flag = {"type": "inbox", "unread": 1, "threads": [{"from": "a", "subject": "b", "flag": 1}]}
    assert refused(bad_flag) == (
        "widget 1 (inbox): thread 1: flag must be one of good, bad, attention, neutral, not 1."
    )


def test_thread_due():
    out = validate_widget({**MIN_OF["thread"], "due": "2026-10-31"}, 1)
    assert out["due"] == "2026-10-31"
    assert refused({**MIN_OF["thread"], "due": "2026-13-01"}) == (
        "widget 1 (thread): due '2026-13-01' must be ISO 8601, like 2026-10-25T14:30 or 2026-10-25."
    )
    assert refused({**MIN_OF["thread"], "excerpt": "x" * 601}) == (
        "widget 1 (thread): excerpt is 601 characters, 600 at most."
    )


def test_mentions_limit_across_groups():
    items = [{"from": "a", "text": "b"}] * 16
    groups = [{"channel": "#a", "items": items}, {"channel": "#b", "items": items}]
    assert refused({"type": "mentions", "groups": groups}) == (
        "widget 1 (mentions): 32 messages across the groups, 30 at most."
    )


def test_plan_done_is_true_or_false():
    assert refused({"type": "plan", "steps": [{"title": "a", "done": "yes"}]}) == (
        "widget 1 (plan): step 1: done must be true or false."
    )


def test_decision_shapes():
    base = MIN_OF["decision"]
    assert refused({**base, "options": ["Hike"], "scores": [[8]]}) == (
        "widget 1 (decision): 1 option; a decision takes 2 to 8."
    )
    assert refused({**base, "options": ["Hike", "Hike"]}) == (
        "widget 1 (decision): option 'Hike' is named twice; names must differ."
    )
    assert refused({**base, "criteria": [f"C{i}" for i in range(7)]}) == (
        "widget 1 (decision): 7 criteria, 6 at most."
    )
    assert refused({**base, "scores": [[8]]}) == (
        "widget 1 (decision): scores has 1 row but there are 2 options; give one row per option."
    )
    assert refused({**base, "scores": [[8], [6, 7]]}) == (
        "widget 1 (decision): scores row 2 (Museum) has 2 scores but there is 1 criterion."
    )
    assert refused({**base, "scores": [[8], [11]]}) == (
        "widget 1 (decision): scores row 2 (Museum): Fun must be a number from 0 to 10, not 11."
    )
    assert refused({**base, "scores": "high"}) == "widget 1 (decision): scores must be a list."
    assert refused({**base, "pick": "Beach"}) == (
        "widget 1 (decision): pick 'Beach' is not an option; use one of Hike, Museum."
    )


def test_callout_and_goal_limits():
    assert refused({"type": "callout", "text": "x" * 201}) == (
        "widget 1 (callout): text is 201 characters, 200 at most."
    )
    assert refused({"type": "callout", "text": "a", "tone": "red"}) == (
        "widget 1 (callout): tone must be one of good, bad, attention, neutral, not 'red'."
    )
    assert refused({**MIN_OF["goal"], "target": 0}) == (
        "widget 1 (goal): target must be above 0, not 0."
    )
    assert refused({**MIN_OF["goal"], "value": "lots"}) == (
        "widget 1 (goal): value: 'lots' is not a number."
    )


def test_custom_runs_the_block_checks():
    assert refused({"type": "custom", "blocks": [{"type": "meter", "value": 1, "max": 0}]}) == (
        "widget 1 (custom): block 1 (meter): max must be above 0, not 0."
    )
    assert refused({"type": "custom"}, "w4") == "custom w4: blocks must be a list."


# What each recipe becomes


def test_expand_stat():
    raw = {**OLD_OF["stat"], "delta": "+12%", "hint": "vs September", "tone": "up"}
    assert blocks_of(raw) == [
        {
            "type": "value",
            "value": "$41.20",
            "label": "Total",
            "delta": "+12%",
            "hint": "vs September",
            "tone": "good",
        }
    ]
    assert blocks_of({**OLD_OF["stat"], "tone": "down"})[0]["tone"] == "bad"
    assert blocks_of({**OLD_OF["stat"], "tone": "neutral"})[0]["tone"] == "neutral"


def test_expand_chart_table_note_timeline():
    assert blocks_of({**OLD_OF["chart"], "unit": "$"}) == [
        {
            "type": "chart",
            "kind": "line",
            "x": ["Oct 1", "Oct 2"],
            "series": [{"name": "Spend", "values": [1.2, 3.4]}],
            "unit": "$",
        }
    ]
    assert blocks_of(OLD_OF["table"]) == [
        {"type": "table", "columns": ["Day", "Spend"], "rows": [["Mon", 3]]}
    ]
    assert blocks_of(OLD_OF["note"]) == [{"type": "text", "text": "One thing."}]
    assert blocks_of(OLD_OF["timeline"]) == [
        {"type": "timeline", "events": [{"title": "Standup", "start": "09:30"}]}
    ]


def test_expand_list():
    raw = {
        "type": "list",
        "items": [{"title": "Milk", "subtitle": "2 litres", "meta": 3, "done": True}],
    }
    assert blocks_of(raw) == [
        {
            "type": "rows",
            "items": [{"title": "Milk", "detail": "2 litres", "meta": "3", "done": True}],
        }
    ]


def test_expand_compare():
    raw = {
        "type": "compare",
        "options": [
            {"name": "Hike", "metrics": [{"label": "Fun", "score": 8}], "tags": ["Free"]},
            {"name": "Museum", "metrics": [{"label": "Fun", "score": 6}], "note": "Indoors"},
        ],
        "pick": "Hike",
        "reason": "Sunny all day.",
    }
    assert blocks_of(raw) == [
        {
            "type": "columns",
            "columns": [
                [
                    {"type": "value", "value": "Hike", "label": "Her pick"},
                    {"type": "meter", "value": 8, "max": 10, "label": "Fun"},
                    {"type": "chips", "items": ["Free"]},
                ],
                [
                    {"type": "value", "value": "Museum"},
                    {"type": "meter", "value": 6, "max": 10, "label": "Fun"},
                    {"type": "text", "text": "Indoors"},
                ],
            ],
        },
        {"type": "text", "text": "Sunny all day."},
    ]


def test_expand_next_up():
    assert blocks_of(FULL_OF["next_up"]) == [
        {
            "type": "time",
            "at": "2026-10-25T14:30",
            "end": "2026-10-25T15:00",
            "label": "Design review",
        },
        {"type": "people", "names": ["Sam", "Jo"]},
        {"type": "chips", "items": ["Maple Park room 2"]},
        {"type": "text", "text": "Bring the mockups."},
    ]
    assert blocks_of(MIN_OF["next_up"]) == [
        {"type": "time", "at": "2026-10-25T14:30", "label": "Design review"}
    ]


def test_expand_free_time_groups_by_the_date_as_written():
    assert blocks_of(FULL_OF["free_time"]) == [
        {
            "type": "rows",
            "items": [
                {"title": "Wed 28 Oct", "detail": "9:00\u201310:30 am, 2:00\u20133:00 pm"},
                {"title": "Thu 29 Oct", "detail": "11:30 am\u201312:15 pm"},
            ],
        }
    ]


@pytest.mark.parametrize(
    ("start", "end", "shown"),
    [
        ("09:00", "10:30", "9:00\u201310:30 am"),
        ("14:00", "15:00", "2:00\u20133:00 pm"),
        ("11:30", "12:15", "11:30 am\u201312:15 pm"),
        ("12:00", "12:45", "12:00\u201312:45 pm"),
        ("00:30", "01:00", "12:30\u20131:00 am"),
        ("19:05", "21:00", "7:05\u20139:00 pm"),
    ],
)
def test_free_time_ranges(start, end, shown):
    raw = {
        "type": "free_time",
        "slots": [{"start": f"2026-10-28T{start}", "end": f"2026-10-28T{end}"}],
    }
    assert blocks_of(raw)[0]["items"][0]["detail"] == shown


def test_expand_inbox():
    assert blocks_of(FULL_OF["inbox"]) == [
        {"type": "value", "value": "12", "label": "Unread", "hint": "3 need a reply"},
        {
            "type": "rows",
            "items": [
                {
                    "title": "Sam",
                    "detail": "Budget for Q4",
                    "person": "Sam",
                    "status": "attention",
                    "when": "2026-10-25T08:10",
                },
                {"title": "Jo", "detail": "Lunch?", "person": "Jo"},
            ],
        },
    ]
    assert blocks_of({**MIN_OF["inbox"], "needs_reply": 1})[0]["hint"] == "1 needs a reply"
    assert blocks_of({**MIN_OF["inbox"], "needs_reply": 0})[0]["hint"] == "None need a reply"
    assert blocks_of(MIN_OF["inbox"]) == [{"type": "value", "value": "12", "label": "Unread"}]


def test_expand_thread():
    assert blocks_of(FULL_OF["thread"]) == [
        {"type": "person", "name": "Jo", "detail": "Venue contract"},
        {"type": "text", "text": "Can you sign the contract?"},
        {"type": "facts", "items": [{"label": "Due", "value": "Friday"}]},
        {
            "type": "quote",
            "text": "The venue needs it back by Friday to hold the date.",
            "when": "2026-10-24T16:00",
        },
    ]
    due = {**MIN_OF["thread"], "due": "2026-10-31"}
    assert blocks_of(due)[2] == {
        "type": "facts",
        "items": [{"label": "Due", "value": "Sat 31 Oct"}],
    }
    due = {**MIN_OF["thread"], "due": "2026-10-31T17:00"}
    assert blocks_of(due)[2]["items"][0]["value"] == "Sat 31 Oct, 5:00 pm"


def test_expand_mentions():
    assert blocks_of(FULL_OF["mentions"]) == [
        {
            "type": "rows",
            "label": "#launch",
            "items": [
                {
                    "title": "Alex",
                    "detail": "Can you check the copy?",
                    "person": "Alex",
                    "when": "2026-10-25T09:00",
                },
                {"title": "Sam", "detail": "Shipping Friday.", "person": "Sam"},
            ],
        },
        {
            "type": "rows",
            "label": "#design",
            "items": [{"title": "Jo", "detail": "New mockups are up.", "person": "Jo"}],
        },
    ]


def test_expand_files():
    assert blocks_of(FULL_OF["files"]) == [
        {
            "type": "rows",
            "items": [
                {"title": "Q4 plan", "detail": "by Sam", "when": "2026-10-24T17:20", "chip": "Doc"},
                {"title": "Budget", "chip": "Sheet"},
            ],
        }
    ]


def test_expand_quote_and_facts():
    assert blocks_of(FULL_OF["quote"]) == [
        {"type": "quote", "text": "See you there.", "by": "Jo", "when": "2026-10-20"}
    ]
    assert blocks_of(FULL_OF["facts"]) == [
        {
            "type": "facts",
            "items": [{"label": "Plan", "value": "Pro"}, {"label": "Seats", "value": "5"}],
        }
    ]


def test_expand_plan():
    assert blocks_of(FULL_OF["plan"]) == [
        {"type": "meter", "value": 2, "max": 5, "label": "2 of 5 done"},
        {
            "type": "rows",
            "items": [
                {"title": "Book the room", "done": True},
                {"title": "Send invites", "done": True},
                {"title": "Order food", "detail": "For 20 people", "done": False},
                {"title": "Print badges", "done": False},
                {"title": "Set up the room", "done": False},
            ],
        },
    ]


def test_expand_decision():
    assert blocks_of(FULL_OF["decision"]) == [
        {
            "type": "table",
            "columns": ["Option", "Fun", "Cost", "Total"],
            "rows": [["Hike", 8, 6, 14], ["Museum", 6, 9.5, 15.5]],
            "highlight": 1,
        },
        {"type": "text", "text": "Cheaper and still fun."},
    ]
    assert blocks_of(MIN_OF["decision"]) == [
        {
            "type": "table",
            "columns": ["Option", "Fun", "Total"],
            "rows": [["Hike", 8, 8], ["Museum", 6, 6]],
        }
    ]
    tenths = {**MIN_OF["decision"], "criteria": ["A", "B"], "scores": [[0.1, 0.2], [1, 2]]}
    assert blocks_of(tenths)[0]["rows"][0][-1] == 0.3


def test_expand_callout():
    assert blocks_of(FULL_OF["callout"]) == [
        {"type": "text", "text": "Rent is due tomorrow.", "tone": "attention"}
    ]
    assert blocks_of(MIN_OF["callout"]) == [
        {"type": "text", "text": "Rent is due tomorrow.", "tone": "neutral"}
    ]
    assert blocks_of({**MIN_OF["callout"], "tone": "down"})[0]["tone"] == "bad"


def test_expand_goal():
    assert blocks_of(FULL_OF["goal"]) == [
        {"type": "meter", "value": 4200, "max": 6000, "label": "Savings", "shape": "ring"},
        {"type": "value", "value": "$4,200", "hint": "of $6,000 by Nov 30"},
    ]
    km = {**MIN_OF["goal"], "value": 42.5, "target": 100, "unit": "km", "by": "the end of the year"}
    assert blocks_of(km)[1] == {
        "type": "value",
        "value": "42.5 km",
        "hint": "of 100 km by the end of the year",
    }
    assert blocks_of({**MIN_OF["goal"], "unit": "%"})[1]["value"] == "4,200%"
    assert blocks_of({**MIN_OF["goal"], "unit": "€"})[1]["hint"] == "of €6,000"


def test_expand_sources():
    assert blocks_of(FULL_OF["sources"]) == [
        {
            "type": "rows",
            "items": [
                {"title": "Rail pass guide", "detail": "Prices for 2026", "meta": "example.org"},
                {"title": "Timetables", "meta": "example.com"},
            ],
        }
    ]


def test_expand_custom():
    assert blocks_of(FULL_OF["custom"]) == [
        {"type": "rows", "items": [{"title": "Sam", "person": "Sam", "chip": "Primary"}]},
        {"type": "chips", "items": ["Pager"]},
    ]


@pytest.mark.parametrize("raw", EVERY, ids=lambda w: w["type"])
def test_every_expansion_is_valid_blocks(raw):
    blocks = blocks_of(raw)
    assert blocks
    # Already in the blocks' own form: checking them changes nothing.
    assert validate_blocks(copy.deepcopy(blocks), "widget 1") == blocks


def test_expand_does_not_share_lists_with_the_widget():
    widget = validate_widget(OLD_OF["chart"], 1)
    expand(widget)[0]["x"].append("Oct 3")
    assert widget["x"] == ["Oct 1", "Oct 2"]


# Boards


def test_board_saved_with_todays_types_validates_and_expands():
    saved = b.show(None, "October", OLD)
    sent = expand_board(saved)
    assert sent["title"] == "October" and sent["open"] is True
    assert [w["id"] for w in sent["widgets"]] == [f"w{i}" for i in range(1, 8)]
    for widget in sent["widgets"]:
        assert set(widget) <= {"id", "type", "size", "title", "source", "asks", "blocks"}
        assert validate_blocks(copy.deepcopy(widget["blocks"]), widget["id"]) == widget["blocks"]
    assert sent["widgets"][1] == {
        "id": "w2",
        "type": "chart",
        "size": "medium",
        "title": "Daily spend",
        "blocks": blocks_of(OLD_OF["chart"]),
    }
    assert "blocks" not in saved["widgets"][0]


def test_expanded_widget_keeps_its_name_and_drops_recipe_fields():
    saved = b.show(
        None, "Day", [{**FULL_OF["next_up"], "source": "Calendar", "asks": ["Prep me for this"]}]
    )
    assert expand_board(saved)["widgets"][0] == {
        "id": "w1",
        "type": "next_up",
        "size": "small",
        "title": "Next up",
        "source": {"name": "Calendar"},
        "asks": ["Prep me for this"],
        "blocks": blocks_of(FULL_OF["next_up"]),
    }


def test_summary_names_the_new_types():
    board = b.show(
        None,
        "Today",
        [
            MIN_OF["next_up"],
            FULL_OF["plan"],
            MIN_OF["goal"],
            MIN_OF["thread"],
            FULL_OF["custom"],
            MIN_OF["inbox"],
        ],
    )
    assert b.summary(board) == (
        'Board (open): "Today": w1 next up "Design review", w2 plan "Launch", '
        'w3 goal "Savings", w4 thread "Venue contract", w5 custom "Who\'s on call", w6 inbox.'
    )


# Deadlines in words, even ones that start with a digit


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ({**MIN_OF["goal"], "unit": "$", "by": "30 Nov"}, "of $6,000 by 30 Nov"),
        ({**MIN_OF["goal"], "by": "2027"}, "of 6,000 by 2027"),
    ],
)
def test_goal_by_in_words_that_start_with_a_digit(raw, shown):
    assert blocks_of(raw)[1]["hint"] == shown


@pytest.mark.parametrize("due", ["2nd Friday", "5pm today", "30 Nov"])
def test_thread_due_in_words_that_start_with_a_digit(due):
    facts = blocks_of({**MIN_OF["thread"], "due": due})[2]
    assert facts == {"type": "facts", "items": [{"label": "Due", "value": due}]}


def test_is_date_is_the_one_rule():
    assert is_date("2026-10-31") and is_date("2026-10-31T17:00")
    assert not is_date("30 Nov") and not is_date("2nd Friday") and not is_date("2027")


def test_one_bad_widget_does_not_take_the_board_down(caplog):
    saved = b.show(None, "Day", [OLD_OF["stat"], MIN_OF["goal"]])
    # As if saved by an older kit: a field the recipe cannot read.
    saved["widgets"][1]["by"] = "2026-99-99"
    with caplog.at_level(logging.WARNING, logger="trillion.workspace.recipes"):
        sent = expand_board(saved)
    assert sent["widgets"][0]["blocks"] == blocks_of(OLD_OF["stat"])
    # The card still shows its title.
    assert sent["widgets"][1] == {
        "id": "w2",
        "type": "goal",
        "size": "small",
        "title": "Savings",
        "blocks": [],
    }
    assert "could not expand widget w2 (goal)" in caplog.text


# Near misses that follow the fields


def test_an_alias_without_its_key_field_follows_the_fields():
    out = validate_widget({"type": "checklist", "items": [{"title": "Milk", "done": True}]}, 1)
    assert out == {"type": "list", "size": "medium", "items": [{"title": "Milk", "done": True}]}
    assert validate_widget({"type": "progress", "label": "Done", "value": 3}, 1)["type"] == "stat"
    assert validate_widget({"type": "checklist", "steps": [{"title": "a"}]}, 1)["type"] == "plan"


@pytest.mark.parametrize(
    "kind", ["inbox", "mentions", "files", "next_up", "goal", "thread", "free_time", "plan"]
)
def test_more_types_read_from_their_fields(kind):
    untyped = {k: v for k, v in FULL_OF[kind].items() if k != "type"}
    assert validate_widget(untyped, 1)["type"] == kind


def test_inbox_read_from_threads_alone():
    threads = FULL_OF["inbox"]["threads"]
    assert refused({"threads": threads}) == "widget 1 (inbox): unread is required."


# Options in the other shape


def test_decision_takes_named_options():
    named = {**MIN_OF["decision"], "options": [{"name": "Hike"}, {"name": "Museum"}]}
    assert validate_widget(named, 1)["options"] == ["Hike", "Museum"]


def test_decision_given_a_compares_options():
    compared = {**MIN_OF["decision"], "options": OLD_OF["compare"]["options"]}
    assert refused(compared) == (
        "widget 1 (decision): option 1 has metrics, a compare's shape; a decision takes"
        " options as names, criteria, and scores with one row per option."
    )


def test_compare_given_names():
    assert refused({"type": "compare", "options": ["Hike", "Museum"]}) == (
        "widget 1 (compare): option 1: an option is {name, metrics};"
        " for options scored against criteria use decision."
    )


# Smaller rules


@pytest.mark.parametrize(
    ("tone", "drawn"),
    [("up", "good"), ("down", "bad"), ("good", "good"), ("bad", "bad"), ("attention", "attention")],
)
def test_stat_tones(tone, drawn):
    assert blocks_of({**OLD_OF["stat"], "tone": tone})[0]["tone"] == drawn


def test_free_time_is_local_and_not_empty():
    def slot(start: str, end: str) -> dict[str, Any]:
        return {"type": "free_time", "slots": [{"start": start, "end": end}]}

    local = (
        "widget 1 (free_time): slot 1: write slots in the user's local time, like 2026-10-28T09:00."
    )
    assert refused(slot("2026-10-28T09:00Z", "2026-10-28T10:00Z")) == local
    assert refused(slot("2026-10-28T09:00", "2026-10-28T10:00-07:00")) == local
    assert refused(slot("2026-10-28T09:00", "2026-10-28T09:00")) == (
        "widget 1 (free_time): slot 1: end 2026-10-28T09:00 is not after start 2026-10-28T09:00."
    )


def test_thread_when_without_an_excerpt_is_a_fact():
    sent = {**MIN_OF["thread"], "due": "Friday", "when": "2026-10-24T16:00"}
    assert blocks_of(sent)[2] == {
        "type": "facts",
        "items": [
            {"label": "Due", "value": "Friday"},
            {"label": "Sent", "value": "Sat 24 Oct, 4:00 pm"},
        ],
    }
    assert "facts" not in [blk["type"] for blk in blocks_of(MIN_OF["thread"])]


def test_goal_negative_amounts():
    debt = {**MIN_OF["goal"], "value": -5, "unit": "$"}
    assert blocks_of(debt)[1]["value"] == "-$5"
    assert blocks_of({**debt, "unit": "km"})[1]["value"] == "-5 km"


def test_note_lines_are_trimmed_like_a_text_block():
    assert validate_widget({"type": "note", "text": "a  \n   - b"}, 1)["text"] == "a\n- b"


# Default titles: what a card is called when she gave it none


@pytest.mark.parametrize(
    ("raw", "title"),
    [
        (MIN_OF["next_up"], "Next up"),
        (MIN_OF["free_time"], "Free time"),
        (MIN_OF["inbox"], "Inbox"),
        (MIN_OF["mentions"], "Mentions"),
        (MIN_OF["files"], "Files"),
        (MIN_OF["plan"], "Plan"),
        (MIN_OF["decision"], "Decision"),
        (MIN_OF["sources"], "Sources"),
        (MIN_OF["facts"], "Details"),
        (MIN_OF["goal"], "Savings"),
        (MIN_OF["thread"], "Venue contract"),
    ],
    ids=lambda v: v["type"] if isinstance(v, dict) else v,
)
def test_untitled_catalogue_widgets_get_a_title_when_sent(raw, title):
    saved = b.show(None, "Day", [raw])
    assert "title" not in saved["widgets"][0]  # the default is not saved
    assert expand_board(saved)["widgets"][0]["title"] == title


@pytest.mark.parametrize(
    "raw",
    [*OLD, MIN_OF["quote"], MIN_OF["callout"], MIN_OF["custom"]],
    ids=lambda w: w["type"],
)
def test_widgets_named_by_their_first_block_stay_untitled(raw):
    if "title" in raw:
        raw = {k: v for k, v in raw.items() if k != "title"}
    assert "title" not in expand_board(b.show(None, "Day", [raw]))["widgets"][0]


def test_her_title_wins_over_the_default():
    saved = b.show(None, "Day", [{**MIN_OF["inbox"], "title": "Work mail"}])
    assert expand_board(saved)["widgets"][0]["title"] == "Work mail"


def test_a_title_from_a_field_is_not_said_twice():
    # A thread titled by its subject does not repeat it under the sender.
    assert blocks_of(MIN_OF["thread"])[0] == {"type": "person", "name": "Jo"}
    # A goal titled by its label does not repeat it under the ring.
    assert blocks_of(MIN_OF["goal"])[0] == {
        "type": "meter",
        "value": 4200,
        "max": 6000,
        "shape": "ring",
    }


# ask and asks


def test_a_lone_ask_becomes_an_entry_of_asks():
    inbox = validate_widget({**MIN_OF["inbox"], "ask": "Draft a reply to Sam"}, 1)
    assert inbox["asks"] == ["Draft a reply to Sam"] and "ask" not in inbox
    both = validate_widget({**MIN_OF["inbox"], "asks": "Who is first?", "ask": "Draft a reply"}, 1)
    assert both["asks"] == ["Who is first?", "Draft a reply"]
    listed = validate_widget({**MIN_OF["inbox"], "ask": ["Draft a reply"]}, 1)
    assert listed["asks"] == ["Draft a reply"]
    assert refused({**MIN_OF["inbox"], "asks": ["a", "b"], "ask": "c"}) == (
        "widget 1 (inbox): 3 asks, 2 at most."
    )


def test_on_a_thread_a_lone_ask_is_the_request():
    old = {"type": "thread", "from": "Jo", "subject": "Venue contract", "ask": "Can you sign it?"}
    out = validate_widget(old, 1)
    assert out["request"] == "Can you sign it?" and "ask" not in out and "asks" not in out
    # With a request already, an ask is a tap like on any widget.
    tapped = validate_widget({**MIN_OF["thread"], "ask": "Draft a reply"}, 1)
    assert tapped["request"] == "Can you sign it?" and tapped["asks"] == ["Draft a reply"]
    assert refused({"type": "thread", "from": "Jo", "subject": "x"}) == (
        "widget 1 (thread): request is required."
    )


def test_a_thread_saved_with_ask_still_expands():
    saved = {"type": "thread", "size": "medium", "from": "Jo", "subject": "S", "ask": "Sign it?"}
    assert expand(saved)[1] == {"type": "text", "text": "Sign it?"}


# Bare clock times are today


@pytest.fixture
def on_oct_25(monkeypatch):
    monkeypatch.setattr(blocks_mod, "today", lambda: date(2026, 10, 25))


@pytest.mark.usefixtures("on_oct_25")
def test_bare_times_in_recipes_are_today():
    out = validate_widget({**MIN_OF["next_up"], "start": "9:12", "end": "10am"}, 1)
    assert (out["start"], out["end"]) == ("2026-10-25T09:12", "2026-10-25T10:00")
    inbox = {"type": "inbox", "unread": 1, "threads": [{"from": "Sam", "subject": "Lease"}]}
    inbox["threads"][0]["when"] = "9:12"
    assert validate_widget(inbox, 1)["threads"][0]["when"] == "2026-10-25T09:12"
    thread = validate_widget({**MIN_OF["thread"], "when": "3pm"}, 1)
    assert thread["when"] == "2026-10-25T15:00"
    files = validate_widget({"type": "files", "files": [{"name": "a", "edited": "15:30"}]}, 1)
    assert files["files"][0]["edited"] == "2026-10-25T15:30"
    mention = {"channel": "#a", "items": [{"from": "Jo", "text": "hi", "when": "09:05 am"}]}
    got = validate_widget({"type": "mentions", "groups": [mention]}, 1)
    assert got["groups"][0]["items"][0]["when"] == "2026-10-25T09:05"


@pytest.mark.usefixtures("on_oct_25")
def test_a_free_time_slot_needs_its_day():
    raw = {"type": "free_time", "slots": [{"start": "9:00", "end": "2026-10-28T10:00"}]}
    assert refused(raw) == (
        "widget 1 (free_time): slot 1: start '9:00' has no date; a slot is on a day, so write it"
        " like 2026-10-28T09:00."
    )
