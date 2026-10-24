"""Running a timed ask with nobody there (AGT-9, D5).

It runs in a background conversation of its own, kept like any other and never
opened by a page. She gets her usual instructions, an overlay saying the user is
away, and a registry view holding only the tools the router runs without a card;
the router refuses the rest to `via="schedule"` by name as well. What tools return
stays wrapped as untrusted (SPN-6), and after reading outside content what she
writes is recorded as read, not as the user's words (MEM-5).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from trillion.brain.loop import (
    CallTimed,
    LoopEvent,
    ReplyDelta,
    ToolEnded,
    ToolStarted,
    TurnDone,
    run_turn,
)
from trillion.brain.types import (
    ContextBlock,
    ModelProvider,
    Msg,
    SystemBlock,
    TextBlock,
    Usage,
)
from trillion.persona.prompt import Identity, scheduled, volatile_context
from trillion.scheduler.runner import AskFailed, NoModel
from trillion.scheduler.store import Schedule
from trillion.spine.contract import ToolContext
from trillion.spine.registry import ToolRegistry
from trillion.spine.router import Router
from trillion.spine.untrusted import is_outside
from trillion.storage.sqlite.conversation_store import Kind, SqliteConversationStore

logger = logging.getLogger(__name__)

# A provider that can't be reached or refuses the key before saying anything: the
# ask waits, blocked, for the model to come back (TESTING §4.2).
_UNAVAILABLE = frozenset({"unreachable", "unauthorized"})


class Asker:
    """`run_ask` for the scheduler. Everything it reads is read again on each run."""

    def __init__(
        self,
        *,
        provider: Callable[[], ModelProvider | None],
        router: Router,
        registry: ToolRegistry,
        conversations: SqliteConversationStore,
        system: Callable[[], list[SystemBlock]],
        identity: Callable[[], Identity],
        granted_roots: Callable[[], tuple[Path, ...]],
        zone: Callable[[], str | None],
        clock: Callable[[], int],
        owner: str,
    ) -> None:
        self._provider = provider
        self._router = router
        self._registry = registry
        self._conversations = conversations
        self._system = system
        self._identity = identity
        self._roots = granted_roots
        self._zone = zone
        self._clock = clock
        self._owner = owner

    async def __call__(self, schedule: Schedule) -> str:
        provider = self._provider()
        if provider is None:
            raise NoModel
        conversation = uuid.uuid4().hex
        tools = self._registry.unattended()
        system = [
            *await asyncio.to_thread(self._system),
            SystemBlock(text=scheduled(self._identity())),
        ]
        zone = self._zone() or ""
        now = datetime.now(ZoneInfo(zone)) if zone else datetime.now().astimezone()
        ask = Msg(
            role="user",
            blocks=(
                ContextBlock(text=volatile_context(now, [], zone=zone)),
                TextBlock(text=schedule.body),
            ),
        )
        roots = self._roots()
        outside = False
        began = False
        last: list[str] = []
        pricing = provider.pricing()
        spent = Usage()
        cost: float | None = 0.0 if pricing is not None else None
        created = False

        async def ensure() -> None:
            # Kept only once something happens in it: an ask blocked on every tick for an
            # hour leaves no empty conversation behind each time.
            nonlocal created
            if not created:
                now_ms = self._clock()
                await self._conversations.create(conversation, self._owner, now_ms, "scheduled")
                await self._conversations.append(conversation, self._owner, "user", ask, now_ms)
                created = True

        async def settle(usage: Usage, cost_usd: float | None) -> None:
            if not created and usage == Usage():
                return
            await ensure()
            await self._conversations.record_usage(
                owner_id=self._owner,
                conversation_id=conversation,
                provider=provider.name,
                model=provider.model,
                usage=usage,
                cost_usd=cost_usd,
                now_ms=self._clock(),
            )

        def ctx(call_id: str) -> ToolContext:
            return ToolContext(
                owner_id=self._owner,
                session_id=conversation,
                turn=1,
                call_id=call_id,
                via="schedule",
                granted_roots=roots,
                read_outside=outside,
            )

        async def emit(ev: LoopEvent) -> None:
            nonlocal outside, began, spent, cost
            if isinstance(ev, CallTimed):
                # Counted as it is spent, so a run cut off still records what it used.
                spent = spent + ev.usage
                if pricing is not None and cost is not None:
                    cost += pricing.cost(ev.usage)
            elif isinstance(ev, ReplyDelta):
                began = True
                last.append(ev.text)
            elif isinstance(ev, ToolStarted):
                # What she says before a tool call is on the way; the reply is what follows.
                began = True
                last.clear()
            elif isinstance(ev, ToolEnded) and ev.response.status == "succeeded":
                # The live registry: a server connected after the view was taken still
                # runs by name, and what it returns is still someone else's words.
                d = self._registry.definition(ev.name) or tools.definition(ev.name)
                outside = outside or is_outside(ev.name, d)

        async def save(msg: Msg) -> None:
            await ensure()
            kind: Kind = "assistant" if msg.role == "assistant" else "tool_results"
            await self._conversations.append(conversation, self._owner, kind, msg, self._clock())

        done: TurnDone
        try:
            done = await run_turn(
                provider=provider,
                router=self._router,
                registry=tools,
                system=system,
                history=[ask],
                ctx=ctx,
                emit=emit,
                save=save,
            )
        except BaseException:
            # Timed out, cancelled or broken: what it spent is recorded all the same, and
            # a second cancellation cannot cut the write short.
            try:
                await asyncio.shield(settle(spent, cost))
            except Exception as e:
                logger.warning(
                    "usage of timed ask %s not recorded: %s", schedule.id, type(e).__name__
                )
            raise
        await settle(done.usage, done.cost_usd)
        if done.stop == "provider_error":
            if not began and done.error_kind in _UNAVAILABLE:
                raise NoModel
            raise AskFailed(f"the model failed: {done.error_kind or 'other'}")
        reply = "".join(last).strip()
        if not reply:
            # Nothing to deliver is still something the user should hear about.
            raise AskFailed(f"no reply ({done.stop})")
        return reply
