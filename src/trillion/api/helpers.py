"""The helpers panel (the helpers panel spec): her routines, timed asks and watchers,
and the helpers the user made (the your helpers spec), behind the session token like
every /api/ route.

- `GET /api/helpers`: `{helpers: [...], needs_you: n}`, built fresh (`helpers/build.py`).
- `GET /api/helpers/{id}/runs`: the last ten runs, newest first, each `{at_ms, outcome,
  reason, conversation_id}`. A watcher keeps no history of its checks, so its runs are
  its last ten finds (Settings' recent finds): told is `succeeded`, judged not worth
  saying is `nothing`, and one claimed and never told is `failed`.
- `POST /api/helpers/{id}/conversation`: `{conversation_id}`, the one "Talk to it"
  opens. A routine's own, made if it has none yet; today's day conversation for one
  that builds there; for a timed ask, the conversation it was set in, where it reports;
  for a watcher, one of its own, made on first use, remembered in the preference
  `helper_conversations`, and with one host note for its first turn, there only. For a
  helper of the user's, its own chat.
- `POST /api/helpers/{id}/pause`, `.../resume` and `DELETE /api/helpers/{id}`: a helper
  of the user's only (`own:<id>`), with its schedules; each answers with the list. A
  routine, timed ask or watcher has routes of its own. A helper's runs are those of
  all its schedules, its handoffs and its routines.
- `PUT /api/helpers/{id}/look {icon, color}`: a new picture for a helper of the user's,
  saved at once with no card, since it changes nothing it does. 400 for a name not on
  the lists (`helpers/look.py`). Answers with the list.

The page only displays: no route here starts a run or answers a card (SPN-14).
Everything that changes a helper calls `Runtime.helpers_changed`, and every open page
gets `helpers.changed` with the same body (`helpers/live.py`).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter
from fastapi import Path as Segment
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from trillion.attention.settings import TITLED, WATCHERS
from trillion.helpers.build import run_of, snapshot
from trillion.helpers.look import COLORS, ICONS
from trillion.helpers.model import to_json
from trillion.helpers.pausing import pause_helper, resume_helper
from trillion.helpers.store import HelperStore, OwnHelper
from trillion.helpers.wiring import CONVERSATIONS_PREF, OWNER, RuntimeSources, talks
from trillion.persona import day
from trillion.scheduler import runner
from trillion.scheduler.store import Run

if TYPE_CHECKING:
    from trillion.api.runtime import Runtime
    from trillion.storage.sqlite.schedule_store import SqliteScheduleStore

__all__ = ["CONVERSATIONS_PREF", "create_helpers_router", "note_for"]

RUNS = 10
# A find claimed this long ago and never told was lost (as Settings counts it).
LOST_AFTER_MS = 5 * 60_000
HELPER_ID = Segment(
    min_length=3,
    max_length=60,
    pattern=r"^((routine|ask):[a-f0-9]{8}|watcher:[a-z_]{1,30}|own:[a-f0-9]{32})$",
)


class Look(BaseModel):
    icon: str = Field(max_length=40)
    color: str = Field(max_length=40)


def note_for(name: str) -> str:
    """The host note a watcher's own conversation starts with."""
    return (
        f"This conversation is about the {TITLED.get(name, name)} watcher ({name}): what "
        "the user says here is about what it watches. Use speak_up_narrow and "
        "speak_up_widen as usual."
    )


def _not_found() -> JSONResponse:
    return JSONResponse({"error": "not_found"}, status_code=404)


def _find_run(status: str, first_seen_ms: int, now_ms: int) -> tuple[str, str | None]:
    if status in ("told", "dismissed"):
        return "succeeded", None
    if status == "silent":
        return "nothing", None
    if now_ms - first_seen_ms > LOST_AFTER_MS:
        return "failed", "stopped before telling you"
    return "skipped", "telling you now"


def _told(history: list[tuple[Run, str | None]]) -> list[dict[str, Any]]:
    """Runs as the panel tells them, with the conversation each opens."""
    out = []
    for run, opens in history:
        last = run_of(run)
        out.append(
            {
                "at_ms": last.at_ms,
                "outcome": last.outcome,
                "reason": last.reason,
                "conversation_id": opens,
            }
        )
    return out


def create_helpers_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/helpers")
    # One helper conversation made at a time: two taps never make two.
    making = asyncio.Lock()

    def store() -> SqliteScheduleStore:
        return RuntimeSources(rt).schedules

    async def schedule_of(helper_id: str) -> Any:
        kind, _, sid = helper_id.partition(":")
        s = await store().get(sid)
        if s is None or s.owner_id != OWNER or s.kind != "ask":
            return None
        return s if (s.target is not None) == (kind == "routine") else None

    def made() -> HelperStore:
        if rt.helpers is None:  # pragma: no cover - `own_of` found one first
            raise RuntimeError("helpers are not set up")
        return rt.helpers

    async def own_of(helper_id: str) -> OwnHelper | None:
        """The user's own helper this id names, if it is one. None too while helpers
        are not set up: there is none to find."""
        kind, _, hid = helper_id.partition(":")
        if kind != "own" or rt.helpers is None:
            return None
        return await rt.helpers.get(OWNER, hid)

    async def listing() -> dict[str, Any]:
        found, _ = await snapshot(RuntimeSources(rt))
        return to_json(found)

    @router.get("")
    async def read() -> dict[str, Any]:
        return await listing()

    @router.post("/{helper_id}/pause")
    async def pause(helper_id: str = HELPER_ID) -> Any:
        h = await own_of(helper_id)
        if h is None:
            return _not_found()
        # Already paused is fine: the tap is answered with how it stands.
        await pause_helper(made(), store(), OWNER, h.id, rt.clock())
        return await listing()

    @router.post("/{helper_id}/resume")
    async def resume(helper_id: str = HELPER_ID) -> Any:
        h = await own_of(helper_id)
        if h is None:
            return _not_found()
        # Its routines from their next due time after now: none is run late.
        await resume_helper(made(), store(), runner, OWNER, h.id, rt.clock())
        return await listing()

    @router.put("/{helper_id}/look")
    async def look(body: Look, helper_id: str = HELPER_ID) -> Any:
        if body.icon not in ICONS or body.color not in COLORS:
            return JSONResponse({"error": "bad_request"}, status_code=400)
        h = await own_of(helper_id)
        if h is None or not await made().set_look(OWNER, h.id, body.icon, body.color):
            return _not_found()
        return await listing()

    @router.delete("/{helper_id}")
    async def delete(helper_id: str = HELPER_ID) -> Any:
        h = await own_of(helper_id)
        # Its schedules go with it; its chat stays in History as a plain one.
        if h is None or not await made().delete(OWNER, h.id, rt.clock()):
            return _not_found()
        return await listing()

    @router.get("/{helper_id}/runs")
    async def runs(helper_id: str = HELPER_ID) -> Any:
        if helper_id.startswith("own:"):
            h = await own_of(helper_id)
            if h is None:
                return _not_found()
            return {"runs": _told(await store().helper_history(OWNER, h.id, RUNS))}
        if helper_id.startswith("watcher:"):
            name = helper_id.removeprefix("watcher:")
            if name not in WATCHERS or rt.attention_store is None:
                return _not_found()
            now = rt.clock()
            finds = [f for f in await rt.attention_store.recent(OWNER, now) if f.watcher == name]
            out = []
            for f in finds[:RUNS]:
                outcome, reason = _find_run(f.status, f.first_seen_ms, now)
                out.append(
                    {
                        "at_ms": f.first_seen_ms,
                        "outcome": outcome,
                        "reason": reason,
                        "conversation_id": None,
                    }
                )
            return {"runs": out}
        s = await schedule_of(helper_id)
        if s is None:
            return _not_found()
        return {"runs": _told(await store().history(OWNER, s.id, RUNS))}

    @router.post("/{helper_id}/conversation")
    async def conversation(helper_id: str = HELPER_ID) -> Any:
        if helper_id.startswith("own:"):
            h = await own_of(helper_id)
            if h is None:
                return _not_found()
            return {"conversation_id": h.conversation_id}
        if helper_id.startswith("watcher:"):
            name = helper_id.removeprefix("watcher:")
            if name not in WATCHERS:
                return _not_found()
            return {"conversation_id": await watcher_conversation(name)}
        s = await schedule_of(helper_id)
        if s is None or s.status == "cancelled":
            return _not_found()
        now = rt.clock()
        if s.target == "day":
            date = day.today(now, rt.zone())
            found, _ = await rt.conversations.day_conversation(
                OWNER, date.isoformat(), day.title(date), now
            )
            return {"conversation_id": found}
        if s.target == "own":
            return {"conversation_id": await routine_conversation(s.id)}
        # A timed ask reports where it was set.
        if await rt.conversations.exists(s.created_in, OWNER):
            return {"conversation_id": s.created_in}
        return JSONResponse({"error": "no_conversation"}, status_code=404)

    async def routine_conversation(schedule_id: str) -> str:
        async with making:
            # Read again under the lock: a tap just before may have made it.
            s = await store().get(schedule_id)
            if s is None:  # pragma: no cover - read just before, and never deleted
                raise RuntimeError("the routine went")
            mine = s.conversation_id
            if mine is not None and await rt.conversations.exists(mine, OWNER):
                return mine
            # As its first run would: a conversation of its own, titled with its name.
            made = await rt.conversations.routine_conversation(OWNER, s.title, rt.clock())
            async with _kept_or_dropped(made):
                await store().set_conversation(s.id, made)
            return made

    async def watcher_conversation(name: str) -> str:
        key = f"watcher:{name}"
        async with making:
            mine = await talks(rt)
            found = mine.get(key)
            if found is not None and await rt.conversations.exists(found, OWNER):
                return found
            now = rt.clock()
            made = uuid.uuid4().hex
            await rt.conversations.create(made, OWNER, now)
            async with _kept_or_dropped(made):
                await rt.conversations.set_title(made, OWNER, TITLED.get(name, name), "user")
                async with rt.helper_talks:
                    # Read again under the lock, which a pruning read takes too.
                    raw = await rt.preferences.get(OWNER, CONVERSATIONS_PREF)
                    kept = dict(raw) if isinstance(raw, dict) else {}
                    await rt.preferences.put(OWNER, CONVERSATIONS_PREF, {**kept, key: made}, now)
                await rt.helper_note_keep(made, note_for(name))
        rt.helpers_changed()
        return made

    @asynccontextmanager
    async def _kept_or_dropped(made: str) -> AsyncIterator[None]:
        """A conversation just made is kept only once it is linked to its helper: one
        nothing points to would sit in History, empty, for good."""
        try:
            yield
        except BaseException:
            await asyncio.shield(rt.conversations.delete(made, OWNER, rt.clock()))
            raise

    return router
