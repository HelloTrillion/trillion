"""Devices (SEC-11): the ones she knows, for Settings, and forgetting one. Behind the
session token like every /api/ route.

Forgetting is soft: the device is hidden and unknown again, so its next sign-in is
told about (the security alerts spec, D7).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from trillion.api.runtime import Runtime


class ForgetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]


def create_security_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    @router.get("/api/devices")
    async def devices() -> dict[str, Any]:
        if rt.security is None:
            return {"devices": []}
        return {
            "devices": [
                {
                    "id": d.id,
                    "label": d.label,
                    "host": d.host,
                    "first_seen_ms": d.first_seen_ms,
                    "last_seen_ms": d.last_seen_ms,
                }
                for d in rt.security.book.known()
            ]
        }

    @router.post("/api/devices/forget")
    async def forget(body: ForgetBody) -> dict[str, Any]:
        if rt.security is None or not rt.security.book.forget(body.id, rt.clock()):
            raise HTTPException(status_code=404, detail="not_found")
        return {"ok": True}

    return router


# Mounted by `trillion.api.routes.plugged`: nothing to add to app.py.
ROUTES = (create_security_router,)
