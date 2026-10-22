"""The channels while Trillion runs: which are on, who may talk, and answering (SUR-5).

`channels.json` holds whether each platform is on and who is allowed, by platform
id and name. Tokens are in the keychain. Someone who writes without being allowed
gets no answer and is listed in Settings for the user to allow or ignore.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from trillion.channels import telegram
from trillion.channels.answer import Answerer
from trillion.channels.base import ChannelAdapter, Inbound, Platform
from trillion.channels.gate import Gate
from trillion.channels.slack import SlackChannel
from trillion.channels.telegram import TelegramChannel, TelegramError
from trillion.connectors.slack import APP_TOKEN, BOT_TOKEN
from trillion.connectors.slack.client import Slack, SlackError
from trillion.secrets.store import SecretStore, SecretStoreError

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = "telegram/bot"  # noqa: S105 - a keychain name
PLATFORMS: tuple[Platform, ...] = ("slack", "telegram")


class ChannelSetupError(ValueError):
    """What the user gave cannot be used. Safe to show."""


class Channels:
    def __init__(
        self,
        secrets: SecretStore,
        path: Path,
        answerer: Answerer,
        clock: Callable[[], int],
        *,
        slack: Slack | None = None,
        http: httpx.AsyncClient | None = None,
        telegram_base: str = telegram.API,
        slack_connect: Callable[[str], Any] | None = None,
    ) -> None:
        self._secrets = secrets
        self._path = path
        self._answerer = answerer
        self._clock = clock
        self._slack = slack or Slack()
        self._http = http
        self._telegram_base = telegram_base
        self._slack_connect = slack_connect
        self.gate = Gate()
        self.on: dict[Platform, bool] = {}
        self.adapters: dict[Platform, ChannelAdapter] = {}
        self._tasks: dict[Platform, asyncio.Task[None]] = {}
        self._load()

    # ── The file ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        for p in PLATFORMS:
            entry = raw.get(p) if isinstance(raw, dict) else None
            entry = entry if isinstance(entry, dict) else {}
            self.on[p] = entry.get("on") is True
            found = entry.get("allowed")
            allowed: dict[str, Any] = found if isinstance(found, dict) else {}
            self.gate.allowed[p] = {str(k): str(v)[:80] for k, v in allowed.items()}

    def _save(self) -> None:
        data = {p: {"on": self.on[p], "allowed": self.gate.allowed.get(p, {})} for p in PLATFORMS}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".channels-", suffix=".json", dir=self._path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    # ── Running ───────────────────────────────────────────────────────────

    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
        return self._http

    def configured(self, p: Platform) -> bool:
        if p == "slack":
            return bool(self._secrets.get(BOT_TOKEN) and self._secrets.get(APP_TOKEN))
        return bool(self._secrets.get(TELEGRAM_TOKEN))

    def start(self) -> None:
        for p in PLATFORMS:
            if self.on[p] and self.configured(p):
                self._launch(p)

    def _launch(self, p: Platform) -> None:
        self._stop_now(p)
        adapter: ChannelAdapter
        if p == "slack":
            kw = {"connect": self._slack_connect} if self._slack_connect else {}
            adapter = SlackChannel(
                self._secrets.get(APP_TOKEN) or "",
                self._secrets.get(BOT_TOKEN) or "",
                self.handle,
                self._slack,
                **kw,
            )
        else:
            adapter = TelegramChannel(
                self._secrets.get(TELEGRAM_TOKEN) or "",
                self.handle,
                self.http(),
                self._telegram_base,
            )
        self.adapters[p] = adapter
        self._tasks[p] = asyncio.create_task(adapter.run())

    def _stop_now(self, p: Platform) -> None:
        task = self._tasks.pop(p, None)
        if task is not None:
            task.cancel()
        self.adapters.pop(p, None)

    async def close(self) -> None:
        for p in PLATFORMS:
            self._stop_now(p)
        if self._http is not None:
            await self._http.aclose()

    async def handle(self, m: Inbound) -> None:
        """One message from a channel: answered if the gate lets it through, else not."""
        if self.gate.judge(m, self._clock()) != "answer":
            return
        adapter = self.adapters.get(m.platform)
        if adapter is None:
            return
        try:
            reply = await self._answerer.answer(m)
            await adapter.reply(m, reply)
        except Exception as e:  # one bad message never stops the channel
            logger.warning("channel message not answered: %s", type(e).__name__)

    # ── Settings ──────────────────────────────────────────────────────────

    async def set_slack(self, bot_token: str, app_token: str) -> None:
        bot_token, app_token = bot_token.strip(), app_token.strip()
        if not bot_token.startswith("xoxb-"):
            raise ChannelSetupError("the Bot User OAuth Token starts with xoxb-")
        if not app_token.startswith("xapp-"):
            raise ChannelSetupError("the app-level token starts with xapp-")
        try:
            await self._slack.call(bot_token, "auth.test")
            await self._slack.call(app_token, "apps.connections.open", post=True)
        except SlackError as e:
            raise ChannelSetupError(str(e)) from None
        self._secrets.set(BOT_TOKEN, bot_token)
        self._secrets.set(APP_TOKEN, app_token)
        self.on["slack"] = True
        self._save()
        self._launch("slack")

    async def set_telegram(self, token: str) -> None:
        token = token.strip()
        if ":" not in token or any(c.isspace() for c in token) or len(token) > 100:
            raise ChannelSetupError(
                "that is not a bot token from @BotFather (it looks like 123456:ABC…)"
            )
        try:
            await telegram.call(self.http(), self._telegram_base, token, "getMe")
        except TelegramError as e:
            raise ChannelSetupError(str(e)) from None
        self._secrets.set(TELEGRAM_TOKEN, token)
        self.on["telegram"] = True
        self._save()
        self._launch("telegram")

    def turn(self, p: Platform, on: bool) -> None:
        self.on[p] = on
        self._save()
        if on and self.configured(p):
            self._launch(p)
        else:
            self._stop_now(p)

    def allow(self, p: Platform, sender: str) -> None:
        knock = self.gate.knocks.get(p, {}).pop(sender, None)
        if knock is None:
            raise ChannelSetupError("nobody by that id has written to her")
        self.gate.allowed.setdefault(p, {})[sender] = knock.name
        self._save()

    def ignore(self, p: Platform, sender: str) -> None:
        self.gate.knocks.get(p, {}).pop(sender, None)

    def disallow(self, p: Platform, sender: str) -> None:
        self.gate.allowed.get(p, {}).pop(sender, None)
        self._save()

    def forget(self, p: Platform) -> None:
        """Disconnect: stop, and delete the tokens from the keychain."""
        self._stop_now(p)
        self.on[p] = False
        names = (BOT_TOKEN, APP_TOKEN) if p == "slack" else (TELEGRAM_TOKEN,)
        for name in names:
            with contextlib.suppress(SecretStoreError):
                self._secrets.delete(name)
        self._save()

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for p in PLATFORMS:
            a = self.adapters.get(p)
            out[p] = {
                "configured": self.configured(p),
                "on": self.on[p],
                "status": a.status if a is not None else "off",
                "problem": a.problem if a is not None else "",
                "handle": a.handle if a is not None else "",
                "allowed": [{"id": k, "name": v} for k, v in self.gate.allowed.get(p, {}).items()],
                "knocks": [
                    {"id": k, "name": v.name, "times": v.times, "at_ms": v.at_ms}
                    for k, v in self.gate.knocks.get(p, {}).items()
                ],
            }
        return out
