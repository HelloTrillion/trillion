"""Words for times and numbers on a card, as every recipe writes them: "Wed 28 Oct",
"9:00\u201310:30 am", "$4,200". Pure, no I/O.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from trillion.workspace.blocks import Block
from trillion.workspace.recipe_kit import is_date

_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_DASH = "\u2013"  # an en dash, as a range of times is printed
# Written before the amount ("$4,200"); every other unit after it ("42 km").
_PREFIX = ("$", "€", "£")


def day(text: str) -> str:
    """'2026-10-28…' → 'Wed 28 Oct'."""
    d = date.fromisoformat(text[:10])
    return f"{_DAYS[d.weekday()]} {d.day} {_MONTHS[d.month - 1]}"


def clock(text: str) -> tuple[str, str]:
    """'…T14:05' → ('2:05', 'pm')."""
    hour, minute = int(text[11:13]), text[14:16]
    return f"{hour % 12 or 12}:{minute}", "am" if hour < 12 else "pm"


def span(start: str, end: str) -> str:
    """9:00 to 10:30 am, 2:00 to 3:00 pm, 11:30 am to 12:15 pm, joined by an en dash:
    the half of the day said once when both ends share it, as the page's ranges are."""
    (a, a_half), (b, b_half) = clock(start), clock(end)
    if a_half != b_half:
        return f"{a} {a_half}{_DASH}{b} {b_half}"
    return f"{a}{_DASH}{b} {b_half}"


def deadline(text: str) -> str:
    """An ISO date as 'Sat 31 Oct' (with ', 5:00 pm' when it has a time); words as written."""
    if not is_date(text):
        return text
    if "T" not in text:
        return day(text)
    time, half = clock(text)
    return f"{day(text)}, {time} {half}"


def by_date(text: str) -> str:
    """A goal's date as 'Nov 30'; words as written."""
    if not is_date(text):
        return text
    d = date.fromisoformat(text[:10])
    return f"{_MONTHS[d.month - 1]} {d.day}"


def figure(n: float | int) -> str:
    """4200 → '4,200'; 42.5 → '42.5'; 1/3 → '0.33'."""
    if float(n).is_integer():
        return f"{int(n):,}"
    return f"{n:,.2f}".rstrip("0").rstrip(".")


def amount(n: float | int, unit: str | None) -> str:
    """'$4,200', '-$5', '42 km', '12%'."""
    if not unit:
        return figure(n)
    if unit in _PREFIX:
        sign = "-" if n < 0 else ""
        return f"{sign}{unit}{figure(abs(n))}"
    return f"{figure(n)}{'' if unit == '%' else ' '}{unit}"


def with_fields(out: Block, **fields: Any) -> Block:
    """The block with the fields that have a value."""
    out.update({k: v for k, v in fields.items() if v is not None})
    return out
