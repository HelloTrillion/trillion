"""A helper's picture: kept when she names one on the lists, else picked from the job
and the name, the same each time."""

from __future__ import annotations

from trillion.helpers.look import COLORS, ICONS, pick


def test_the_lists() -> None:
    assert len(ICONS) == 24 and len(set(ICONS)) == 24 and "spark" in ICONS
    assert len(COLORS) == 8 and len(set(COLORS)) == 8


def test_a_valid_icon_and_colour_are_kept() -> None:
    assert pick("Invoices", "Chases overdue invoices.", "leaf", "rose") == ("leaf", "rose")


def test_the_icon_comes_from_the_job() -> None:
    assert pick("A", "Chases overdue invoices.")[0] == "receipt"
    assert pick("A", "Keeps my inbox tidy.")[0] == "envelope"
    assert pick("A", "Plans each meeting.")[0] == "calendar"
    assert pick("A", "Sums up the weekly reports.")[0] == "chart"
    assert pick("A", "Does the thing.")[0] == "spark"


def test_the_colour_is_stable_and_from_the_name() -> None:
    first = pick("Invoices", "x")[1]
    assert first in COLORS
    assert pick("Invoices", "something else")[1] == first
    # Case set aside, as names are.
    assert pick("INVOICES", "x")[1] == first
    # Not every name gets the same one.
    assert len({pick(f"Helper {i}", "x")[1] for i in range(40)}) > 1


def test_names_not_on_the_lists_are_replaced() -> None:
    icon, color = pick("Invoices", "Chases overdue invoices.", "skull", "neon")
    assert icon == "receipt" and color == pick("Invoices", "x")[1]
    assert pick("Invoices", "Chases overdue invoices.", 3, None)[0] == "receipt"
