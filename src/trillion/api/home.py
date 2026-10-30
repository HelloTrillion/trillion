"""Home (SUR-1a): what each briefing tile can show, from what is set up. Behind the
session token like every /api/ route.

It only looks: nothing from a connector's data is read, no network call is made, and
today's day conversation is never made here (opening it is the page's `/day` call).
The states themselves are decided in `workspace/home.py`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from trillion.api.runtime import OWNER, Runtime
from trillion.connectors.google.config import SERVICES
from trillion.connectors.mcp.catalog import ENTRIES
from trillion.persona import day
from trillion.workspace.home import home

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

    @router.get("/api/workspace/home")
    async def read() -> dict[str, Any]:
        return home(
            rt.day_setup(),
            configured(rt),
            # Google signs in with one click only once this copy has an app to sign in as.
            google_ready=rt.google is not None and rt.google.client() is not None,
            briefing_built=await briefing_built(rt),
            identity=rt.identity,
        )

    return router
