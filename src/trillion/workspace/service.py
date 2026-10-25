"""Her board's service (SUR-1): apply a change, save it, tell the pages.

Every change loads the conversation's board, applies one pure edit from `board.py`,
saves the result and sends it to every page open on that conversation. A refused
edit (`BoardError`) changes nothing, saved or shown.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal, Protocol

from trillion.workspace import board as edits
from trillion.workspace.board import Board, BoardError, Mode

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


def for_page(board: Board | None) -> Board | None:
    """The board as the page draws it: the id counter is the server's own."""
    if board is None:
        return None
    return {k: v for k, v in board.items() if k != "next_id"}


class Boards:
    def __init__(
        self,
        store: BoardStore,
        conversations: Conversations,
        send_to: SendTo,
        *,
        clock: Callable[[], int],
        owner: str,
    ) -> None:
        self._store = store
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
            new = edit(old)
            try:
                await self._store.put(conversation_id, self._owner, new, self._clock())
            except Exception as e:
                logger.warning("board not saved: %s", type(e).__name__)
                raise BoardSaveError("the board could not be saved.") from None
            # Still inside the lock, so pages get boards in the order they were saved;
            # bounded, so a slow page cannot hold every other change.
            try:
                await asyncio.wait_for(self._send_to(conversation_id, for_page(new)), SEND_S)
            except TimeoutError:
                logger.warning("board not sent: a page was too slow")
        return old, new

    # ── Hers, through the workspace tools ────────────────────────────────

    async def show(
        self,
        conversation_id: str,
        title: str | None,
        widgets: Sequence[Any],
        mode: Mode = "replace",
    ) -> Board:
        _, new = await self._apply(conversation_id, lambda b: edits.show(b, title, widgets, mode))
        return new

    async def update(self, conversation_id: str, widget_id: str, widget: Any) -> Board:
        _, new = await self._apply(conversation_id, lambda b: edits.update(b, widget_id, widget))
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

    # ── The user's, from the page (D7) ───────────────────────────────────

    async def user_close(self, conversation_id: str) -> None:
        """A page closed the board. Nothing to close is not an error."""
        try:
            await self.close(conversation_id)
        except BoardError:
            pass

    async def user_remove(self, conversation_id: str, widget_id: str) -> None:
        """A page took a widget off. An id that is not there (already gone) is ignored."""
        try:
            await self.remove(conversation_id, [widget_id])
        except BoardError:
            pass
