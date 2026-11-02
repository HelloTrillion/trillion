"""Signing in to Google, keeping the sign-in fresh, and calling Google's APIs.

Sign-in is OAuth 2.0 for installed apps: authorization code with PKCE (S256), a
random state, and a loopback redirect to Trillion's own address. Access tokens last
an hour and are refreshed from the refresh token, which lives in the keychain with
the scopes it was granted. Nothing here logs a token, a code or a message.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from trillion.connectors.google.config import (
    IDENTITY,
    SCOPES,
    Client,
    GoogleError,
    Service,
    account_key,
)
from trillion.secrets.store import SecretStore

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


@dataclass(frozen=True, kw_only=True)
class Endpoints:
    auth: str = "https://accounts.google.com/o/oauth2/v2/auth"
    token: str = "https://oauth2.googleapis.com/token"  # noqa: S105 - an address
    userinfo: str = "https://openidconnect.googleapis.com/v1/userinfo"
    revoke: str = "https://oauth2.googleapis.com/revoke"
    api: str = "https://www.googleapis.com"


class GoogleCallError(Exception):
    """A call to Google did not work. `kind` and the message are safe to show."""

    def __init__(self, kind: str, message: str, *, answered: bool = True) -> None:
        super().__init__(message)
        self.kind = kind
        self.answered = answered


@dataclass(kw_only=True)
class Started:
    url: str
    state: str
    verifier: str
    service: Service
    redirect: str


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


@dataclass
class Google:
    """One client for every connected account."""

    secrets: SecretStore
    endpoints: Endpoints = field(default_factory=Endpoints)
    http: httpx.AsyncClient | None = None
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def _http(self) -> httpx.AsyncClient:
        if self.http is None:
            self.http = httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False)
        return self.http

    async def close(self) -> None:
        if self.http is not None:
            await self.http.aclose()
            self.http = None

    # ── Signing in ────────────────────────────────────────────────────────

    def start(self, client: Client, service: Service, redirect: str, hint: str = "") -> Started:
        """The address of Google's sign-in page for one service."""
        verifier, challenge = _pkce()
        state = secrets.token_urlsafe(32)
        params = {
            "client_id": client.id,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": " ".join((*IDENTITY, *SCOPES[service])),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            # A refresh token every time, and the account's other services kept.
            "access_type": "offline",
            "prompt": "consent select_account",
            "include_granted_scopes": "true",
        }
        if hint:
            params["login_hint"] = hint
        return Started(
            url=f"{self.endpoints.auth}?{urlencode(params)}",
            state=state,
            verifier=verifier,
            service=service,
            redirect=redirect,
        )

    async def finish(self, client: Client, started: Started, code: str) -> str:
        """Swap the code for tokens, learn which account it is, and keep the sign-in.
        Returns the account's email address."""
        r = await self._http().post(
            self.endpoints.token,
            data={
                "client_id": client.id,
                "client_secret": client.secret,
                "code": code,
                "code_verifier": started.verifier,
                "grant_type": "authorization_code",
                "redirect_uri": started.redirect,
            },
        )
        if r.status_code != 200:
            raise GoogleError("Google did not accept the sign-in; try again")
        tokens = r.json()
        granted = set(str(tokens.get("scope", "")).split())
        missing = [s for s in SCOPES[started.service] if s not in granted]
        if missing:
            raise GoogleError(
                "not every box was ticked on Google's page, so it cannot do its job;"
                " sign in again and allow all of them"
            )
        who = await self._http().get(
            self.endpoints.userinfo,
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        email = str(who.json().get("email", "")) if who.status_code == 200 else ""
        if not email:
            raise GoogleError("Google did not say which account signed in")
        old = self._read(email)
        self._write(
            email,
            {
                "refresh_token": tokens.get("refresh_token") or old.get("refresh_token"),
                "access_token": tokens["access_token"],
                "expires_at": time.time() + float(tokens.get("expires_in", 3600)),
                "scopes": sorted(granted | set(old.get("scopes", []))),
            },
        )
        return email

    async def forget(self, email: str) -> None:
        """Sign out: revoke the sign-in with Google and remove it from the keychain."""
        token = self._read(email).get("refresh_token")
        if token:
            try:
                await self._http().post(self.endpoints.revoke, data={"token": token})
            except httpx.HTTPError:
                logger.warning("Google sign-out could not reach Google; removed here anyway")
        self.secrets.delete(account_key(email))

    def scopes(self, email: str) -> set[str]:
        return set(self._read(email).get("scopes", []))

    def signed_in(self, email: str) -> bool:
        return bool(self._read(email).get("refresh_token"))

    # ── Tokens ────────────────────────────────────────────────────────────

    def _read(self, email: str) -> dict[str, Any]:
        raw = self.secrets.get(account_key(email))
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, email: str, data: Mapping[str, Any]) -> None:
        self.secrets.set(account_key(email), json.dumps(dict(data)))

    async def _token(self, client: Client, email: str, *, fresh: bool = False) -> str:
        lock = self._locks.setdefault(email.lower(), asyncio.Lock())
        async with lock:
            data = self._read(email)
            if not data.get("refresh_token"):
                raise GoogleCallError("signed_out", f"{email} is not signed in; connect it again")
            if (
                not fresh
                and data.get("access_token")
                and data.get("expires_at", 0) > time.time() + 60
            ):
                return str(data["access_token"])
            try:
                r = await self._http().post(
                    self.endpoints.token,
                    data={
                        "client_id": client.id,
                        "client_secret": client.secret,
                        "refresh_token": data["refresh_token"],
                        "grant_type": "refresh_token",
                    },
                )
            except httpx.HTTPError:
                raise GoogleCallError(
                    "unreachable", "Google could not be reached", answered=False
                ) from None
            if r.status_code != 200:
                raise GoogleCallError(
                    "signed_out", f"Google ended the sign-in for {email}; connect it again"
                )
            tokens = r.json()
            data["access_token"] = tokens["access_token"]
            data["expires_at"] = time.time() + float(tokens.get("expires_in", 3600))
            self._write(email, data)
            return str(tokens["access_token"])

    async def access_token(self, client: Client, email: str) -> str:
        """A current access token for `email`, for Google's file picker page."""
        return await self._token(client, email)

    # ── Calling ───────────────────────────────────────────────────────────

    async def call(
        self,
        client: Client,
        email: str,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Any = None,
        raw: bool = False,
    ) -> Any:
        """One API call as `email`. Returns the JSON reply (or text, with `raw`)."""
        url = f"{self.endpoints.api}{path}"
        for attempt in (1, 2):
            token = await self._token(client, email, fresh=attempt == 2)
            try:
                r = await self._http().request(
                    method,
                    url,
                    params=params,
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.TimeoutException:
                raise GoogleCallError(
                    "unreachable", "Google did not answer in time", answered=False
                ) from None
            except httpx.HTTPError:
                raise GoogleCallError(
                    "unreachable", "Google could not be reached", answered=False
                ) from None
            if r.status_code == 401 and attempt == 1:
                continue  # the token went stale early: refresh once
            if r.status_code in (200, 201, 204):
                if raw:
                    return r.text
                return r.json() if r.content else {}
            raise _failure(r)
        raise GoogleCallError(
            "signed_out", f"Google ended the sign-in for {email}; connect it again"
        )


def _failure(r: httpx.Response) -> GoogleCallError:
    reason = ""
    try:
        err = r.json().get("error", {})
        reason = (err.get("errors") or [{}])[0].get("reason", "") if isinstance(err, dict) else ""
    except ValueError:
        pass
    if r.status_code == 404:
        return GoogleCallError("not_found", "Google found nothing with that id")
    if r.status_code == 410:
        return GoogleCallError("gone", "Google says that was deleted")
    if r.status_code == 403 and reason in (
        "insufficientPermissions",
        "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
    ):
        return GoogleCallError(
            "not_allowed", "Trillion was not given that access; connect it again"
        )
    if r.status_code == 403 and reason in ("accessNotConfigured", "SERVICE_DISABLED"):
        return GoogleCallError(
            "api_off", "that Google API is switched off in your Google Cloud project"
        )
    if r.status_code == 429 or reason in ("rateLimitExceeded", "userRateLimitExceeded"):
        return GoogleCallError("busy", "Google says too many requests; try again in a minute")
    if r.status_code >= 500:
        # Google's own trouble: a write may still have gone through.
        return GoogleCallError("server", f"Google had a problem on its side ({r.status_code})")
    if r.status_code == 400:
        return GoogleCallError("refused", "Google refused that request as it was asked")
    return GoogleCallError("failed", f"Google answered with an error ({r.status_code})")
