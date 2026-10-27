"""The thread list (SUR-1): the user's conversations, newest first, with search,
rename, and a soft delete that can be undone (SPN-9). Behind the session token like
every /api/ route."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from trillion.api.runtime import OWNER, Runtime
from trillion.persona import day
from trillion.storage.sqlite.conversation_store import tidy_title

LIMIT_MAX = 100
QUERY_MAX = 200
# Before tidying, which cuts a title to its own limit.
TITLE_MAX = 500


class Rename(BaseModel):
    title: str = Field(max_length=TITLE_MAX)


def _not_found() -> JSONResponse:
    return JSONResponse({"error": "not_found"}, status_code=404)


def create_conversations_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/conversations")

    @router.get("")
    async def listing(
        q: str = Query("", max_length=QUERY_MAX),
        before: int | None = Query(None, ge=0, le=2**63 - 1),
        before_id: str = Query("", max_length=100),
        limit: int = Query(50, ge=1, le=LIMIT_MAX),
    ) -> Any:
        # The cursor is the last row shown: its time and id, so equal times never skip.
        if before is not None and not before_id:
            return JSONResponse({"error": "bad_request"}, status_code=400)
        cursor = (before, before_id) if before is not None else None
        rows, more = await rt.conversations.list_chats(
            OWNER, query=q or None, before=cursor, limit=limit
        )
        return {
            "conversations": [
                {
                    "id": r.id,
                    "title": r.title,
                    "last_message_at": r.last_message_at,
                    "kind": r.kind,
                    "day": r.day,
                }
                for r in rows
            ],
            "more": more,
        }

    @router.get("/day")
    async def today() -> Any:
        """Today's day conversation in the user's zone, made the first time it is asked
        for (SUR-1). Opening it builds nothing: the page asks for that separately."""
        now = rt.clock()
        date = day.today(now, rt.zone())
        cid, created = await rt.conversations.day_conversation(
            OWNER, date.isoformat(), day.title(date), now
        )
        return {"id": cid, "created": created}

    @router.patch("/{conversation_id}")
    async def rename(conversation_id: str, body: Rename) -> Any:
        title = tidy_title(body.title)
        if not title:
            return JSONResponse({"error": "bad_request"}, status_code=400)
        if not await rt.conversations.set_title(conversation_id, OWNER, title, "user"):
            return _not_found()
        live = rt.by_conversation.get(conversation_id)
        if live is not None:
            await live.renamed(title)
        return {"id": conversation_id, "title": title}

    @router.delete("/{conversation_id}")
    async def delete(conversation_id: str) -> Any:
        if not await rt.conversations.delete(conversation_id, OWNER, rt.clock()):
            return _not_found()
        # Its reads stop: nothing is told of them, and nothing is saved there.
        await rt.jobs.cancel(conversation_id)
        live = rt.by_conversation.get(conversation_id)
        if live is not None:
            await live.deleted()
        return {"id": conversation_id}

    @router.post("/{conversation_id}/restore")
    async def restore(conversation_id: str) -> Any:
        if not await rt.conversations.restore(conversation_id, OWNER):
            return _not_found()
        return {"id": conversation_id}

    return router
