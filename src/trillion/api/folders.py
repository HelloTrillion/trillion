"""Settings → Shared folders, and the folder browser's clicks (the shared folders spec).
Behind the session token like every /api/ route.

Here the user acts for themselves: a folder they add or widen in Settings needs no
card, as one she proposes does. D4 still holds, in the shares store. A click in the
panel moves that page's browser through its session, so her next turn is told where
it is; with no page open on the conversation it is answered from the same fence.

The store and the browser touch the disk and can wait on a slow drive, so every call
into them runs in a thread. A `ShareError` says what went wrong in words for the user.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter
from fastapi import Path as InPath
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from trillion.api.runtime import Runtime
from trillion.files.shares import EVERYONE, Share, ShareError
from trillion.tools.folders import View, open_place

ID = InPath(min_length=1, max_length=64)
_ROUTINE_GONE = "that routine was not found"


class ShareBody(BaseModel):
    """Add a folder (`path`), or change a share (`id`). What is left out stays as it is
    (a new share is read only, for everyone)."""

    id: str | None = Field(default=None, min_length=1, max_length=64)
    path: str | None = Field(default=None, min_length=1, max_length=4096)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    access: Literal["read", "change"] | None = None
    scope: str | None = Field(default=None, min_length=1, max_length=100)


class BrowseBody(BaseModel):
    """Where to go: `path`, or `place` (`places`, `up`, or a name the panel shows). The
    `conversation` whose page clicked, so its browser moves and she is told."""

    path: str | None = Field(default=None, min_length=1, max_length=4096)
    place: str | None = Field(default=None, min_length=1, max_length=4096)
    conversation: str | None = Field(default=None, min_length=1, max_length=100)


def _fail(detail: str) -> JSONResponse:
    return JSONResponse({"error": "refused", "detail": detail}, status_code=400)


def _not_found() -> JSONResponse:
    return JSONResponse({"error": "not_found"}, status_code=404)


def create_folders_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/folders")

    def row(s: Share, state: str | None, titles: dict[str, str]) -> dict[str, Any]:
        routine_id = s.scope.removeprefix("routine:") if s.scope != EVERYONE else None
        return {
            "id": s.id,
            "name": s.name,
            "path": str(s.path),
            "access": s.access,
            "scope": s.scope,
            # The routine it is for, by its title; None when it is for everyone, or the
            # routine is gone (it then reaches nothing until removed with it).
            "routine": (
                {"id": routine_id, "title": titles[routine_id]}
                if routine_id is not None and routine_id in titles
                else None
            ),
            # Why it reaches nothing now ("not found", "moved or replaced by a link",
            # or a reason it can never be shared), or None when it reaches its folder.
            "state": state,
            "run_only": s.run_only,
        }

    async def listing() -> dict[str, Any]:
        def look() -> tuple[list[tuple[Share, str | None]], str | None]:
            shares = rt.shares.all()
            return [(s, rt.shares.state(s)) for s in shares], rt.shares.problem()

        rows, problem = await asyncio.to_thread(look)
        titles = dict(await rt.routine_titles())
        return {"shares": [row(s, state, titles) for s, state in rows], "problem": problem}

    async def known_scope(scope: str | None) -> str | None:
        """Why a scope can't be used, or None. A routine must be one there is."""
        if scope is None or scope == EVERYONE:
            return None
        routine_id = scope.removeprefix("routine:")
        if not scope.startswith("routine:") or routine_id not in dict(await rt.routine_titles()):
            return _ROUTINE_GONE
        return None

    @router.get("")
    async def read() -> dict[str, Any]:
        return await listing()

    @router.post("")
    async def save(body: ShareBody) -> Any:
        if (body.id is None) == (body.path is None):
            return _fail("give a folder's path to add it, or a share's id to change it")
        why = await known_scope(body.scope)
        if why is not None:
            return _fail(why)
        try:
            if body.id is not None:
                # The user's own change: it may widen as well as narrow.
                await asyncio.to_thread(
                    rt.shares.change,
                    body.id,
                    name=body.name,
                    access=body.access,
                    scope=body.scope,
                )
            elif body.path is not None:
                await asyncio.to_thread(
                    rt.shares.put,
                    # `~` is the user's home, as in her tools.
                    Path(body.path).expanduser(),
                    name=body.name,
                    access=body.access or "read",
                    scope=body.scope or EVERYONE,
                )
        except ShareError as e:
            return _fail(str(e))
        except OSError:
            return _fail("your shared folders could not be saved")
        return await listing()

    @router.delete("/{share_id}")
    async def remove(share_id: str = ID) -> Any:
        found = await asyncio.to_thread(lambda: any(s.id == share_id for s in rt.shares.all()))
        if not found:
            return _not_found()
        try:
            await asyncio.to_thread(rt.shares.remove, share_id)
        except ShareError as e:
            return _fail(str(e))
        except OSError:
            return _fail("your shared folders could not be saved")
        return await listing()

    @router.post("/browse")
    async def browse(body: BrowseBody) -> Any:
        target = body.path or body.place
        if target is None:
            return _fail("say where: a folder's path, or a place")
        session = rt.by_conversation.get(body.conversation) if body.conversation else None
        if session is not None:
            view: View = await session.browsed(target)
        else:
            # No page open there: the same fence, from the places list.
            view = await open_place(None, target, rt.shares, rt.folder_look)
        if view.problem is not None:
            return _fail(view.problem)
        return {"type": "folders", **view.frame}

    return router
