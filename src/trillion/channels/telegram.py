"""Talking to her in Telegram, through a bot, by long polling (SUR-5).

Trillion asks Telegram for new messages over an outbound connection; nothing on
this computer is reachable from the internet. She hears private messages to the
bot, and in a group only a message that @mentions the bot or answers one of hers.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import httpx

from trillion.channels.base import Inbound, OnMessage, Platform, Status

logger = logging.getLogger(__name__)

API = "https://api.telegram.org"
POLL_S = 30
BACKOFF = (1, 2, 5, 10, 30, 60)


class TelegramError(Exception):
    """Telegram said no. The message is safe to show; it never holds the token."""


async def call(http: httpx.AsyncClient, base: str, token: str, method: str, **params: Any) -> Any:
    try:
        r = await http.post(f"{base}/bot{token}/{method}", json=params, timeout=POLL_S + 15)
    except httpx.HTTPError:
        raise TelegramError("Telegram could not be reached") from None
    try:
        data = r.json()
    except ValueError:
        raise TelegramError(f"Telegram answered with an error ({r.status_code})") from None
    if not data.get("ok"):
        if r.status_code == 401:
            raise TelegramError("Telegram did not accept the bot token; paste it again")
        if r.status_code == 409:
            raise TelegramError("another program is already reading this bot's messages")
        raise TelegramError(f"Telegram refused that ({r.status_code})")
    return data.get("result")


class TelegramChannel:
    platform: Platform = "telegram"

    def __init__(
        self, token: str, on_message: OnMessage, http: httpx.AsyncClient, base: str = API
    ) -> None:
        self._token = token
        self._on = on_message
        self._http = http
        self._base = base
        self.status: Status = "connecting"
        self.problem = ""
        self.handle = ""
        self._me = 0
        self._tasks: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        offset = 0
        tries = 0
        while True:
            try:
                if not self._me:
                    me = await call(self._http, self._base, self._token, "getMe")
                    self._me, self.handle = int(me["id"]), str(me.get("username", ""))
                updates = await call(
                    self._http, self._base, self._token, "getUpdates",
                    offset=offset, timeout=POLL_S, allowed_updates=["message"],
                )  # fmt: skip
                self.status, self.problem, tries = "connected", "", 0
                for u in updates or []:
                    offset = max(offset, int(u.get("update_id", 0)) + 1)
                    m = self.inbound(u.get("message") or {})
                    if m is not None:
                        task = asyncio.create_task(self._on(m))
                        self._tasks.add(task)
                        task.add_done_callback(self._tasks.discard)
                continue
            except asyncio.CancelledError:
                raise
            except TelegramError as e:
                self.status, self.problem = "failed", str(e)
                if "token" in str(e):
                    return  # it will not work until the user fixes it
            except Exception as e:
                self.status, self.problem = "connecting", "the connection to Telegram dropped"
                logger.warning("Telegram channel dropped: %s", type(e).__name__)
            await asyncio.sleep(BACKOFF[min(tries, len(BACKOFF) - 1)])
            tries += 1

    def inbound(self, msg: dict[str, Any]) -> Inbound | None:
        sender = msg.get("from") or {}
        text = str(msg.get("text") or "")
        if not text or sender.get("is_bot") or not sender.get("id"):
            return None
        chat = msg.get("chat") or {}
        direct = chat.get("type") == "private"
        tag = f"@{self.handle}" if self.handle else ""
        replied = ((msg.get("reply_to_message") or {}).get("from") or {}).get("id") == self._me
        mentioned = direct or replied or (bool(tag) and tag.lower() in text.lower())
        if tag:
            text = text.replace(tag, "").strip()
        name = " ".join(p for p in (sender.get("first_name"), sender.get("last_name")) if p) or str(
            sender.get("username") or "someone"
        )
        return Inbound(
            platform="telegram",
            sender=str(sender["id"]),
            name=name,
            chat=str(chat.get("id", "")),
            reply_to="" if direct else str(msg.get("message_id", "")),
            text=text,
            direct=direct,
            mentioned=mentioned,
        )

    async def reply(self, to: Inbound, text: str) -> None:
        params: dict[str, Any] = {"chat_id": to.chat, "text": text}
        if to.reply_to:
            params["reply_parameters"] = {"message_id": int(to.reply_to)}
        with contextlib.suppress(TelegramError):
            await call(self._http, self._base, self._token, "sendMessage", **params)
