"""Home (SUR-1a): what each briefing tile can show, from what is set up. Behind the
session token like every /api/ route.

It only looks: nothing from a connector's data is read, no network call is made, and
today's day conversation is never made here (opening it is the page's `/day` call).
The states themselves are decided in `workspace/home.py`.

`PUT /api/workspace/home/layout` saves how the user arranged Home (D13) and tells every
open page, so another tab redraws.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from trillion.api.runtime import OWNER, Runtime
from trillion.connectors.google.config import SERVICES
from trillion.connectors.mcp.catalog import ENTRIES
from trillion.persona import day
from trillion.persona.day import SetUp
from trillion.workspace.home import Layout, clean, home

# Generous: a layout names nine tiles, and an old page may send one more or less.
_MAX_IDS = 50
_Id = Annotated[str, Field(max_length=64)]


class LayoutBody(BaseModel):
    """The layout as the page has it. Cleaned as any record is: unknown ids are dropped.
    Both lists are required, so an empty body never resets Home by accident: a reset
    is the page sending the default."""

    model_config = ConfigDict(extra="forbid")

    order: list[_Id] = Field(max_length=_MAX_IDS)
    removed: list[_Id] = Field(max_length=_MAX_IDS)


# Google's built-in services by catalogue id ("calendar" is "google-calendar").
_GOOGLE = {e.service: e.id for e in ENTRIES if e.how == "google"}


def configured(rt: Runtime) -> dict[str, str]:
    """Each catalogue id set up, to the Connected panel's key for its card."""
    out: dict[str, str] = {}
    saved = rt.google.saved if rt.google is not None else None
    for service in SERVICES:
        if saved is not None and saved.with_service(service):
            out[_GOOGLE[service]] = f"google:{service}"
    if rt.slack is not None and rt.slack.token():
        out["slack"] = "chat:slack"
    states = rt.mcp.states.values() if rt.mcp is not None else []
    for s in states:
        # The first server for an entry is the card to reveal, as Connected tools does.
        if s.config.catalog and not s.config.disabled:
            out.setdefault(s.config.catalog, s.config.name)
    return out


async def briefing_built(rt: Runtime) -> bool:
    """Whether today's day board has widgets, without making the day if it is not there."""
    date = day.today(rt.clock(), rt.zone())
    cid = await rt.conversations.find_day(OWNER, date.isoformat())
    if cid is None:
        return False
    board = await rt.board_store.get(cid, OWNER)
    return bool(board and board.get("widgets"))


def create_home_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    def looked() -> tuple[SetUp, dict[str, str], bool]:
        # Google signs in with one click only once this copy has an app to sign in as.
        ready = rt.google is not None and rt.google.client() is not None
        return rt.day_setup(), configured(rt), ready

    async def answer(layout: Layout) -> dict[str, Any]:
        # The keychain and the disk can be slow: never on the event loop.
        setup, found, google_ready = await asyncio.to_thread(looked)
        return home(
            setup,
            found,
            google_ready=google_ready,
            briefing_built=await briefing_built(rt),
            identity=rt.identity,
            layout=layout,
        )

    @router.get("/api/workspace/home")
    async def read() -> dict[str, Any]:
        return await answer(await rt.home_layout())

    @router.put("/api/workspace/home/layout")
    async def arrange(body: LayoutBody) -> dict[str, Any]:
        sent = clean(body.model_dump())
        layout, _ = await rt.arrange_home(lambda _was: sent)
        return await answer(layout)

    return router
