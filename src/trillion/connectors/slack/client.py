"""Calling Slack's Web API with one of the user's tokens. Nothing here logs a token
or a message."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

API = "https://slack.com/api"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class SlackError(Exception):
    """Slack said no, or could not be reached. `kind` and the message are safe to show."""

    def __init__(self, kind: str, message: str, *, answered: bool = True) -> None:
        super().__init__(message)
        self.kind = kind
        self.answered = answered


_WORDS = {
    "invalid_auth": "Slack did not accept the token; paste it again",
    "not_authed": "Slack did not accept the token; paste it again",
    "token_revoked": "that token was revoked in Slack; paste a new one",
    "account_inactive": "that Slack account is no longer active",
    "missing_scope": "the Slack app is missing a permission; make it again from Trillion's link",
    "channel_not_found": "Slack found no such conversation",
    "not_in_channel": "you are not in that conversation",
    "is_archived": "that conversation is archived",
    "ratelimited": "Slack says too many requests; try again in a minute",
    "msg_too_long": "that message is too long for Slack",
}


# Slack's own trouble, not a refusal: a post may still have gone through.
SERVER_SIDE = frozenset(
    {"server", "internal_error", "fatal_error", "service_unavailable", "request_timeout"}
)


@dataclass
class Slack:
    base: str = API
    http: httpx.AsyncClient | None = None
    _names: dict[str, str] = field(default_factory=dict)

    def _http(self) -> httpx.AsyncClient:
        if self.http is None:
            self.http = httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False)
        return self.http

    async def close(self) -> None:
        if self.http is not None:
            await self.http.aclose()
            self.http = None

    async def call(
        self,
        token: str,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        post: bool = False,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"}
        try:
            if post:
                r = await self._http().post(
                    f"{self.base}/{method}", json=dict(params or {}), headers=headers
                )
            else:
                r = await self._http().get(
                    f"{self.base}/{method}", params=dict(params or {}), headers=headers
                )
        except httpx.TimeoutException:
            raise SlackError(
                "unreachable", "Slack did not answer in time", answered=False
            ) from None
        except httpx.HTTPError:
            raise SlackError("unreachable", "Slack could not be reached", answered=False) from None
        if r.status_code == 429:
            raise SlackError("busy", _WORDS["ratelimited"])
        if r.status_code >= 500:
            raise SlackError("server", f"Slack had a problem on its side ({r.status_code})")
        try:
            data = r.json()
        except ValueError:
            raise SlackError("failed", f"Slack answered with an error ({r.status_code})") from None
        if not data.get("ok"):
            code = str(data.get("error", "failed"))
            raise SlackError(code, _WORDS.get(code, f"Slack refused that ({code})"))
        return dict(data)

    async def name_of(self, token: str, user: str) -> str:
        """A person's display name, remembered while Trillion runs."""
        if not user:
            return "someone"
        if user not in self._names:
            try:
                info = (await self.call(token, "users.info", {"user": user})).get("user") or {}
                profile = info.get("profile") or {}
                self._names[user] = (
                    profile.get("display_name") or info.get("real_name") or info.get("name") or user
                )
            except SlackError:
                return user
        return self._names[user]
