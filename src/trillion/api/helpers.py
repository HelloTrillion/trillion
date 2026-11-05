"""The helpers panel (the helpers panel spec): her routines, timed asks and watchers,
behind the session token like every /api/ route.

- `GET /api/helpers`: `{helpers: [...], needs_you: n}`, built fresh (`helpers/build.py`).
- `GET /api/helpers/{id}/runs`: the last ten runs, newest first, each `{at_ms, outcome,
  reason, conversation_id}`. A watcher keeps no history of its checks, so its runs are
  its last ten finds (Settings' recent finds): told is `succeeded`, judged not worth
  saying is `nothing`, and one claimed and never told is `failed`.
- `POST /api/helpers/{id}/conversation`: `{conversation_id}`, the one "Talk to it"
  opens. A routine's own, made if it has none yet; today's day conversation for one
  that builds there; for a timed ask, the conversation it was set in, where it reports;
  for a watcher, one of its own, made on first use, remembered in the preference
  `helper_conversations`, and with one host note for its first turn, there only.

The page only displays: no route here starts a run or answers a card (SPN-14).
Everything that changes a helper calls `Runtime.helpers_changed`, and every open page
gets `helpers.changed` with the same body (`helpers/live.py`).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter
from fastapi import Path as Segment
from fastapi.responses import JSONResponse

from trillion.attention.runner import WatcherStatus
from trillion.attention.settings import TITLED, WATCHERS
from trillion.helpers.build import run_of, snapshot
from trillion.helpers.live import Built
from trillion.helpers.model import to_json
from trillion.persona import day
from trillion.spend.types import Work
from trillion.spine.confirm import PendingAction, Status

if TYPE_CHECKING:
    from trillion.api.runtime import Runtime
    from trillion.storage.sqlite.schedule_store import SqliteScheduleStore

OWNER = "local"
# Each watcher's conversation, by helper id: {"watcher:<name>": conversation_id}.
CONVERSATIONS_PREF = "helper_conversations"
RUNS = 10
# A find claimed this long ago and never told was lost (as Settings counts it).
LOST_AFTER_MS = 5 * 60_000
HELPER_ID = Segment(
    min_length=3, max_length=60, pattern=r"^((routine|ask):[a-f0-9]{8}|watcher:[a-z_]{1,30})$"
)


def note_for(name: str) -> str:
    """The host note a watcher's own conversation starts with."""
    return (
        f"This conversation is about the {TITLED.get(name, name)} watcher ({name}): what "
        "the user says here is about what it watches. Use speak_up_narrow and "
        "speak_up_widen as usual."
    )


@dataclass(frozen=True)
class RuntimeSources:
    """What the list is built from, in the running app."""

    rt: Runtime
    owner: str = OWNER

    @property
    def schedules(self) -> SqliteScheduleStore:
        if self.rt.schedules is None:
            raise RuntimeError("the scheduler is not running")
        return self.rt.schedules

    def now_ms(self) -> int:
        return self.rt.clock()

    async def watchers(self) -> list[WatcherStatus]:
        runner = self.rt.attention
        return await runner.statuses() if runner is not None else []

    async def capped(self) -> list[Work]:
        pid = getattr(self.rt.background_provider, "provider_id", None)
        return await self.rt.spend.paused(pid) if pid is not None else []

    async def cards(self) -> Sequence[PendingAction]:
        return await self.rt.spine_store.list_by_status(Status.PROPOSED)

    async def conversations(self) -> Mapping[str, str]:
        return await talks(self.rt)


async def talks(rt: Runtime) -> dict[str, str]:
    raw = await rt.preferences.get(OWNER, CONVERSATIONS_PREF)
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, str)}


async def built(rt: Runtime) -> Built:
    found, wake = await snapshot(RuntimeSources(rt))
    return to_json(found), wake


async def send_all(rt: Runtime, body: dict[str, Any]) -> None:
    """Every open page, as `_broadcast_title` reaches every page on a conversation."""
    fields = {k: v for k, v in body.items() if k != "type"}
    for session in list(rt.sessions):
        await session.send(str(body["type"]), **fields)


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


def create_helpers_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/helpers")
    # One watcher conversation made at a time: two taps never make two.
    making = asyncio.Lock()

    def store() -> SqliteScheduleStore:
        return RuntimeSources(rt).schedules

    async def schedule_of(helper_id: str) -> Any:
        kind, _, sid = helper_id.partition(":")
        s = await store().get(sid)
        if s is None or s.owner_id != OWNER or s.kind != "ask":
            return None
        return s if (s.target is not None) == (kind == "routine") else None

    @router.get("")
    async def read() -> dict[str, Any]:
        body, _ = await built(rt)
        return body

    @router.get("/{helper_id}/runs")
    async def runs(helper_id: str = HELPER_ID) -> Any:
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
        out = []
        for run, opens in await store().history(OWNER, s.id, RUNS):
            last = run_of(run)
            out.append(
                {
                    "at_ms": last.at_ms,
                    "outcome": last.outcome,
                    "reason": last.reason,
                    "conversation_id": opens,
                }
            )
        return {"runs": out}

    @router.post("/{helper_id}/conversation")
    async def conversation(helper_id: str = HELPER_ID) -> Any:
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
            mine = s.conversation_id
            if mine is not None and await rt.conversations.exists(mine, OWNER):
                return {"conversation_id": mine}
            # As its first run would: a conversation of its own, titled with its name.
            made = await rt.conversations.routine_conversation(OWNER, s.title, now)
            await store().set_conversation(s.id, made)
            return {"conversation_id": made}
        # A timed ask reports where it was set.
        if await rt.conversations.exists(s.created_in, OWNER):
            return {"conversation_id": s.created_in}
        return JSONResponse({"error": "no_conversation"}, status_code=404)

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
            await rt.conversations.set_title(made, OWNER, TITLED.get(name, name), "user")
            await rt.preferences.put(OWNER, CONVERSATIONS_PREF, {**mine, key: made}, now)
            await rt.helper_note_keep(made, note_for(name))
        rt.helpers_changed()
        return made

    return router
