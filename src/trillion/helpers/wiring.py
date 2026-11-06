"""The helpers panel wired to the running app: what the list is built from, and how
`helpers.changed` reaches every page. Free of the API layer, so the runtime builds its
notifier from it without importing a router."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from trillion.attention.runner import WatcherStatus
from trillion.helpers.build import snapshot
from trillion.helpers.live import Built
from trillion.helpers.model import to_json
from trillion.helpers.store import OwnHelper
from trillion.spend.types import Work
from trillion.spine.confirm import PendingAction, Status

if TYPE_CHECKING:
    from trillion.api.runtime import Runtime
    from trillion.storage.sqlite.schedule_store import SqliteScheduleStore

OWNER = "local"
# Each watcher's conversation, by helper id: {"watcher:<name>": conversation_id}.
CONVERSATIONS_PREF = "helper_conversations"


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

    async def own(self) -> Sequence[OwnHelper]:
        made = self.rt.helpers
        return await made.all(self.owner) if made is not None else []


async def talks(rt: Runtime) -> dict[str, str]:
    """Each helper's own conversation, by helper id. One deleted since is forgotten
    here, with any note still kept for it."""
    raw = await rt.preferences.get(OWNER, CONVERSATIONS_PREF)
    if not isinstance(raw, dict):
        return {}
    found = {str(k): v for k, v in raw.items() if isinstance(v, str)}
    gone = {c for c in found.values() if not await rt.conversations.exists(c, OWNER)}
    if not gone:
        return found
    async with rt.helper_talks:
        # Read again under the lock: only what is gone is taken out, never one just made.
        now = await rt.preferences.get(OWNER, CONVERSATIONS_PREF)
        kept = {k: v for k, v in (now if isinstance(now, dict) else {}).items() if v not in gone}
        await rt.preferences.put(OWNER, CONVERSATIONS_PREF, kept, rt.clock())
    for conversation_id in gone:
        await rt.helper_note_said(conversation_id)
    return {k: v for k, v in found.items() if v not in gone}


async def built(rt: Runtime) -> Built:
    found, wake = await snapshot(RuntimeSources(rt))
    return to_json(found), wake


async def send_all(rt: Runtime, body: dict[str, Any]) -> None:
    """Every open page, as `_broadcast_title` reaches every page on a conversation."""
    fields = {k: v for k, v in body.items() if k != "type"}
    for session in list(rt.sessions):
        await session.send(str(body["type"]), **fields)
