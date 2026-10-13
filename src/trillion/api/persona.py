"""Onboarding and renaming: who the user is and what they call her (ONB-3, PER-1a, PER-6).

This is the privileged setup path. Like the rest of Settings it needs the session
token and the app's own origin, and the model cannot reach it: there is no tool
that reads or writes here. What the user types is validated, written to `USER.md`
and `IDENTITY.md` in the user data directory, and taken up from the next turn.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from trillion.api.runtime import Runtime
from trillion.persona.prompt import DEFAULT_AGENT_NAME
from trillion.persona.setup import (
    DEFAULT_DIRECTNESS,
    DIRECTNESS,
    Directness,
    Profile,
    SetupError,
)

# Generous on the wire so a name that is too long gets a reason, not a bare 422.
_WIRE_LIMIT = 200


class Introduce(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_name: str = Field(max_length=_WIRE_LIMIT)
    agent_name: str = Field(default=DEFAULT_AGENT_NAME, max_length=_WIRE_LIMIT)
    timezone: str = Field(default="", max_length=_WIRE_LIMIT)
    directness: Directness = DEFAULT_DIRECTNESS


def _describe(rt: Runtime) -> dict[str, Any]:
    p = rt.profile
    return {
        "onboarding": rt.onboarding(),
        "user_name": p.user_name if p else None,
        "agent_name": rt.identity.agent_name,
        "timezone": p.timezone if p else "",
        "directness": p.directness if p else DEFAULT_DIRECTNESS,
        "choices": {"directness": list(DIRECTNESS)},
    }


def create_persona_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/settings/persona")

    @router.get("")
    async def read() -> dict[str, Any]:
        return _describe(rt)

    @router.put("")
    async def introduce(body: Introduce) -> Any:
        try:
            await rt.use_profile(
                Profile(
                    user_name=body.user_name,
                    agent_name=body.agent_name,
                    timezone=body.timezone,
                    directness=body.directness,
                )
            )
        except SetupError as e:
            return JSONResponse({"error": e.code}, status_code=400)
        except OSError:
            # The folder could not be written. Nothing was adopted.
            return JSONResponse({"error": "not_saved"}, status_code=500)
        return _describe(rt)

    @router.post("/skip")
    async def skip() -> dict[str, Any]:
        if rt.profile is None:
            await rt.skip_onboarding()
        return _describe(rt)

    return router
