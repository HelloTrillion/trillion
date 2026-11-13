"""`trillion doctor --seamless`: what she asks of the user, against the Seamless targets.

Each measure in docs/specs/2026-11-12-seamless.md ("Measures"), as recorded on this
computer, with its target beside it. It reads the database read-only and contacts
nothing, so it works offline, and with nothing recorded it says so. Numbers only:
nothing anyone said is part of a measure (ONB-6).
"""

from __future__ import annotations

from trillion.storage.sqlite.measures_store import Asks, FirstRun, Measures, ToolTurns

NOT_RECORDED = "not recorded"
TARGETS = {
    "questions": "0 with a model already available, 1 (pick a model) with none",
    "seconds": "under 30, with a model available",
    "asks": "down by half for someone who uses her daily",
    "quick": "reported, as the sign of asks that didn't need you",
    "tool_turns": "most",
}


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _share(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "-"


def _ready(f: FirstRun) -> str:
    return "a model was available" if f.model_ready else "no model was available"


def questions(f: FirstRun | None) -> str:
    if f is None:
        return f"{NOT_RECORDED} (this install began before it was counted)"
    if f.first_reply_at is None:
        return f"{f.questions} so far; she hasn't replied yet ({_ready(f)})"
    return f"{f.questions} ({_ready(f)})"


def seconds(f: FirstRun | None) -> str:
    if f is None:
        return f"{NOT_RECORDED} (this install began before it was counted)"
    s = f.seconds
    if s is None:
        return "not yet: she hasn't replied"
    return f"{s:.0f} ({_ready(f)})"


def asks(found: tuple[Asks, ...]) -> str:
    if not found:
        return NOT_RECORDED
    parts = []
    for a in found:
        weeks = a.days // 7
        rate = f", about {a.shown / weeks:.1f} a week" if weeks > 1 else ""
        parts.append(f"{_plural(a.shown, 'card')} shown in the last {a.days} days{rate}")
    return "; ".join(parts)


def quick(found: tuple[Asks, ...]) -> str:
    if not found:
        return NOT_RECORDED
    return "; ".join(
        f"{a.quick} of {a.shown} in the last {a.days} days ({_share(a.quick, a.shown)})"
        for a in found
    )


def tool_turns(t: ToolTurns | None) -> str:
    if t is None:
        return NOT_RECORDED
    if not t.turns:
        return f"none recorded in the last {t.days} days (only turns with her voice on count)"
    return (
        f"{t.in_time} of {t.turns} in the last {t.days} days ({_share(t.in_time, t.turns)}),"
        " her voice on"
    )


def report(m: Measures | None) -> str:
    """The measures in plain lines, each with its target. `None`: no database yet."""
    if m is None:
        m = Measures(first_run=None, asks=(), tool_turns=None)
    rows = (
        ("Questions before her first reply", questions(m.first_run), TARGETS["questions"]),
        (
            "Seconds from opening the app to her first reply",
            seconds(m.first_run),
            TARGETS["seconds"],
        ),
        ("Asks (cards shown)", asks(m.asks), TARGETS["asks"]),
        ("Asks approved within 30 seconds with nothing changed", quick(m.asks), TARGETS["quick"]),
        (
            "Tool turns over 2 seconds with words spoken before the 2-second mark",
            tool_turns(m.tool_turns),
            TARGETS["tool_turns"],
        ),
    )
    out = ["Seamless: what she asks of you, counted on this computer only. Nothing is sent.", ""]
    for name, value, target in rows:
        out += [f"{name}: {value}", f"  target: {target}", ""]
    return "\n".join(out)
