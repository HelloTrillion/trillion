"""Signing in to a remote MCP server: OAuth with PKCE, through the user's browser.

The MCP library does the protocol: it finds the server's sign-in service, registers
Trillion with it, builds the sign-in address and swaps the code for a token. What is
here is what belongs to Trillion:

- Where the sign-in is kept: the system keychain, one entry per server, never a
  file and never the log.
- Where the browser comes back to: `/mcp/callback` on Trillion's own address. That
  page needs no session token, because the service's site sends the browser there,
  so it does one thing only: it hands a code to the sign-in that is waiting for it.
  Every sign-in has its own random `state`; a code with a state nobody is waiting for
  is turned away, and the library checks the state again before using the code.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

from trillion.secrets.store import SecretStore

SIGN_IN_TIMEOUT_S = 600.0  # time for a person to finish signing in


def sign_in_key(server: str) -> str:
    return f"mcp/{server}/sign-in"


class SignInFailed(RuntimeError):
    """The sign-in was refused, abandoned or not finished in time. Safe to show."""


class KeychainSignIn:
    """The library's token storage, kept in the keychain as one JSON entry."""

    def __init__(self, secrets: SecretStore, server: str, redirect: str) -> None:
        self._secrets = secrets
        self._key = sign_in_key(server)
        self._redirect = redirect

    def _read(self) -> dict[str, Any]:
        raw = self._secrets.get(self._key)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        self._secrets.set(self._key, json.dumps(data))

    def expires_at(self) -> float | None:
        value = self._read().get("expires_at")
        return float(value) if isinstance(value, int | float) else None

    async def get_tokens(self) -> Any:
        from mcp.shared.auth import OAuthToken

        tokens = self._read().get("tokens")
        return OAuthToken.model_validate(tokens) if tokens else None

    async def set_tokens(self, tokens: Any) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        expires_in = getattr(tokens, "expires_in", None)
        data["expires_at"] = time.time() + expires_in if expires_in else None
        self._write(data)

    async def get_client_info(self) -> Any:
        from mcp.shared.auth import OAuthClientInformationFull

        client = self._read().get("client")
        if not client:
            return None
        info = OAuthClientInformationFull.model_validate(client)
        # Registered for another address (Trillion now runs on another port): register again.
        if [str(u) for u in info.redirect_uris or []] != [self._redirect]:
            return None
        return info

    async def set_client_info(self, client_info: Any) -> None:
        data = self._read()
        data["client"] = client_info.model_dump(mode="json", exclude_none=True)
        self._write(data)


@dataclass
class _Waiting:
    server: str
    future: asyncio.Future[Any]


@dataclass
class SignIns:
    """Sign-ins waiting for the browser to come back, by their `state`."""

    _waiting: dict[str, _Waiting] = field(default_factory=dict)

    def expect(self, server: str, authorization_url: str) -> str:
        """Note a sign-in about to be opened. Returns its state."""
        state = (parse_qs(urlparse(authorization_url).query).get("state") or [""])[0]
        if not state:
            raise SignInFailed("the service's sign-in page did not carry a state")
        self.cancel(server)  # one sign-in at a time per server
        loop = asyncio.get_running_loop()
        self._waiting[state] = _Waiting(server, loop.create_future())
        return state

    async def wait(self, state: str, timeout: float = SIGN_IN_TIMEOUT_S) -> Any:
        from mcp.shared.auth import AuthorizationCodeResult

        waiting = self._waiting.get(state)
        if waiting is None:
            raise SignInFailed("the sign-in was cancelled")
        try:
            got = await asyncio.wait_for(asyncio.shield(waiting.future), timeout)
        except TimeoutError:
            raise SignInFailed("the sign-in was not finished in time") from None
        finally:
            self._waiting.pop(state, None)
        if isinstance(got, SignInFailed):
            raise got
        return AuthorizationCodeResult(code=got["code"], state=state, iss=got.get("iss"))

    def arrive(self, params: dict[str, str]) -> str | None:
        """The browser came back. Returns the server it was for, or None if nobody was
        waiting for this state (a stale tab, or a page that was not ours)."""
        state = params.get("state", "")
        waiting = self._waiting.get(state)
        if waiting is None or waiting.future.done():
            return None
        if params.get("code"):
            waiting.future.set_result({"code": params["code"], "iss": params.get("iss")})
        else:
            refused = params.get("error") == "access_denied"
            waiting.future.set_result(
                SignInFailed("you did not allow it" if refused else "the service refused")
            )
        return waiting.server

    def cancel(self, server: str) -> None:
        for state, waiting in list(self._waiting.items()):
            if waiting.server == server:
                if not waiting.future.done():
                    waiting.future.set_result(SignInFailed("the sign-in was cancelled"))
                self._waiting.pop(state, None)


def provider(
    *,
    server: str,
    url: str,
    secrets: SecretStore,
    redirect: str,
    on_open: Any,
    sign_ins: SignIns,
) -> Any:
    """The library's OAuth provider for one server, kept in the keychain.

    `on_open(url)` is called with the sign-in address when a person has to sign in:
    Settings shows it as a button. Nothing opens a browser on its own."""
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    storage = KeychainSignIn(secrets, server, redirect)
    pending: dict[str, str] = {}

    async def redirect_handler(authorization_url: str) -> None:
        pending["state"] = sign_ins.expect(server, authorization_url)
        on_open(authorization_url)

    async def callback_handler() -> Any:
        return await sign_ins.wait(pending.get("state", ""))

    class _Provider(OAuthClientProvider):
        async def _initialize(self) -> None:
            await super()._initialize()
            # The library keeps a token's expiry in memory only; after a restart it
            # would use an expired token and ask the person to sign in again, when a
            # refresh would do. The keychain entry remembers when it expires.
            self.context.token_expiry_time = storage.expires_at()

    return _Provider(
        server_url=url,
        client_metadata=OAuthClientMetadata(
            client_name="Trillion",
            client_uri=None,
            redirect_uris=[redirect],  # type: ignore[list-item]
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",  # noqa: S106 - a public client, not a password
        ),
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
