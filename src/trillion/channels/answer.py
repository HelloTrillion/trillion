"""Answering one channel message: answers only, with no tools at all (SUR-5, SPN-16).

The model is called directly with an empty tool list, not through the tool loop,
because the loop always offers the spine's own tools (confirming what waits for
the user among them). Here there is nothing to call, so nothing said in a channel
can act, read anything private, or approve anything.

At a hard spend cap (the spend caps spec) a message is not answered at all: nothing is
sent back, and its conversation shows nothing new.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from importlib.resources import files

from trillion.brain.types import ModelProvider, Msg, SystemBlock, TextBlock, TextDelta
from trillion.channels.base import MAX_IN, MAX_OUT, Inbound
from trillion.spend.caller import as_caller
from trillion.spend.gate import SpendCapped
from trillion.spend.types import Work
from trillion.storage.sqlite.conversation_store import SqliteConversationStore

logger = logging.getLogger(__name__)

HISTORY = 30  # messages of the conversation she sees
MAX_TOKENS = 1024
PLATFORMS = {"slack": "Slack", "telegram": "Telegram"}


def overlay(platform: str, person: str, agent_name: str, user_name: str) -> str:
    text = (files("trillion.persona") / "prompts" / "channel.md").read_text(encoding="utf-8")
    text = "\n".join(line for line in text.splitlines() if not line.startswith("<!--")).strip()
    return (
        text.replace("{platform}", PLATFORMS.get(platform, platform))
        .replace("{person}", person or "Someone")
        .replace("{agent_name}", agent_name)
        .replace("{user_name}", user_name)
    )


@dataclass
class Answerer:
    """Everything is looked up when a message comes, so a changed model or persona is used
    at once. `provider()` may return None when no model is set up."""

    conversations: SqliteConversationStore
    provider: Callable[[], ModelProvider | None]
    system: Callable[[], Sequence[SystemBlock]]
    names: Callable[[], tuple[str, str]]  # (agent_name, user_name)
    clock: Callable[[], int]
    owner: str
    # Whether her model is at its hard spend cap: then no answer is made at all.
    held: Callable[[Work], Awaitable[bool]] | None = None
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    async def answer(self, m: Inbound) -> str | None:
        """Her answer, or None to send nothing (at a hard spend cap)."""
        provider = self.provider()
        if provider is None:
            return "I'm not set up to talk yet."
        if self.held is not None and await self.held("channels"):
            logger.info("channel reply paused at the spend cap")
            return None
        store = self.conversations
        cid = m.conversation
        async with self._locks.setdefault(cid, asyncio.Lock()):
            now = self.clock()
            # Made only once a call is paid for (its usage row names it) or there is an
            # answer to keep: one refused at a spend cap leaves nothing.
            existed = await store.exists(cid, self.owner)
            history = await store.history(cid, self.owner) if existed else []
            made = existed

            async def ensure() -> None:
                nonlocal made
                if not made:
                    await store.create(cid, self.owner, now)
                    made = True

            past = [msg for _, msg in history][-HISTORY:]
            said = Msg(
                role="user",
                blocks=(TextBlock(text=f"{m.name or 'Someone'} wrote: {m.text[:MAX_IN]}"),),
            )
            agent, user = self.names()
            system = [
                *self.system(),
                SystemBlock(text=overlay(m.platform, m.name, agent, user)),
            ]
            text: list[str] = []
            try:
                # A channel's answer is background work, counted with its conversation:
                # the metered provider records the call (BRN-7).
                with as_caller("background", cid, ready=ensure, work="channels"):
                    async for ev in provider.stream(
                        system=system,
                        messages=[*_tidy(past), said],
                        tools=[],
                        max_tokens=MAX_TOKENS,
                    ):
                        if isinstance(ev, TextDelta):
                            text.append(ev.text)
            except SpendCapped:
                # Refused before anything was sent: dropped, with nothing kept.
                logger.info("channel reply paused at the spend cap")
                return None
            except Exception as e:
                logger.warning("channel reply failed: %s", type(e).__name__)
                return "Sorry, I can't answer right now."
            reply = "".join(text).strip()[:MAX_OUT] or "…"
            await ensure()
            await store.append(cid, self.owner, "user", said, now)
            await store.append(
                cid,
                self.owner,
                "assistant",
                Msg(role="assistant", blocks=(TextBlock(text=reply),)),
                self.clock(),
            )
            return reply


def _tidy(past: list[Msg]) -> list[Msg]:
    """Only text, starting with someone's message, so any provider accepts it."""
    out = [
        Msg(role=m.role, blocks=tuple(b for b in m.blocks if isinstance(b, TextBlock)))
        for m in past
    ]
    out = [m for m in out if m.blocks]
    while out and out[0].role != "user":
        out.pop(0)
    return out
