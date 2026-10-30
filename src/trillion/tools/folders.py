"""Her folder tools (the shared folders spec): she browses, proposes a share on a card,
and narrows a share at once.

`folders_browse` moves the folder browser the page draws beside the board, and tells
her the folder's path and its folder names, nothing else (D5). It runs only on a turn
the user started by speaking or typing, and never once the turn has read something
from outside (D7): an email that says "list their folders" finds nothing to call.

`folder_share` raises a card the user must tap (D2): a spoken yes never approves it,
because the folder may have been named by something she read. The card is composed
here from the frozen arguments, naming the folder as it really is (links followed),
and the folder is checked against D4 when the card is raised and again when it is
approved, so a folder swapped for a link in between is refused.

`folder_unshare` removes a share, lowers it to read only or narrows it to one routine
at once, even after an outside read (D3); anything that would widen it is refused and
sent to `folder_share`.

The shares store and the browser touch the disk and wait on slow drives, so all of
it runs in a thread.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from trillion.files import browse, rules
from trillion.files.shares import (
    EVERYONE,
    Access,
    Share,
    ShareError,
    Shares,
    routine_scope,
    widens,
)
from trillion.memory.present import inert
from trillion.spine.contract import (
    ASKS_BY_CARD,
    JSON,
    ActionSummary,
    BaseTool,
    Precondition,
    Risk,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from trillion.spine.deny_floor import CASE_BLIND, fold
from trillion.spine.neutralize import neutralize
from trillion.spine.router import SUMMARY_LIMIT

CATEGORY = "files"
BROWSE, SHARE, UNSHARE = "folders_browse", "folder_share", "folder_unshare"
THIS = "this"
TIMEOUT_S = 5.0
# A turn the user started themselves. Never a run with nobody there, a tapped ask
# (its words may come from a page), a refresh or a channel.
_SAID = frozenset({"voice", "text"})
_UNSAID = "the folder browser opens only when the user asks, by speaking or typing"
_FROM_READING = (
    "the folder browser doesn't open after something someone else wrote was read in "
    "this turn; the user can open it themselves"
)
_NO_PAGE = "the folder browser needs the page open"
_UNAVAILABLE = "shared folders are not available"
_NOT_SHOWING = (
    "the folder browser isn't showing a folder: open one with folders_browse, or give "
    "the folder's full path"
)
_WIDENS = (
    "that would let Trillion reach more, so it goes on a card: call folder_share with "
    "the folder's path, and the user taps the card"
)
_TOO_LONG = (
    "that folder's path is too long to show whole on a card: the user can share it in "
    "Settings, Shared folders"
)
_ACCESS = {"read": "read only", "change": "read and change"}

Routines = Callable[[], Awaitable[Sequence[tuple[str, str]]]]


@dataclass(frozen=True)
class Look:
    """How the browser looks at this computer; None is this computer's own. Tests give
    a fence, a home and a places finder over a temporary folder."""

    fence: Callable[[Sequence[Path]], browse.Fence] | None = None
    home: Path | None = None
    finder: Callable[[Path], browse.Found] | None = None


@dataclass(frozen=True)
class View:
    """Where the browser is after a move, and the panel's frame; with `problem` set it
    did not move and there is no frame."""

    place: Path | None
    frame: dict[str, Any] = field(default_factory=dict)
    problem: str | None = None
    # What she is told: the path spelled from home, and the names.
    told: dict[str, Any] = field(default_factory=dict)


class Panel(Protocol):
    """The page's folder browser, kept by its session."""

    folder_place: Path | None

    async def show_folders(self, view: View) -> None: ...


def near_home(p: Path) -> str:
    """A folder under the home folder spelled from `~`, as the user would say it."""
    home = Path.home()
    if p == home or not p.is_relative_to(home):
        return str(p)
    return f"~{os.sep}{p.relative_to(home)}"


def _same(a: Path, b: Path) -> bool:
    return fold(str(a)) == fold(str(b)) if CASE_BLIND else a == b


def _real(raw: str) -> Path:
    """The folder a path names, links followed. Touches the disk only to follow links."""
    try:
        return rules.resolved(Path(raw).expanduser())
    except (OSError, RuntimeError, ValueError):
        return Path(raw).expanduser()


def _places_view(found: Sequence[browse.Place]) -> View:
    rows = [p.row() for p in found]
    return View(
        place=None,
        frame={
            "path": None,
            "crumbs": [],
            "folders": [],
            "more": 0,
            "shared": None,
            "places": rows,
        },
        told={
            "shows": "the places list",
            "places": [{"label": p.label, "path": near_home(p.path)} for p in found],
        },
    )


def _folder_view(listing: browse.Listing, everyone: Sequence[Share]) -> View:
    shared = next((s for s in everyone if _same(s.path, listing.path)), None)
    told: dict[str, Any] = {"shows": near_home(listing.path), "folders": list(listing.folders)}
    if listing.more:
        told["and_more"] = listing.more
    if shared is not None:
        told["shared_as"] = shared.name
    return View(
        place=listing.path,
        frame={
            "path": str(listing.path),
            "crumbs": [{"label": label, "path": str(p)} for label, p in listing.crumbs],
            "folders": list(listing.folders),
            "more": listing.more,
            "shared": shared.id if shared is not None else None,
            "places": None,
        },
        told=told,
    )


async def open_place(current: Path | None, target: str, shares: Shares, look: Look) -> View:
    """Where `target` takes the browser from `current` (None: the places list), within
    the fence. The same for her tool and the user's click. Never raises."""
    await asyncio.to_thread(shares.refresh)
    reached = shares.known_reached(EVERYONE)
    everyone = shares.known_all()
    paths = [s.path for s in reached]

    def work() -> View:
        fence = look.fence(paths) if look.fence is not None else None
        found: list[browse.Place] | None = None

        def places_now() -> list[browse.Place]:
            nonlocal found
            if found is None:
                found = browse.places(
                    [(s.name, s.path) for s in reached],
                    home=look.home,
                    finder=look.finder,
                    fence=fence,
                )
            return found

        moved = browse.step(
            current,
            target,
            shared=paths,
            fence=fence,
            # Only the places list's own labels need it: a name said there.
            places_now=places_now() if current is None else (),
        )
        if moved.problem is not None:
            return View(place=current, problem=moved.problem)
        if moved.to is None:
            return _places_view(places_now())
        listing = browse.listing(moved.to, shared=paths, fence=fence)
        if listing.problem is not None:
            return View(place=current, problem=listing.problem)
        return _folder_view(listing, everyone)

    return await asyncio.to_thread(work)


def host_note(view: View) -> str:
    """What she is told on her next turn after the user moved the browser themselves,
    so "share this one" means what the user sees. The path's names are the disk's, so
    they are neutralized as any tool's words are (SPN-6), and kept to one line."""
    shows = neutralize(str(view.told.get("shows", "the places list")))[0]
    return f"[host] The folder browser shows {inert(shows)}"


def _everyone(said: str) -> bool:
    return not said or said.lower() == EVERYONE


def _matching(said: str, known: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    """The routines `said` names, by id or by title in any case."""
    want = said.removeprefix("routine:")
    by_id = [r for r in known if r[0] == want]
    return by_id or [r for r in known if fold(r[1].strip()) == fold(said)]


async def _scope(raw: object, routines: Routines) -> tuple[str | None, str | None]:
    """A scope as said (`everyone`, or a routine by its title or id) as stored, or why
    not: (scope, None) or (None, reason)."""
    said = str(raw).strip() if raw is not None else ""
    if _everyone(said):
        return EVERYONE, None
    found = [rid for rid, _title in _matching(said, list(await routines()))]
    if len(found) == 1:
        return routine_scope(found[0]), None
    if found:
        return None, f"more than one routine is called {said}: say which by its id"
    return None, f"there is no routine called {said}"


class _FolderTool(BaseTool):
    def __init__(self) -> None:
        self._shares: Shares | None = None
        self._panel: Callable[[str], Panel | None] = lambda _sid: None
        self._look: Callable[[], Look] = Look
        self._routines: Routines = _none
        # The routines as last read, only to name one on a card by its own title.
        self._titles: Sequence[tuple[str, str]] = ()

    def bind(
        self,
        shares: Shares,
        *,
        panel: Callable[[str], Panel | None],
        look: Callable[[], Look],
        routines: Routines,
    ) -> None:
        """Called once at startup with the process's shares, a conversation's panel,
        how the browser looks and the routines by (id, title)."""
        self._shares, self._panel, self._look = shares, panel, look

        async def read() -> Sequence[tuple[str, str]]:
            self._titles = list(await routines())
            return self._titles

        self._routines = read


async def _none() -> Sequence[tuple[str, str]]:
    return ()


def _refused(text: str, kind: str = "refused") -> ToolResult:
    return ToolResult(ok=False, content=text, effect="none", error_kind=kind)


class FoldersBrowse(_FolderTool):
    definition = ToolDefinition(
        name=BROWSE,
        description=(
            "Open or move the folder browser beside the board, to help the user find a "
            "folder to share. `place` is 'places' (Home, Documents, drives, shared "
            "folders), a full path, the name of a folder the browser shows now "
            "('Clients'), or 'up'. Only when the user asked to look at their folders. It "
            "shows folder names only: no files, nothing inside them."
        ),
        input_schema={
            "type": "object",
            "properties": {"place": {"type": "string", "minLength": 1, "maxLength": 4096}},
            "required": ["place"],
            "additionalProperties": False,
        },
        risk=Risk.READ,
        category=CATEGORY,
        timeout_s=TIMEOUT_S,
        # A path she names is still refused if it touches anything protected (SEC-10).
        outside_path_args=("place",),
    )

    def _why_not(self, ctx: ToolContext) -> str | None:
        if self._shares is None:
            return _UNAVAILABLE
        if ctx.via not in _SAID:
            return _UNSAID
        if ctx.read_outside:
            return _FROM_READING
        if self._panel(ctx.session_id) is None:
            return _NO_PAGE
        return None

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        why = self._why_not(ctx)
        return Precondition.failed(why) if why is not None else Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        # Asked again here: nothing between the check and the listing may let it run.
        why = self._why_not(ctx)
        panel = self._panel(ctx.session_id)
        if why is not None or panel is None or self._shares is None:
            return _refused(why or _NO_PAGE)
        view = await open_place(panel.folder_place, str(args["place"]), self._shares, self._look())
        if view.problem is not None:
            return _refused(view.problem)
        await panel.show_folders(view)
        return ToolResult(ok=True, content=json.dumps(view.told, ensure_ascii=False), effect="none")


class FolderShare(_FolderTool):
    definition = ToolDefinition(
        name=SHARE,
        description=(
            "Share a folder with yourself, so your file tools can reach it. `path` is the "
            "folder's full path, or 'this' for the folder the browser shows. `access` is "
            "read (the default) or change; `scope` is everyone (the default) or one "
            "routine by its title. Some folders can never be shared: a home folder, a "
            "whole drive, system and hidden folders. The share is made only when the user "
            "taps the card. " + ASKS_BY_CARD
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "minLength": 1, "maxLength": 4096},
                "name": {"type": "string", "minLength": 1, "maxLength": 60},
                "access": {"type": "string", "enum": ["read", "change"]},
                "scope": {"type": "string", "minLength": 1, "maxLength": 200},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        # Widens what every later file tool reaches, and the folder may have been named
        # by something she read: only the user's tap makes it (SPN-14).
        risk=Risk.IRREVERSIBLE,
        recoverable=False,
        category=CATEGORY,
        timeout_s=TIMEOUT_S,
        # Never a granted folder (it is not shared yet), but never a protected one.
        outside_path_args=("path",),
    )

    def _existing(self, folder: Path) -> Share | None:
        shares = self._shares.known_all() if self._shares is not None else []
        return next((s for s in shares if not s.run_only and _same(s.path, folder)), None)

    def _name(self, args: JSON, folder: Path) -> str | None:
        """The name the share gets: as asked, else the one it already has (None keeps
        it), else the folder's own. Given to `put` whole, so it is never numbered
        behind the card's back."""
        if "name" in args:
            return " ".join(str(args["name"]).split())
        return None if self._existing(folder) is not None else folder.name

    def describe(self, args: JSON) -> ActionSummary:
        folder = _real(str(args["path"]))
        existing = self._existing(folder)
        name = self._name(args, folder) or (existing.name if existing else folder.name)
        access = _ACCESS.get(str(args.get("access", "read")), "read only")
        said = str(args.get("scope", "")).strip()
        named = _matching(said, self._titles)
        title = named[0][1] if len(named) == 1 else said
        who = "everyone" if _everyone(said) else f"the routine {title}"
        return ActionSummary(
            text=f"Share {inert(near_home(folder))} with Trillion as {inert(name)}, "
            f"{access}, for {inert(who)}"
        )

    def target(self, args: JSON) -> str:
        return f"folder:{_real(str(args['path']))}"

    def fingerprint(self, args: JSON) -> str | None:
        # The folder as it really is: one swapped for a link since the card is refused.
        return str(_real(str(args["path"])))

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        shares = self._shares
        if shares is None:
            return Precondition.failed(_UNAVAILABLE)
        raw = str(args["path"]).strip()
        if raw.lower() == THIS:
            panel = self._panel(ctx.session_id)
            place = panel.folder_place if panel is not None else None
            if place is None:
                return Precondition.failed(_NOT_SHOWING)
            # The card must name the folder itself, frozen as the user will see it.
            return Precondition.failed(
                f"the folder browser shows {inert(near_home(place))}: call folder_share "
                "again with that path"
            )
        _, why = await _scope(args.get("scope"), self._routines)
        if why is not None:
            return Precondition.failed(why)
        try:
            # D4, now and again when the card is approved (SPN-2c).
            await asyncio.to_thread(shares.folder, Path(raw))
        except ShareError as e:
            return Precondition.failed(str(e))
        if len(self.describe(args).text) > SUMMARY_LIMIT:
            # A card cut short would hide part of the path the user is approving.
            return Precondition.failed(_TOO_LONG)
        return Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        shares = self._shares
        if shares is None:
            return _refused(_UNAVAILABLE)
        scope, why = await _scope(args.get("scope"), self._routines)
        if scope is None:
            return _refused(why or _UNAVAILABLE)
        folder = _real(str(args["path"]))
        access: Access = "change" if args.get("access") == "change" else "read"
        try:
            share = await asyncio.to_thread(
                shares.put, folder, name=self._name(args, folder), access=access, scope=scope
            )
        except ShareError as e:
            return _refused(str(e), "failed")
        return ToolResult(
            ok=True,
            content=(
                f"shared {near_home(share.path)} as {share.name}, {_ACCESS[share.access]}: "
                "your file tools reach it from the next call"
            ),
            effect="confirmed",
        )


class FolderUnshare(_FolderTool):
    definition = ToolDefinition(
        name=UNSHARE,
        description=(
            "Make a shared folder reach less, at once, without asking: with only `name`, "
            "stop sharing it; with `access: read`, make it read only; with `scope` set to "
            "a routine's title, share it with that routine only. Anything that would "
            "reach more is folder_share's, on a card."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 60},
                "access": {"type": "string", "enum": ["read", "change"]},
                "scope": {"type": "string", "minLength": 1, "maxLength": 200},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        # Only ever narrows what she reaches (D3), so it never asks.
        risk=Risk.WRITE,
        category=CATEGORY,
        timeout_s=TIMEOUT_S,
    )

    def target(self, args: JSON) -> str:
        return f"folder:{args.get('name', '')}"

    async def _plan(self, args: JSON) -> tuple[Share | None, str | None, str | None, str | None]:
        """(the share, its new access, its new scope, why not). With neither access nor
        scope it is removed."""
        shares = self._shares
        if shares is None:
            return None, None, None, _UNAVAILABLE
        name = str(args["name"])
        old = await asyncio.to_thread(shares.get, name)
        if old is None:
            return None, None, None, f"no shared folder is called {inert(name)}"
        if "access" not in args and "scope" not in args:
            return old, None, None, None
        scope = old.scope
        if "scope" in args:
            found, why = await _scope(args["scope"], self._routines)
            if found is None:
                return None, None, None, why
            scope = found
        access = str(args.get("access", old.access))
        # A `--grant` share stays this run's when changed here: only a save widens it.
        if widens(replace(old, run_only=False), access=access, scope=scope):
            return None, None, None, _WIDENS
        return old, access, scope, None

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        why = (await self._plan(args))[3]
        return Precondition.failed(why) if why is not None else Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        # Planned again here, so a change made since the check can't be widened.
        old, access, scope, why = await self._plan(args)
        shares = self._shares
        if old is None or shares is None:
            return _refused(why or _UNAVAILABLE)
        try:
            if access is None or scope is None:
                await asyncio.to_thread(shares.remove, old.id)
                return ToolResult(
                    ok=True, content=f"stopped sharing {old.name}", effect="confirmed"
                )
            new = await asyncio.to_thread(
                shares.change,
                old.id,
                access="read" if access == "read" else "change",
                scope=scope,
            )
        except ShareError as e:
            return _refused(str(e), "failed")
        who = "everyone" if new.scope == EVERYONE else "one routine"
        return ToolResult(
            ok=True,
            content=f"{new.name} is now {_ACCESS[new.access]}, for {who}",
            effect="confirmed",
        )
