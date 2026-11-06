"""A helper's picture (the your helpers spec, Its picture): a colour, drawn as an orb,
and an icon kept with it, never blank. One she leaves out or names wrong is picked from
the job's words, else a spark, and the colour from the name, so it stays the same each
time."""

from __future__ import annotations

import hashlib
import re

__all__ = ["COLORS", "ICONS", "pick"]

ICONS = (
    "receipt",
    "envelope",
    "calendar",
    "chart",
    "cart",
    "folder",
    "people",
    "chat",
    "star",
    "heart",
    "bell",
    "globe",
    "book",
    "briefcase",
    "wrench",
    "leaf",
    "home",
    "plane",
    "music",
    "camera",
    "code",
    "shield",
    "lightbulb",
    "spark",
)
# The web draws each as an orb in that colour; the icon is kept but no longer drawn.
COLORS = (
    "blue",
    "green",
    "amber",
    "rose",
    "violet",
    "teal",
    "orange",
    "slate",
    "red",
    "fuchsia",
    "lime",
)
FALLBACK_ICON = "spark"

# A word in the job, as it starts, and the icon it suggests. The first that matches wins.
_BY_WORD: tuple[tuple[str, str], ...] = (
    ("invoice", "receipt"),
    ("payment", "receipt"),
    ("bill", "receipt"),
    ("revenue", "receipt"),
    ("expense", "receipt"),
    ("receipt", "receipt"),
    ("email", "envelope"),
    ("inbox", "envelope"),
    ("mail", "envelope"),
    ("calendar", "calendar"),
    ("meeting", "calendar"),
    ("schedule", "calendar"),
    ("appointment", "calendar"),
    ("report", "chart"),
    ("number", "chart"),
    ("metric", "chart"),
    ("sales", "chart"),
    ("shop", "cart"),
    ("grocer", "cart"),
    ("order", "cart"),
    ("file", "folder"),
    ("document", "folder"),
    ("folder", "folder"),
    ("contact", "people"),
    ("customer", "people"),
    ("client", "people"),
    ("team", "people"),
    ("message", "chat"),
    ("chat", "chat"),
    ("slack", "chat"),
    ("health", "heart"),
    ("fitness", "heart"),
    ("remind", "bell"),
    ("alert", "bell"),
    ("news", "globe"),
    ("web", "globe"),
    ("read", "book"),
    ("book", "book"),
    ("learn", "book"),
    ("research", "book"),
    ("work", "briefcase"),
    ("job", "briefcase"),
    ("project", "briefcase"),
    ("fix", "wrench"),
    ("repair", "wrench"),
    ("maintenance", "wrench"),
    ("garden", "leaf"),
    ("plant", "leaf"),
    ("home", "home"),
    ("house", "home"),
    ("travel", "plane"),
    ("trip", "plane"),
    ("flight", "plane"),
    ("music", "music"),
    ("song", "music"),
    ("photo", "camera"),
    ("picture", "camera"),
    ("code", "code"),
    ("deploy", "code"),
    ("bug", "code"),
    ("security", "shield"),
    ("password", "shield"),
    ("idea", "lightbulb"),
)
_WORDS = re.compile(r"\w+")


def _icon_for(job: str) -> str:
    words = _WORDS.findall(job.casefold())
    for start, icon in _BY_WORD:
        if any(w.startswith(start) for w in words):
            return icon
    return FALLBACK_ICON


def _color_for(name: str) -> str:
    # Not Python's own hash, which changes from one run to the next.
    digest = hashlib.sha256(name.casefold().encode("utf-8")).digest()
    return COLORS[int.from_bytes(digest[:4], "big") % len(COLORS)]


def pick(name: str, job: str, icon: object = None, color: object = None) -> tuple[str, str]:
    """(icon, colour) for a helper: the ones given when they are on the lists, else
    picked from the job and the name."""
    chosen = icon if isinstance(icon, str) and icon in ICONS else _icon_for(job)
    hue = color if isinstance(color, str) and color in COLORS else _color_for(name)
    return chosen, hue
