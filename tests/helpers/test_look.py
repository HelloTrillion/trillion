"""A helper's picture: kept when she names one on the lists, else picked from the job
and the name, the same each time."""

from __future__ import annotations

from trillion.helpers.look import COLORS, ICONS, pick


def test_the_lists() -> None:
    assert len(ICONS) == 24 and len(set(ICONS)) == 24 and "spark" in ICONS
    assert len(COLORS) == 11 and len(set(COLORS)) == 11


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


def test_a_new_helper_passes_over_colours_its_others_have() -> None:
    _, first = pick("Socials", "I post for you.")
    # The colour it would have is taken: the next free one, never a match.
    _, second = pick("Socials", "I post for you.", taken={first})
    assert second != first
    # One it was given that is taken is passed over too.
    _, given = pick("Socials", "I post for you.", color="blue", taken={"blue"})
    assert given != "blue"
    # A free one given is kept.
    assert pick("Socials", "I post for you.", color="lime", taken={"blue"})[1] == "lime"
    # With every colour used, it keeps its own.
    assert pick("Socials", "I post for you.", taken=set(COLORS))[1] == first
