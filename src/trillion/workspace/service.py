"""Her board's service (SUR-1): apply a change, save it, tell the pages.

Every change loads the conversation's board, applies one pure edit from `board.py`,
saves the result and sends it to every page open on that conversation. A refused
edit (`BoardError`) changes nothing, saved or shown.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from trillion.workspace import board as edits
from trillion.workspace import live
from trillion.workspace.blocks import day_in, on_day
from trillion.workspace.board import Board, BoardError, Mode
from trillion.workspace.kit import Widget
from trillion.workspace.recipes import expand_board

logger = logging.getLogger(__name__)

# How long the pages may take to be sent a board; it is saved either way.
SEND_S = 2.0
# Sends a board (as the page draws it, or None) to every page open on a conversation.
SendTo = Callable[[str, Board | None], Awaitable[None]]


class BoardStore(Protocol):
    async def get(self, conversation_id: str, owner_id: str) -> Board | None: ...

    async def put(self, conversation_id: str, owner_id: str, board: Board, now_ms: int) -> None: ...


class Conversations(Protocol):
    async def exists(self, conversation_id: str, owner_id: str) -> bool: ...


class BoardSaveError(BoardError):
    """The edit was fine; the database refused it."""


def for_page(
    board: Board | None, zone: str | None = None, refreshing: Collection[str] = ()
) -> Board | None:
    """The board as the page draws it: every widget expanded into blocks, and without
    the id counter, which is the server's own. Expanded on today in `zone` (the
    user's), so "Today" in a due-soon card is their day, not the server's. The saved
    board is left as it was. `refreshing`: the ids of widgets being refreshed now."""
    if board is None:
        return None
    with on_day(day_in(zone)):
        page = expand_board({k: v for k, v in board.items() if k != "next_id"})
    # Expanded in order, one for one. A live widget says how it is kept fresh, never
    # the reads that do it.
    for saved, shown in zip(board["widgets"], page["widgets"], strict=True):
        put = live.for_page(saved, saved.get("id") in refreshing)
        if put is not None:
            shown["live"] = put
    return page


def _iso(now_ms: int) -> str:
    return datetime.fromtimestamp(now_ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp(board: Board, now_ms: int) -> None:
    """When a widget's facts were put up is the server's to say: `validate_widget`
    drops any `at` she sends, so a widget with a source and no `at` is new or changed
    by this edit. A widget the edit left alone keeps its time."""
    at = _iso(now_ms)
    for widget in board["widgets"]:
        source = widget.get("source")
        if isinstance(source, dict) and "at" not in source:
            source["at"] = at


class Boards:
    def __init__(
        self,
        store: BoardStore,
        conversations: Conversations,
        send_to: SendTo,
        *,
        clock: Callable[[], int],
        owner: str,
        zone: Callable[[], str | None] = lambda: None,
        reads: live.Reads | None = None,
        refreshing: Callable[[str], Collection[str]] = lambda conversation_id: (),
    ) -> None:
        # The widgets being refreshed on a conversation now: a board sent meanwhile says so.
        self._refreshing = refreshing
        self._store = store
        # This turn's reads, which a widget she saves from a connector remembers.
        self._reads = reads
        # The user's timezone, so a bare "9:12" she writes is on their today.
        self._zone = zone
        self._conversations = conversations
        self._send_to = send_to
        self._clock = clock
        self._owner = owner
        # One change at a time: her tool call and a tap on the page both load, edit and
        # save, and neither may lose the other's change.
        self._lock = asyncio.Lock()

    async def current(self, conversation_id: str) -> Board | None:
        return await self._store.get(conversation_id, self._owner)

    async def _apply(
        self, conversation_id: str, edit: Callable[[Board | None], Board]
    ) -> tuple[Board | None, Board]:
        """(the board before, the board after) the edit."""
        async with self._lock:
            # Only a live chat has a page to show a board on.
            if not await self._conversations.exists(conversation_id, self._owner):
                raise BoardError("there is no open conversation to put a board in.")
            old = await self._store.get(conversation_id, self._owner)
            zone = self._zone()
            with on_day(day_in(zone)):
                new = edit(old)
            now = self._clock()
            _stamp(new, now)
            page = for_page(new, zone, self._refreshing(conversation_id))
            try:
                await self._store.put(conversation_id, self._owner, new, now)
            except Exception as e:
                logger.warning("board not saved: %s", type(e).__name__)
                raise BoardSaveError("the board could not be saved.") from None
            # Still inside the lock, so pages get boards in the order they were saved;
            # bounded, so a slow page cannot hold every other change.
            try:
                await asyncio.wait_for(self._send_to(conversation_id, page), SEND_S)
            except TimeoutError:
                logger.warning("board not sent: a page was too slow")
        return old, new

    # ── Hers, through the workspace tools ────────────────────────────────

    def _turn_reads(self, conversation_id: str, turn: int | None) -> list[live.Read]:
        if self._reads is None or turn is None:
            return []
        return self._reads.of(conversation_id, turn)

    async def show(
        self,
        conversation_id: str,
        title: str | None,
        widgets: Sequence[Any],
        mode: Mode = "replace",
        *,
        turn: int | None = None,
    ) -> Board:
        """`turn`, her turn the call is in: a widget from a connector remembers that
        turn's reads of it."""
        reads = self._turn_reads(conversation_id, turn)

        def edit(board: Board | None) -> Board:
            new = edits.show(board, title, widgets, mode)
            # Ids are never reused, so the new widgets are the ones with new ids.
            had = {w["id"] for w in (board or {}).get("widgets", [])}
            for widget in new["widgets"]:
                if widget["id"] not in had:
                    live.attach(widget, reads, None)
            return new

        _, new = await self._apply(conversation_id, edit)
        return new

    async def update(
        self, conversation_id: str, widget_id: str, widget: Any, *, turn: int | None = None
    ) -> Board:
        """As `show`: the widget's `live` is rebuilt from this turn's reads, or kept."""
        reads = self._turn_reads(conversation_id, turn)

        def edit(board: Board | None) -> Board:
            new = edits.update(board, widget_id, widget)
            (old,) = [w for w in (board or {})["widgets"] if w["id"] == widget_id]
            (changed,) = [w for w in new["widgets"] if w["id"] == widget_id]
            live.attach(changed, reads, old)
            return new

        _, new = await self._apply(conversation_id, edit)
        return new

    async def remove(
        self, conversation_id: str, ids: Sequence[str] | Literal["all"]
    ) -> tuple[Board, list[dict[str, Any]]]:
        """The board after, and the widgets taken off."""
        old, new = await self._apply(conversation_id, lambda b: edits.remove(b, ids))
        kept = {w["id"] for w in new["widgets"]}
        return new, [w for w in (old or {}).get("widgets", []) if w["id"] not in kept]

    async def close(self, conversation_id: str) -> Board:
        _, new = await self._apply(conversation_id, edits.close)
        return new

    # ── A live widget's refresh (the live widgets spec) ──────────────────

    async def _in_place(
        self, conversation_id: str, before: Mapping[str, Any], change: Callable[[Widget], None]
    ) -> bool:
        """Change one widget where it is, only if it is still exactly `before`: one she
        changed or took off meanwhile is hers, and a refresh never overwrites it."""

        def edit(board: Board | None) -> Board:
            new = copy.deepcopy(board) if board is not None else None
            mine = [w for w in (new or {}).get("widgets", []) if w.get("id") == before.get("id")]
            if new is None or len(mine) != 1 or mine[0] != before:
                raise BoardError("the widget changed meanwhile.")
            change(mine[0])
            return new

        try:
            await self._apply(conversation_id, edit)
        except BoardSaveError:
            raise
        except BoardError:
            return False
        return True

    async def refreshed(
        self, conversation_id: str, before: Mapping[str, Any], after: Widget
    ) -> bool:
        """Save a refreshed widget in place (same id, same position) with a new `at`,
        keeping its reads. False when the widget changed meanwhile."""

        def change(widget: Widget) -> None:
            reads = copy.deepcopy(widget["live"]["reads"])
            widget.clear()
            # No `at` in `after`: `_stamp` gives it now.
            widget.update({"id": before["id"], **copy.deepcopy(after)})
            widget["live"] = {
                "reads": reads,
                "every": live.every({**widget, "live": {"reads": reads}}),
            }

        return await self._in_place(conversation_id, before, change)

    async def refresh_failed(self, conversation_id: str, before: Mapping[str, Any]) -> bool:
        """Keep the old data and count the failure, with when: the page says so."""

        def change(widget: Widget) -> None:
            failed = widget["live"].get("failed")
            count = failed.get("count", 0) if isinstance(failed, Mapping) else 0
            widget["live"]["failed"] = {"count": count + 1, "at": _iso(self._clock())}

        return await self._in_place(conversation_id, before, change)

    # ── The user's, from the page (D7) ───────────────────────────────────

    async def user_close(self, conversation_id: str) -> bool:
        """A page closed the board. False when nothing changed (no board, or it could
        not be saved): the page then needs the board as it really is."""
        try:
            await self.close(conversation_id)
        except BoardError:
            return False
        return True

    async def user_open(self, conversation_id: str) -> bool:
        """A page reopened the board as it was. False when nothing changed (no board,
        already open, an empty one, or a failed save), as for `user_close`."""

        def reopen(board: Board | None) -> Board:
            if board is None or board["open"]:
                raise BoardError("there is no closed board to open.")
            return edits.show(board, None, [])

        try:
            await self._apply(conversation_id, reopen)
        except BoardError:
            return False
        return True

    async def user_remove(self, conversation_id: str, widget_id: str) -> bool:
        """A page took a widget off. False when nothing changed (an id already gone, or
        a failed save), as for `user_close`."""
        try:
            await self.remove(conversation_id, [widget_id])
        except BoardError:
            return False
        return True
