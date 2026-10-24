"""The thread list (SUR-1): the user's conversations, newest first, with search,
rename, and a soft delete that can be undone (SPN-9). Behind the session token like
every /api/ route."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from trillion.api.runtime import OWNER, Runtime
from trillion.storage.sqlite.conversation_store import tidy_title

LIMIT_MAX = 100


class Rename(BaseModel):
    title: str


def _not_found() -> JSONResponse:
    return JSONResponse({"error": "not_found"}, status_code=404)


def create_conversations_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/conversations")

    @router.get("")
    async def listing(
        q: str = "", before: int | None = None, before_id: str = "", limit: int = 50
    ) -> dict[str, Any]:
        # The cursor is the last row shown: its time and id, so equal times never skip.
        cursor = (before, before_id) if before is not None else None
        rows, more = await rt.conversations.list_chats(
            OWNER, query=q or None, before=cursor, limit=max(1, min(limit, LIMIT_MAX))
        )
        return {
            "conversations": [
                {"id": r.id, "title": r.title, "last_message_at": r.last_message_at} for r in rows
            ],
            "more": more,
        }

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
