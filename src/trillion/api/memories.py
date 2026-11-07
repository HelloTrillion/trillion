"""Settings → Memories (MEM-6). Behind the session token like every /api/ route.

What she remembers, and a way to correct it. Reading lists the memories that are
current; editing writes through the same `MemoryBook.write` her own tool uses, as
the user's own statement (MEM-6a), and the old version is kept, superseded by the
new one (MEM-7). There is no route that forgets: forgetting is `memory_forget`,
raised from the page over the session's socket so it asks on the same card as
when she proposes it (MEM-3, SPN-2, SPN-14). The text of a memory is never logged.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi import Path as Segment
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from trillion.api.runtime import Runtime
from trillion.memory import files
from trillion.memory.book import MAX_ACTIVE, MemoryBook
from trillion.memory.types import MAX_TEXT, Memory, MemoryError_

_ID = r"^mem_[0-9a-f]{12}$"


class Edit(BaseModel):
    # A little over the bound, so an overlong edit is told as too long, not as malformed.
    text: str = Field(max_length=MAX_TEXT * 2)
    # The version the page showed: an edit made on a memory that changed since is refused.
    version: int = Field(ge=1)


def row(m: Memory) -> dict[str, Any]:
    return {
        "id": m.id,
        "type": m.type,
        "text": m.text,
        "source": m.source,
        "moves": m.moves,
        "status": m.status,
        "version": m.version,
        "created_ms": m.created_at,
        "as_of_ms": m.as_of,
        "supersedes": m.supersedes,
    }


def _fail(error: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": error}, status_code=status)


def create_memories_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    def book() -> MemoryBook:
        return rt.memory

    def state() -> dict[str, Any]:
        b = book()
        current = b.recall("", limit=MAX_ACTIVE)
        sync = b.last_sync
        return {
            "memories": [row(m) for m in current],
            "max_text": MAX_TEXT,
            # When the files and the index disagreed at startup, the page says so (MEM-6b).
            "diverged": sync.diverged,
            "unreadable": sync.unreadable,
        }

    @router.get("/api/memories")
    async def read() -> dict[str, Any]:
        return state()

    @router.put("/api/memories/{memory_id}")
    async def edit(body: Edit, memory_id: str = Segment(pattern=_ID)) -> Any:
        b = book()
        old = b.peek(memory_id)
        if old is None or not old.current:
            return _fail("not_found", 404)
        if old.version != body.version:
            return _fail("changed", 409)
        if files.clean(body.text) == old.text:
            return _fail("unchanged")
        try:
            done = await b.write(
                type=old.type,
                text=body.text,
                # What the user typed here is what they said (MEM-6a), whoever said it first.
                source="user_statement",
                moves=old.moves,
                replaces=old.id,
                now_ms=rt.clock(),
            )
        except MemoryError_ as e:
            return _fail(e.kind, 404 if e.kind == "not_found" else 400)
        return {**state(), "id": done.memory.id, "duplicate": done.duplicate}

    return router


# Mounted by `trillion.api.routes.plugged`: nothing to add to app.py.
ROUTES = (create_memories_router,)
