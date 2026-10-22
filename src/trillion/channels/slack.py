"""Talking to her in Slack, over Socket Mode (SUR-5).

Slack hands events to an outbound WebSocket the app opens with its app-level token,
so nothing on this computer is reachable from the internet. She hears direct
messages to the app and messages that @mention it, and answers in the thread.
Every event is acknowledged at once, before it is answered, as Slack requires.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Callable
from typing import Any

from trillion.channels.base import Inbound, OnMessage, Platform, Status
from trillion.connectors.slack.client import Slack, SlackError

logger = logging.getLogger(__name__)

_MENTION = re.compile(r"<@[A-Z0-9]+>")
BACKOFF = (1, 2, 5, 10, 30, 60)


def _connect(url: str) -> Any:
    from websockets.asyncio.client import connect

    return connect(url, max_size=2**20, open_timeout=20)


class SlackChannel:
    platform: Platform = "slack"

    def __init__(
        self,
        app_token: str,
        bot_token: str,
        on_message: OnMessage,
        slack: Slack,
        connect: Callable[[str], Any] = _connect,
    ) -> None:
        self._app = app_token
        self._bot = bot_token
        self._on = on_message
        self._slack = slack
        self._connect = connect
        self.status: Status = "connecting"
        self.problem = ""
        self.handle = ""
        self._bot_user = ""
        self._tasks: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        tries = 0
        while True:
            try:
                who = await self._slack.call(self._bot, "auth.test")
                self._bot_user, self.handle = str(who.get("user_id", "")), str(who.get("user", ""))
                opened = await self._slack.call(self._app, "apps.connections.open", post=True)
                async with self._connect(str(opened["url"])) as ws:
                    async for raw in ws:
                        tries = 0
                        if await self._envelope(ws, raw) == "disconnect":
                            break
            except asyncio.CancelledError:
                raise
            except SlackError as e:
                self.status, self.problem = "failed", str(e)
                if e.kind in ("invalid_auth", "not_authed", "token_revoked", "missing_scope"):
                    return  # it will not work until the user fixes it
            except Exception as e:
                self.status, self.problem = "connecting", "the connection to Slack dropped"
                logger.warning("Slack channel dropped: %s", type(e).__name__)
            await asyncio.sleep(BACKOFF[min(tries, len(BACKOFF) - 1)])
            tries += 1

    async def _envelope(self, ws: Any, raw: Any) -> str:
        try:
            env = json.loads(raw)
        except ValueError:
            return ""
        if env.get("envelope_id"):
            await ws.send(json.dumps({"envelope_id": env["envelope_id"]}))
        kind = env.get("type")
        if kind == "hello":
            self.status, self.problem = "connected", ""
        elif kind == "disconnect":
            return "disconnect"
        elif kind == "events_api":
            m = await self.inbound((env.get("payload") or {}).get("event") or {})
            if m is not None:
                task = asyncio.create_task(self._on(m))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        return ""

    async def inbound(self, e: dict[str, Any]) -> Inbound | None:
        """The message in a Slack event, if it is one she should hear."""
        if (
            e.get("bot_id")
            or e.get("subtype")
            or not e.get("user")
            or e.get("user") == self._bot_user
        ):
            return None
        kind = e.get("type")
        direct = kind == "message" and e.get("channel_type") == "im"
        if kind != "app_mention" and not direct:
            return None
        text = _MENTION.sub("", str(e.get("text", ""))).strip()
        return Inbound(
            platform="slack",
            sender=str(e["user"]),
            name=await self._slack.name_of(self._bot, str(e["user"])),
            chat=str(e.get("channel", "")),
            thread="" if direct else str(e.get("thread_ts") or e.get("ts") or ""),
            text=text,
            direct=direct,
            mentioned=True,
        )

    async def reply(self, to: Inbound, text: str) -> None:
        body: dict[str, Any] = {"channel": to.chat, "text": text}
        if to.thread:
            body["thread_ts"] = to.thread
        with contextlib.suppress(SlackError):
            await self._slack.call(self._bot, "chat.postMessage", body, post=True)
