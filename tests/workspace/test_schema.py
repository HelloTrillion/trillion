"""The widget schema her tools carry: flat, complete, and never stricter than the kit."""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tests.workspace.test_recipes import EVERY
from trillion.workspace.blocks import BLOCK_TYPES
from trillion.workspace.schema import BLOCK_CARDS, CARDS, widget_schema
from trillion.workspace.widgets import TYPES

# Every field a recipe reads, by type; the flat schema must name each one.
FIELDS = {
    "stat": ("label", "value", "delta", "hint", "tone"),
    "chart": ("kind", "x", "series", "unit"),
    "list": ("items",),
    "table": ("columns", "rows"),
    "note": ("text",),
    "timeline": ("events",),
    "compare": ("options", "pick", "reason"),
    "next_up": ("event", "start", "end", "where", "people", "note"),
    "free_time": ("slots",),
    "inbox": ("unread", "needs_reply", "threads"),
    "thread": ("from", "subject", "ask", "due", "when", "excerpt"),
    "mentions": ("groups",),
    "files": ("files",),
    "quote": ("text", "by", "when"),
    "plan": ("steps",),
    "decision": ("options", "criteria", "scores", "pick", "reason"),
    "facts": ("items",),
    "callout": ("text", "tone"),
    "goal": ("label", "value", "target", "unit", "by"),
    "sources": ("items",),
    "custom": ("blocks",),
}


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
    assert list(BLOCK_CARDS) == list(BLOCK_TYPES)


def test_still_flat() -> None:
    keys = _keys(widget_schema())
    for word in ("oneOf", "anyOf", "allOf", "enum", "$ref"):
        assert word not in keys


def test_common_fields() -> None:
    props = widget_schema()["properties"]
    assert "string" in props["source"]["type"]
    assert props["asks"]["type"] == "array" and props["asks"]["items"] == {"type": "string"}
    blocks = props["blocks"]
    assert blocks["type"] == "array"
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


def test_with_source_and_asks() -> None:
    raw = {**EVERY[0], "source": "Calendar", "asks": ["Why?"]}
    assert list(Draft202012Validator(widget_schema()).iter_errors(raw)) == []


def test_serialisable() -> None:
    json.dumps(widget_schema())
