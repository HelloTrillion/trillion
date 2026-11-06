"""When she offers a helper (the your helpers spec, Suggestions, decision 4).

A need is an area of work, found from the tools a turn used by their names. An area
used on 3 different local days in the last 14, in your own chats with her (never a
helper's, never a run), is one she may offer a helper for: once a day at most, never
one you declined, and never one a helper's name or job already covers.

Pure: the session reads the audit, the conversations' kinds and the preferences, and
asks this.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

# Days back, days used, and how long after an offer the next may come.
WINDOW_DAYS = 14
DAYS_NEEDED = 3
ONCE_MS = 24 * 3600 * 1000
# Only your own chats with her count: a helper's chat and every run do not.
COUNTED_KINDS = frozenset({"chat", "day"})

# A built-in tool's area by the start of its name. An MCP server's tools are found
# from the server itself (`McpServers.server_of`), never from their names.
AREAS: tuple[tuple[str, str], ...] = (
    ("gmail_", "email"),
    ("calendar_", "calendar"),
    ("drive_", "files"),
    ("file_", "files"),
    ("stripe_", "payments"),
    ("slack_", "slack"),
    ("web_", "web"),
)
MCP = "mcp_"
# A connected MCP server's area: Stripe's from the catalogue is payments, any other
# server is its own, by its configured name.
MCP_AREA = "mcp:"
STRIPE_CATALOG = "stripe"

# The words a helper's name or job uses when it already looks after an area.
COVER_WORDS: dict[str, tuple[str, ...]] = {
    "email": ("email", "e-mail", "inbox", "mail", "gmail"),
    "calendar": ("calendar", "meeting", "schedule", "event", "appointment"),
    "files": ("file", "document", "doc", "drive", "folder"),
    "payments": ("payment", "invoice", "stripe", "revenue", "billing", "charge"),
    "slack": ("slack", "channel"),
    "web": ("web", "research", "news", "site", "browse"),
}

_LABELS = {"slack": "Slack"}

# What the rail's + box and the Helpers panel send ahead of the user's words.
NEW_HELPER = "new helper:"
# A plain ask for one: a verb of making or wanting, then within a few words a helper,
# an agent, an assistant or a bot. Loose on purpose: a turn wrongly taken for one only
# goes without an offer.
_ASKS_FOR = re.compile(
    r"\b(?:make|create|set\s*up|build|add|spin\s+up|start|want|need|get)\b"
    r"(?:\W+\w+){0,5}?\W+(?:helper|agent|assistant|bot)s?\b",
    re.IGNORECASE,
)


def asks_for_helper(text: str) -> bool:
    """Whether the user's words ask for a helper: then her answer is that helper, and no
    offer of another rides with it (your request wins over a suggestion)."""
    said = " ".join(text.split())
    return said.casefold().startswith(NEW_HELPER) or _ASKS_FOR.search(said) is not None


# (tool, conversation id, at) as the audit keeps them.
Call = tuple[str, str, int]
# An MCP tool's area, from its server: None for one no server is known for.
McpAreas = Callable[[str], str | None]


class Named(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def job(self) -> str: ...


def server_area(server: str, catalog: str) -> str:
    """The area an MCP server's tools are in."""
    return "payments" if catalog == STRIPE_CATALOG else f"{MCP_AREA}{server}"


def _no_servers(tool: str) -> str | None:
    return None


def area_of(tool: str, mcp: McpAreas = _no_servers) -> str | None:
    """The area a tool's work is in, or None for her own tools and for an MCP tool
    whose server is not known."""
    if tool.startswith(MCP):
        return mcp(tool)
    for prefix, area in AREAS:
        if tool.startswith(prefix):
            return area
    return None


def label(area: str) -> str:
    """An area as the user reads it: an MCP server by its name."""
    if area.startswith(MCP_AREA):
        return area[len(MCP_AREA) :]
    return _LABELS.get(area, area)


def since(now_ms: int) -> int:
    """How far back the audit is read: a day more than the window, so the first of its
    local days is whole in any zone."""
    return now_ms - (WINDOW_DAYS + 1) * 24 * 3600 * 1000


def _day(at_ms: int, tz: ZoneInfo | None) -> date:
    return datetime.fromtimestamp(at_ms / 1000, tz).date()


def repeated(
    calls: Iterable[Call],
    kinds: Mapping[str, str],
    zone: str,
    now_ms: int,
    mcp: McpAreas = _no_servers,
) -> list[str]:
    """The areas used on at least `DAYS_NEEDED` local days of the last `WINDOW_DAYS`,
    in conversations whose kind is counted. Most days first, then by name."""
    # No zone: this machine's local time, as `zones.month_start` reads it.
    tz = ZoneInfo(zone) if zone else None
    today = _day(now_ms, tz)
    first = today - timedelta(days=WINDOW_DAYS - 1)
    days: dict[str, set[date]] = {}
    for tool, session_id, at in calls:
        area = area_of(tool, mcp)
        if area is None or kinds.get(session_id) not in COUNTED_KINDS:
            continue
        d = _day(at, tz)
        if first <= d <= today:
            days.setdefault(area, set()).add(d)
    found = [(len(ds), area) for area, ds in days.items() if len(ds) >= DAYS_NEEDED]
    return [area for _, area in sorted(found, key=lambda x: (-x[0], x[1]))]


def _words(area: str) -> tuple[str, ...]:
    if area.startswith(MCP_AREA):
        name = label(area)
        # "google-drive" is covered by "google drive" too.
        return tuple(sorted({name, re.sub(r"[-_.]+", " ", name)}))
    return COVER_WORDS.get(area, (area,))


def covered(area: str, helpers: Sequence[Named]) -> bool:
    """A helper's name or job has one of the area's words, as a word of its own."""
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(w) for w in _words(area)) + r")", re.IGNORECASE
    )
    return any(pattern.search(f"{h.name} {h.job}") for h in helpers)


def to_offer(
    repeated: Sequence[str],
    helpers: Sequence[Named],
    declined: Sequence[str],
    last_offer_ms: int | None,
    now_ms: int,
) -> str | None:
    """The area to offer a helper for now, if any."""
    if last_offer_ms is not None and now_ms - last_offer_ms < ONCE_MS:
        return None
    for area in repeated:
        if area not in declined and not covered(area, helpers):
            return area
    return None
