"""The widget schema her tools carry: flat, complete, and never stricter than the kit."""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tests.workspace import test_pack_widgets, test_recipes
from trillion.workspace.blocks import BLOCK_TYPES
from trillion.workspace.packs import PACK_OF, PACKS, told
from trillion.workspace.schema import BLOCK_CARDS, CARDS, FIELDS, type_cards, widget_schema
from trillion.workspace.widgets import TYPES, validate_widget

EVERY = test_recipes.EVERY + test_pack_widgets.EVERY


def _keys(node: Any) -> set[str]:
    """Every key used anywhere in a schema, so a nested oneOf is found too."""
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in _keys(v)}
    if isinstance(node, list):
        return {k for v in node for k in _keys(v)}
    return set()


def test_every_type_is_named_and_has_its_fields() -> None:
    schema = widget_schema()
    props = schema["properties"]
    assert set(FIELDS) == set(TYPES)
    for kind, fields in FIELDS.items():
        assert kind in props["type"]["description"]
        for field in fields:
            assert field in props, f"{kind}: {field}"


def test_a_card_for_every_type_and_block() -> None:
    assert list(CARDS) == list(TYPES)
    assert list(FIELDS) == list(TYPES)
    assert list(BLOCK_CARDS) == list(BLOCK_TYPES)


def test_only_the_told_types_and_their_fields_are_named() -> None:
    core = widget_schema(told([]))["properties"]
    assert "transactions" not in core["type"]["description"]
    assert "kpis" in core["type"]["description"]
    for field in ("who", "sent", "burndown", "issues", "current", "waiting"):
        assert field not in core, field
    # A field a core type shares with a pack keeps the shape both take.
    assert "object" in core["columns"]["items"]["type"]
    money = widget_schema(told(["stripe"]))["properties"]
    assert "total" in money and "sent" not in money
    assert set(widget_schema()["properties"]) == set(widget_schema(told(PACK_OF))["properties"])


def test_the_cards_list_only_the_told_types_and_every_block() -> None:
    core = type_cards(told([]))
    assert "\ntasks: " in core and "\ntransactions: " not in core
    assert core.rstrip(".").endswith(
        "stages {steps[], current (a step or its index from 0), tone?}"
    )
    for block in BLOCK_TYPES:
        assert f"{block} {{" in core, block
    every = type_cards(told(PACK_OF))
    for kinds in PACKS.values():
        for kind in kinds:
            assert f"\n{kind}: " in every, kind
    # Custom, the fallback, comes last.
    assert every.index("\ncustom: ") > every.index("\ndue_soon: ")


@pytest.mark.parametrize("raw", test_pack_widgets.EVERY, ids=lambda w: w["type"])
def test_a_pack_widget_passes_the_core_schema(raw: dict[str, Any]) -> None:
    """She may write a pack's widget with no pack told (she was told the numbers, or a
    board was saved earlier): the schema lists fewer fields, but refuses none."""
    errors = list(Draft202012Validator(widget_schema(told([]))).iter_errors(raw))
    assert errors == [], [e.message for e in errors]


def test_still_flat() -> None:
    keys = _keys(widget_schema())
    for word in ("oneOf", "anyOf", "allOf", "enum", "$ref"):
        assert word not in keys


def test_common_fields() -> None:
    props = widget_schema()["properties"]
    assert "string" in props["source"]["type"]
    assert "array" in props["asks"]["type"] and props["asks"]["items"] == {"type": "string"}
    blocks = props["blocks"]
    assert "array" in blocks["type"]
    item = blocks["items"]
    assert item["type"] == "object" and item["required"] == ["type"]
    assert item["properties"]["type"]["type"] == "string"
    # Loose: each block's own fields are the kit's to check.
    assert item.get("additionalProperties", True) is True


def test_shared_fields_say_their_shapes() -> None:
    props = widget_schema()["properties"]
    options = props["options"]["description"]
    assert "decision" in options and "compare" in options and "metrics" in options


@pytest.mark.parametrize("raw", EVERY, ids=lambda w: w["type"])
def test_never_refuses_what_the_kit_takes(raw: dict[str, Any]) -> None:
    """The router checks tool input against the schema before the kit sees it, so a
    widget the kit takes must pass the schema too."""
    errors = list(Draft202012Validator(widget_schema()).iter_errors(raw))
    assert errors == [], [e.message for e in errors]


# What small models send that the kit takes; the schema is checked first, so it must
# take them too.
LENIENT: list[dict[str, Any]] = [
    {"type": "stat", "label": "Total", "value": "$41.20", "asks": "Why so high?"},
    {"type": "stat", "label": 2026, "value": 26, "delta": None, "hint": None, "tone": None},
    {
        "type": "next_up",
        "event": "Design review",
        "start": "2026-10-25T14:30",
        "people": ["Sam", 2],
        "end": None,
        "where": None,
        "note": None,
        "title": None,
        "size": None,
        "source": None,
        "asks": None,
    },
    {"type": "decision", "options": [1, 2], "criteria": [2026, "Fun"], "scores": [[1, 2], [3, 4]]},
    {
        "type": "decision",
        "options": ["Hike", {"name": "Museum"}],
        "criteria": ["Fun"],
        "scores": [[8], [6]],
        "pick": None,
        "reason": None,
    },
    {"type": "goal", "label": "Savings", "value": 4200, "target": 6000, "unit": None, "by": None},
    {"event": "Design review", "start": "2026-10-25T14:30"},
    {"type": None, "steps": [{"title": "Book the room"}]},
    {"type": "callout", "text": "Rent is due tomorrow.", "tone": None},
    {"type": "inbox", "unread": 3, "needs_reply": None, "threads": None},
    {"type": "thread", "from": "Jo", "subject": "Venue", "request": "Sign?", "due": None},
    # One ask in the singular, as small models write it; the kit reads it as asks.
    {"type": "thread", "from": "Jo", "subject": "Venue", "ask": "Sign?"},
    {"type": "inbox", "unread": 3, "ask": "Draft a reply to Sam"},
    # Counts and goals written as text.
    {"type": "inbox", "unread": "12", "needs_reply": "3"},
    {"type": "goal", "label": "Savings", "value": "4,200", "target": "6000"},
    {
        "type": "chart",
        "kind": "line",
        "x": ["Oct 1", 2],
        "series": [{"name": "Spend", "values": [1, 2]}],
        "unit": None,
    },
]


@pytest.mark.parametrize("raw", LENIENT)
def test_what_the_kit_takes_the_schema_takes(raw: dict[str, Any]) -> None:
    validate_widget(raw, 1)
    errors = list(Draft202012Validator(widget_schema()).iter_errors(raw))
    assert errors == [], [e.message for e in errors]


def test_asks_is_advertised_and_ask_is_not() -> None:
    props = widget_schema()["properties"]
    assert "asks" in props and "ask" not in props
    assert "request" in CARDS["thread"] and "ask" not in CARDS["thread"]


def test_with_source_and_asks() -> None:
    raw = {**EVERY[0], "source": "Calendar", "asks": ["Why?"]}
    assert list(Draft202012Validator(widget_schema()).iter_errors(raw)) == []


def test_serialisable() -> None:
    json.dumps(widget_schema())
