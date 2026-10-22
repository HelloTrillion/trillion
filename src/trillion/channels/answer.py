"""Answering one channel message: answers only, with no tools at all (SUR-5, SPN-16).

The model is called directly with an empty tool list, not through the tool loop,
because the loop always offers the spine's own tools (confirming what waits for
the user among them). Here there is nothing to call, so nothing said in a channel
can act, read anything private, or approve anything.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from importlib.resources import files

from trillion.brain.types import Done, ModelProvider, Msg, SystemBlock, TextBlock, TextDelta, Usage
from trillion.channels.base import MAX_IN, MAX_OUT, Inbound
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
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    async def answer(self, m: Inbound) -> str:
        provider = self.provider()
        if provider is None:
            return "I'm not set up to talk yet."
        store = self.conversations
        cid = m.conversation
        async with self._locks.setdefault(cid, asyncio.Lock()):
            now = self.clock()
            if not await store.exists(cid, self.owner):
                await store.create(cid, self.owner, now)
            past = [msg for _, msg in await store.history(cid, self.owner)][-HISTORY:]
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
            usage = Usage()
            try:
                async for ev in provider.stream(
                    system=system, messages=[*_tidy(past), said], tools=[], max_tokens=MAX_TOKENS
                ):
                    if isinstance(ev, TextDelta):
                        text.append(ev.text)
                    elif isinstance(ev, Done):
                        usage = usage + ev.usage
            except Exception as e:
                logger.warning("channel reply failed: %s", type(e).__name__)
                return "Sorry, I can't answer right now."
            reply = "".join(text).strip()[:MAX_OUT] or "…"
            await store.append(cid, self.owner, "user", said, now)
            await store.append(
                cid,
                self.owner,
                "assistant",
                Msg(role="assistant", blocks=(TextBlock(text=reply),)),
                self.clock(),
            )
            await store.record_usage(
                owner_id=self.owner,
                conversation_id=cid,
                provider=provider.name,
                model=provider.model,
                usage=usage,
                cost_usd=(p.cost(usage) if (p := provider.pricing()) is not None else None),
                now_ms=self.clock(),
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
