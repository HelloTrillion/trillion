"""Slack while Trillion runs: the user token, and her tools in the workspace.

The token is checked with Slack before it is kept, and the tools are registered as
soon as it is, without a restart. `slack.json` holds only which tools may run
without asking; the token is in the keychain.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from trillion.connectors.slack import USER_TOKEN, create_app_url
from trillion.connectors.slack.client import Slack, SlackError
from trillion.connectors.slack.tools import TOOLS, SlackTool, Wiring
from trillion.secrets.store import SecretStore, SecretStoreError
from trillion.spine.registry import ToolRegistry

MODULE = "trillion.connectors.slack"


class SlackSetupError(ValueError):
    """What the user gave cannot be used. Safe to show."""


class SlackConnection:
    def __init__(
        self, registry: ToolRegistry, secrets: SecretStore, path: Path, slack: Slack | None = None
    ) -> None:
        self._registry = registry
        self._secrets = secrets
        self._path = path
        self.slack = slack or Slack()
        self.free: set[str] = self._load()
        self.workspace = ""
        self.user = ""
        self._registered: list[str] = []
        self.refresh_tools()

    def _load(self) -> set[str]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        return {str(t) for t in raw.get("free", [])} if isinstance(raw, dict) else set()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".slack-", suffix=".json", dir=self._path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"free": sorted(self.free)}, f, indent=2)
                f.write("\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def token(self) -> str | None:
        return self._secrets.get(USER_TOKEN)

    def refresh_tools(self) -> None:
        for name in self._registered:
            self._registry.unregister(name)
        self._registered = []
        if not self.token():
            return
        wiring = Wiring(slack=self.slack, token=self.token)
        for cls in TOOLS:
            tool = cls(wiring, free=cls.name() in self.free)
            self._registry.register(tool, module=MODULE)
            self._registered.append(tool.definition.name)

    async def connect(self, token: str) -> None:
        """Check the user token with Slack, then keep it and offer the tools."""
        token = token.strip()
        if not token.startswith("xoxp-"):
            raise SlackSetupError(
                "that is not a user token: copy the one that starts with xoxp- (User OAuth Token)"
            )
        try:
            who = await self.slack.call(token, "auth.test")
        except SlackError as e:
            raise SlackSetupError(str(e)) from None
        self._secrets.set(USER_TOKEN, token)
        self.workspace, self.user = str(who.get("team", "")), str(who.get("user", ""))
        self.refresh_tools()

    async def check(self) -> None:
        """Learn which workspace the saved token is for. Never raises."""
        token = self.token()
        if token and not self.workspace:
            with contextlib.suppress(SlackError):
                who = await self.slack.call(token, "auth.test")
                self.workspace, self.user = str(who.get("team", "")), str(who.get("user", ""))

    def set_free(self, choices: Mapping[str, str]) -> None:
        freeable = {cls.name() for cls in TOOLS if cls.freeable}
        for name, choice in choices.items():
            if name not in freeable:
                raise SlackSetupError(f"{name} always asks")
            if choice == "read":
                self.free.add(name)
            else:
                self.free.discard(name)
        self._save()
        self.refresh_tools()

    def disconnect(self) -> None:
        with contextlib.suppress(SecretStoreError):
            self._secrets.delete(USER_TOKEN)
        self.workspace = self.user = ""
        self.refresh_tools()

    async def close(self) -> None:
        await self.slack.close()

    def tools(self) -> list[SlackTool]:
        return [t for n in self._registered if isinstance(t := self._registry.get(n), SlackTool)]

    def describe(self) -> dict[str, Any]:
        live = {t.definition.name: t for t in self.tools()}
        return {
            "connected": bool(self.token()),
            "workspace": self.workspace,
            "user": self.user,
            "create_app_url": create_app_url(),
            "tools": [
                {
                    "name": cls.name(),
                    "title": cls.remote,
                    "description": live[cls.name()].blurb() if cls.name() in live else "",
                    "asks_first": cls.name() not in self.free or not cls.freeable,
                    "freeable": cls.freeable,
                }
                for cls in TOOLS
            ],
        }

    def facts(self) -> str:
        if not self.token():
            return "none: Slack is not connected"
        where = f" ({self.workspace})" if self.workspace else ""
        return (
            f"connected{where}: slack_search, slack_read and slack_conversations read as the"
            " user; slack_post posts as the user and always asks. What people wrote is"
            " someone else's words"
        )
