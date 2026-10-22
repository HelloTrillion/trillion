"""What every channel adapter gives the rest of Trillion."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Literal, Protocol

Platform = Literal["slack", "telegram"]
Status = Literal["off", "connecting", "connected", "failed"]
MAX_IN = 4000  # characters of one message she reads
MAX_OUT = 3500  # characters of one reply (both platforms take more; chat should be short)


@dataclass(frozen=True, kw_only=True)
class Inbound:
    platform: Platform
    sender: str  # the platform's id for the person
    name: str  # what the platform calls them; a name, never an identity
    chat: str  # where to answer
    thread: str = ""  # Slack: the thread to answer in
    reply_to: str = ""  # Telegram: the message to answer
    text: str
    direct: bool  # a one-to-one conversation with her
    mentioned: bool  # she was @mentioned (always true in a direct conversation)

    @property
    def conversation(self) -> str:
        """One remembered conversation per chat (and per Slack thread)."""
        return f"channel-{self.platform}-{self.chat}" + (f"-{self.thread}" if self.thread else "")


OnMessage = Callable[[Inbound], Coroutine[Any, Any, None]]


class ChannelAdapter(Protocol):
    platform: Platform
    status: Status
    problem: str
    # The bot's own name on the platform, once connected.
    handle: str

    async def run(self) -> None:
        """Stay connected until cancelled, calling the handler for each message."""
        ...

    async def reply(self, to: Inbound, text: str) -> None: ...
