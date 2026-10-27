"""What a live widget remembers (the live widgets spec): the reads that made it.

During a turn the session records each eligible read that succeeded, with the
connector it belongs to. When she saves a widget with a source, the reads of that
turn from the same connector become the widget's `live`, the server's own field: she
cannot write it, and the page never sees the reads. A later task replays them.

Kept in memory per (session, turn) and dropped when the turn ends: a read from an
earlier turn is not what this widget was built from.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from trillion.connectors.mcp.tool import McpTool
from trillion.spine.contract import JSON, Risk, Tool, ToolDefinition
from trillion.spine.registry import runs_unattended
from trillion.tools.describe import NAME as DESCRIBE_SELF
from trillion.tools.web import FETCH
from trillion.workspace.packs import PACKS
from trillion.workspace.recipe_kit import OFF

MAX_READS = 5
# A read's arguments, as JSON, past which it is not remembered: a refresh replays a
# lookup, not a payload, and the board keeps them.
MAX_ARGS = 2000
# Kept per turn, the oldest dropped first: a widget takes at most the last five.
KEEP = 20

# Minutes between refreshes when she says nothing: what changes by the minute, what
# changes through the day, and the rest.
_FAST = ("next_up", "inbox", "mentions", "checks", "deploys", "services", "support_queue")
_DAILY = (*PACKS["money"], *PACKS["customers"], "kanban", "sprint", "tasks", "due_soon", "kpis")
# Fast wins: the support queue is a customers widget that changes by the minute.
DEFAULT_EVERY = {**{t: 15 for t in _DAILY}, **{t: 2 for t in _FAST}}
OTHER_EVERY = 60
# Reads that cost too much to replay often, by the fewest minutes between them, by the
# tool's name: stripe_revenue pages up to a minute of charges each time.
SLOW_READS = {"stripe_revenue": 60}

# Never replayed: a page is chosen by what she read (SPN-5), and her own tools say
# nothing about a connector.
_NEVER = frozenset({FETCH, DESCRIBE_SELF})
_NEVER_CATEGORIES = frozenset({"memory", "workspace"})
# The built-in connectors, by their tools' prefixes, named as their catalogue tiles are.
_PREFIXES = (
    ("gmail_", "Gmail"),
    ("calendar_", "Google Calendar"),
    ("drive_", "Google Drive"),
    ("slack_", "Slack"),
)


@dataclass(frozen=True, kw_only=True)
class Read:
    tool: str
    args: JSON
    connector: str


def eligible(d: ToolDefinition | None) -> bool:
    """Whether a call to this tool may be replayed with nobody deciding (D4)."""
    return (
        d is not None
        and d.risk is Risk.READ
        and runs_unattended(d)
        and d.name not in _NEVER
        and d.category not in _NEVER_CATEGORIES
    )


def connector(name: str, tool: Tool | None) -> str | None:
    """The connector a tool belongs to, by the name its tile has: an MCP server's
    title, or a built-in connector's. None for anything else (files, the web)."""
    if isinstance(tool, McpTool):
        return tool.title
    # A built-in tool that reads through a connector says which (stripe_revenue).
    title = getattr(tool, "connector_title", None)
    if isinstance(title, str) and title:
        return title
    return next((title for prefix, title in _PREFIXES if name.startswith(prefix)), None)


def tile_names(title: str) -> list[str]:
    """The names a source may use for a connector: its title, and the short name people
    say ("Calendar" for "Google Calendar", "Jira" for "Jira and Confluence", "monday"
    for "monday.com"). The page's `tileNames` (apps/web/src/board.ts), in Python."""
    full = title.strip().lower()
    short = full.removeprefix("google ").split(" and ")[0].split(".")[0].strip()
    return [full, short] if short and short != full else [full]


def _spaced(name: str) -> str:
    return " ".join(name.replace("-", " ").replace("_", " ").split())


def matches(source: str, title: str) -> bool:
    """Whether a widget's source names this connector, ignoring case, and reading `-`
    and `_` as spaces: a server named "my-crm" is the "My CRM" she writes."""
    name = _spaced(source.strip().lower())
    return bool(name) and name in {_spaced(t) for t in tile_names(title)}


class Reads:
    """This turn's eligible reads, per (session, turn). In memory: a restart ends every
    turn anyway."""

    def __init__(self) -> None:
        self._by_turn: dict[tuple[str, int], list[Read]] = {}

    def record(self, session_id: str, turn: int, tool: str, args: JSON, title: str) -> None:
        try:
            size = len(json.dumps(dict(args), ensure_ascii=False))
        except (TypeError, ValueError, RecursionError):
            return
        if size > MAX_ARGS:
            return
        # A copy: the arguments as called, whatever happens to the caller's dict later.
        read = Read(tool=tool, args=copy.deepcopy(dict(args)), connector=title)
        kept = self._by_turn.setdefault((session_id, turn), [])
        kept.append(read)
        del kept[:-KEEP]

    def of(self, session_id: str, turn: int) -> list[Read]:
        return list(self._by_turn.get((session_id, turn), ()))

    def end(self, session_id: str, turn: int) -> None:
        self._by_turn.pop((session_id, turn), None)


def every(widget: Mapping[str, Any], reads: Iterable[str] | None = None) -> int:
    """Minutes between refreshes: hers, or her type's default (also when she turned it
    off, so turning it back on needs nothing else), never sooner than its slowest read
    allows. `reads` names the tools it replays; by default, those in its `live`."""
    given = widget.get("refresh")
    if isinstance(given, int):
        return given
    if reads is None:
        kept = widget.get("live")
        listed = kept.get("reads") if isinstance(kept, Mapping) else None
        reads = [str(r.get("tool")) for r in listed or () if isinstance(r, Mapping)]
    base = DEFAULT_EVERY.get(str(widget.get("type")), OTHER_EVERY)
    return max([base, *(SLOW_READS.get(t, 0) for t in reads)])


def _source_name(widget: Mapping[str, Any]) -> str | None:
    source = widget.get("source")
    name = source.get("name") if isinstance(source, Mapping) else None
    return name if isinstance(name, str) else None


def live_for(widget: Mapping[str, Any], reads: list[Read]) -> dict[str, Any] | None:
    """The widget's `live`, from the reads of its source's connector (the last five),
    or None when it has no source or none of the reads match: a snapshot."""
    source = _source_name(widget)
    if source is None:
        return None
    mine = [r for r in reads if matches(source, r.connector)][-MAX_READS:]
    if not mine:
        return None
    return {
        "reads": [{"tool": r.tool, "args": copy.deepcopy(dict(r.args))} for r in mine],
        "every": every(widget, [r.tool for r in mine]),
    }


def attach(widget: dict[str, Any], reads: list[Read], old: Mapping[str, Any] | None) -> None:
    """Give a widget she just saved its `live`, in place. A widget she changed by hand
    (`old`, the one it replaces) keeps the old reads when this turn read nothing from
    the same source, as long as it still names that source."""
    widget.pop("live", None)
    live = live_for(widget, reads)
    if live is None and old is not None:
        kept = old.get("live")
        name, was = _source_name(widget), _source_name(old)
        same = bool(name and was and _spaced(name.lower()) == _spaced(was.lower()))
        if isinstance(kept, Mapping) and same:
            reads = copy.deepcopy(kept.get("reads", []))
            live = {"reads": reads, "every": every({**widget, "live": {"reads": reads}})}
    if live is not None:
        widget["live"] = live


def for_page(widget: Mapping[str, Any], refreshing: bool = False) -> dict[str, Any] | None:
    """What the page is told of a widget's `live`: how often, and how it is going. Never
    the reads. None for a snapshot, or one she turned off. `refreshing`: a refresh of it
    is in flight, so a board sent meanwhile says so."""
    live = widget.get("live")
    if not isinstance(live, Mapping) or widget.get("refresh") == OFF:
        return None
    out: dict[str, Any] = {"every": live.get("every", every(widget)), "state": "idle"}
    # A refresh that failed says so, and when (the refresher records it).
    failed = live.get("failed")
    if isinstance(failed, Mapping) and failed.get("at"):
        out["state"], out["failed_at"] = "failed", failed["at"]
    if refreshing:
        out["state"] = "refreshing"
    return out
