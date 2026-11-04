"""`POST /api/spend/choice`: a spend card's choice (BRN-8). Behind the session token and
the origin check like every /api/ route (SPN-11).

A choice moves money, so it is taken only by a tap (`via: "tap"`, which the page sends
from its button's click alone), from the page of the conversation the card was sent
to while that page is open (SPN-14): the same rule as an approval. Raising writes the
cap; switching lasts until the 1st; stopping keeps the card in the banner.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from trillion.api.runtime import Runtime
from trillion.spend.desk import Refused

_Id = Annotated[str, Field(min_length=1, max_length=200)]
_STATUS = {"unknown_card": 404, "wrong_session": 403, "not_a_tap": 403, "closed": 409}


class Choice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: _Id
    choice: Annotated[str, Field(min_length=1, max_length=400)]
    amount: float | None = None
    # The conversation of the page that was tapped, and how: only a tap is taken.
    session: _Id
    via: Annotated[str, Field(max_length=20)] = ""


def create_spend_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/spend")

    @router.post("/choice")
    async def choose(body: Choice) -> Any:
        try:
            card = await rt.spend.choose(body.id, body.choice, body.amount, body.session, body.via)
        except Refused as e:
            return JSONResponse({"error": e.code}, status_code=_STATUS.get(e.code, 400))
        return card.to_page()

    return router
