"""Home (SUR-1a): the nine widgets a briefing can hold, and whether each is ready.

The core decides every tile's state from `runtime.day_setup()`, so Home only ever
promises what Your day (`persona/day.py`) will read; the page draws what it is given.
Pure, no I/O: nothing from a connector's data is read here, only what is set up.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from trillion.connectors.mcp.catalog import BY_ID, Entry
from trillion.persona.day import SetUp
from trillion.persona.prompt import DEFAULT_USER_NAME, Identity

State = Literal["ready", "asks_first", "connect", "built_in"]
# The service marks a tile shows before "+N more".
SHOWN = 3
# Best first: a tile is as good as its best service.
_RANK: dict[State, int] = {"ready": 0, "asks_first": 1, "connect": 2}


@dataclass(frozen=True, kw_only=True)
class Tile:
    # The sample widget it previews is the type of the same name.
    id: str
    title: str
    what: str
    # Catalogue ids that can feed it, in the order Home offers them. Empty
    # means built in.
    services: tuple[str, ...] = ()


# One per row of Your day, in its order (the spec's D3).
TILES: tuple[Tile, ...] = (
    Tile(
        id="next_up",
        title="Next up",
        what="Your next meeting, and when to leave for it",
        services=("google-calendar",),
    ),
    Tile(
        id="free_time",
        title="Free time",
        what="Open time left in your day",
        services=("google-calendar",),
    ),
    Tile(id="inbox", title="Inbox", what="Mail that's waiting on your reply", services=("gmail",)),
    Tile(id="tasks", title="Reminders", what="Your reminders and timed asks for today"),
    Tile(
        id="mentions", title="Mentions", what="Where people need you in Slack", services=("slack",)
    ),
    Tile(
        id="due_soon",
        title="Due soon",
        what="What's due today and tomorrow",
        services=("linear", "todoist", "notion", "atlassian", "clickup", "monday", "airtable"),
    ),
    Tile(
        id="kpis",
        title="Revenue",
        what="Yesterday's sales against the day before",
        services=("stripe", "paypal", "square"),
    ),
    Tile(
        id="files",
        title="Recent files",
        what="Docs that changed since yesterday",
        services=("google-drive",),
    ),
    Tile(
        id="checks",
        title="What's broken",
        what="Failing checks and new errors, only when there are any",
        services=("github", "sentry", "vercel", "supabase"),
    ),
)


def _readable(service: str, setup: SetUp) -> bool:
    """Read by Your day without asking: the built-in sources have their own flags."""
    built_in = {
        "google-calendar": setup.calendar,
        "gmail": setup.gmail,
        "slack": setup.slack,
        "google-drive": setup.drive,
    }
    return built_in[service] if service in built_in else service in setup.catalog


def _state(service: str, setup: SetUp) -> State:
    if _readable(service, setup):
        return "ready"
    # Set up but not readable yet (a server still starting) is still a connect: the
    # page's Connect then reveals its card, which says what is wrong.
    return "asks_first" if service in setup.asks_first else "connect"


def _one_click(entry: Entry, google_ready: bool) -> bool:
    """Signs in with one click: Google once this copy has its app, Slack, or a sign-in."""
    return entry.how in ("sign_in", "slack") or (entry.how == "google" and google_ready)


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _next(tiles: list[dict[str, Any]], google_ready: bool) -> dict[str, str] | None:
    """D8: the first tile to connect whose service signs in with one click, else the
    first tile to connect. Asks-first tiles are set up already, so never suggested, and
    nor is a service already set up (a server still starting) that needs no new sign-in."""
    locked = [t for t in tiles if t["state"] == "connect"]
    open_ = [(t, s) for t in locked for s in t["services"] if not s["settings_key"]]
    pick = next(
        ((t, s["id"]) for t, s in open_ if _one_click(BY_ID[s["id"]], google_ready)),
        (open_[0][0], open_[0][1]["id"]) if open_ else None,
    )
    if pick is None:
        return None
    tile, service = pick
    one_click = _one_click(BY_ID[service], google_ready)
    adds = _join([t["title"] for t in locked if any(s["id"] == service for s in t["services"])])
    title = BY_ID[service].title
    text = (
        f"Connect {title} next: one sign-in adds {adds}"
        if one_click
        else f"Connect {title} next to add {adds}"
    )
    return {"tile": tile["id"], "service": service, "text": text}


def home(
    setup: SetUp,
    configured: Mapping[str, str],
    *,
    google_ready: bool,
    briefing_built: bool,
    identity: Identity,
) -> dict[str, Any]:
    """What `GET /api/workspace/home` answers. `configured` maps each catalogue id set
    up to the Connected panel's key for its card (`google:<service>`, `chat:slack`, or
    the MCP server's name)."""
    tiles: list[dict[str, Any]] = []
    for t in TILES:
        states = [_state(s, setup) for s in t.services]
        state: State = min(states, key=lambda x: _RANK[x]) if states else "built_in"
        services = [
            {
                "id": s,
                "title": BY_ID[s].title,
                "color": BY_ID[s].color,
                "glyph": BY_ID[s].glyph,
                "how": BY_ID[s].how,
                "category": BY_ID[s].category,
                "state": st,
                "settings_key": configured.get(s, ""),
            }
            for s, st in zip(t.services, states, strict=True)
        ]
        tiles.append(
            {
                "id": t.id,
                "title": t.title,
                "type": t.id,
                "what": t.what,
                "state": state,
                "services": services,
                "more": max(0, len(services) - SHOWN),
            }
        )
    return {
        # No name yet: the greeting goes without one rather than say "the user".
        "user_name": "" if identity.user_name == DEFAULT_USER_NAME else identity.user_name,
        "agent_name": identity.agent_name,
        "briefing": "open" if briefing_built else "build",
        "ready": sum(t["state"] in ("ready", "built_in") for t in tiles),
        "total": len(tiles),
        "next": _next(tiles, google_ready),
        "tiles": tiles,
    }
