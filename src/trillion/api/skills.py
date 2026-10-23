"""Settings → Skills (CON-6). Behind the session token like every /api/ route.

Reads and changes skills.json through the library. Never returns a skill's
instructions: the page shows what the skill says it is for, not how. Each row
carries its fingerprint, a hash of the user's own files, so turning a skill on
can refuse files that changed since the page showed them.

The routes are plain functions, so FastAPI runs them off the event loop: reading
every skill touches the disk. A lock keeps two changes from racing on the file.
"""

from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from trillion.api.runtime import Runtime
from trillion.connectors.google.tools import TOOLS as GOOGLE_TOOLS
from trillion.persona.prompt import skill_index
from trillion.skills.library import Entry, Library, LibraryError


class FolderBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


class Switch(BaseModel):
    id: str = Field(min_length=1, max_length=1100)
    on: bool
    # The fingerprint the card showed; turning on refuses files that changed since.
    fingerprint: str | None = Field(default=None, max_length=100)


def _fail(detail: str) -> JSONResponse:
    return JSONResponse({"error": "refused", "detail": detail}, status_code=400)


def create_skills_router(rt: Runtime) -> APIRouter:
    router = APIRouter()
    lock = threading.Lock()

    def library() -> Library:
        if rt.skills is None:
            raise RuntimeError("skills are not running")
        return rt.skills

    def row(e: Entry, have: set[str]) -> dict[str, Any]:
        s = e.skill
        return {
            "id": e.id,
            "source": e.source,
            "folder": str(e.root or ""),
            "name": e.name,
            "description": s.description if s else "",
            "license": s.license if s else "",
            "tools": [{"name": t, "available": t in have} for t in (s.allowed_tools if s else ())],
            "has_scripts": bool(s and s.has_scripts),
            "state": e.state,
            "reason": e.reason,
            "fingerprint": e.fingerprint,
        }

    def state() -> dict[str, Any]:
        lib = library()
        # Google's tools count while Google is not connected: Trillion has them.
        have = {d.name for d in rt.registry.definitions()} | {
            t.name() for tools in GOOGLE_TOOLS.values() for t in tools
        }
        entries = lib.scan()
        on = sorted((e for e in entries if e.state == "on"), key=lambda e: (e.name, e.id))
        _, unlisted = skill_index(lib.prompt_lines(entries=on))
        return {
            "folders": [{"path": str(f.path), "found": f.found} for f in lib.folders()],
            "skills": [row(e, have) for e in entries],
            # Turned on, but past what her instructions list.
            "unlisted": unlisted,
        }

    @router.get("/api/skills")
    def read() -> dict[str, Any]:
        return state()

    @router.post("/api/skills/folders")
    def add(body: FolderBody) -> Any:
        try:
            with lock:
                library().add_folder(body.path)
        except LibraryError as e:
            return _fail(str(e))
        return state()

    @router.delete("/api/skills/folders")
    def remove(body: FolderBody) -> Any:
        try:
            with lock:
                library().remove_folder(body.path)
        except LibraryError as e:
            return _fail(str(e))
        return state()

    @router.patch("/api/skills")
    def switch(body: Switch) -> Any:
        try:
            with lock:
                if body.on:
                    library().turn_on(body.id, expected=body.fingerprint)
                else:
                    library().turn_off(body.id)
        except LibraryError as e:
            return _fail(str(e))
        return state()

    return router
