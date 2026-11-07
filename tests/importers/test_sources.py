"""Splitting the other apps' notes into entries, and reading their fields."""

from __future__ import annotations

from trillion.importers.sources import field_of, split_hermes, split_markdown


def test_markdown_splits_by_item_and_paragraph() -> None:
    text = (
        "---\ntitle: notes\n---\n"
        "# Heading\n\n"
        "*A template hint.*\n\n"
        "- one\n"
        "  - one, detail\n"
        "* two\n"
        "1. three\n\n"
        "A paragraph\nthat wraps.\n\n"
        "---\n\n"
        "<!-- a comment -->\n"
        "```\ncode\n\nmore code\n```\n"
    )
    assert split_markdown(text) == [
        "one\n- one, detail",
        "two",
        "three",
        "A paragraph\nthat wraps.",
        "```\ncode\n\nmore code\n```",
    ]


def test_hermes_splits_on_the_section_sign_only() -> None:
    assert split_hermes("a\n§\nb\nstill b\n  §  \n\nc §\n§\n") == ["a", "b\nstill b", "c §"]


def test_fields_read_the_usual_shapes() -> None:
    assert field_of("- **Name:** Sam") == ("Name", "Sam")
    assert field_of("**What to call them**: Sam") == ("What to call them", "Sam")
    assert field_of("Timezone: Europe/Berlin") == ("Timezone", "Europe/Berlin")
    assert field_of("- **Notes:**") == ("Notes", "")
    assert field_of("- **Name:** _(optional)_") == ("Name", "")
    assert field_of("Just a sentence.") is None
