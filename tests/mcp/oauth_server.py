"""A small MCP server that needs signing in, for the tests: its own OAuth service on
the same address, which lets a new app register and approves every sign-in at once,
as a person clicking Allow would. Tokens last `TTL` seconds, so a refresh can be
tested.

python -m tests.mcp.oauth_server PORT [TTL]
"""

from __future__ import annotations

import secrets
import sys
import time

from mcp.server import MCPServer
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from mcp.types import ToolAnnotations

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
TTL = int(sys.argv[2]) if len(sys.argv) > 2 else 3600
BASE = f"http://127.0.0.1:{PORT}"


class Approving:
    def __init__(self) -> None:
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.access: dict[str, AccessToken] = {}
        self.refresh: dict[str, RefreshToken] = {}

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        assert client_info.client_id
        self.clients[client_info.client_id] = client_info

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        code = secrets.token_urlsafe(16)
        self.codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + 300,
            client_id=client.client_id or "",
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return self.codes.get(authorization_code)

    def _tokens(self, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:
        access, refresh = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
        self.access[access] = AccessToken(
            token=access,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(time.time()) + TTL,
            resource=resource,
        )
        self.refresh[refresh] = RefreshToken(
            token=refresh, client_id=client_id, scopes=scopes, resource=resource
        )
        return OAuthToken(access_token=access, expires_in=TTL, refresh_token=refresh)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        self.codes.pop(authorization_code.code, None)
        return self._tokens(
            client.client_id or "", authorization_code.scopes, authorization_code.resource
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        return self.refresh.get(refresh_token)

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        self.refresh.pop(refresh_token.token, None)
        return self._tokens(client.client_id or "", refresh_token.scopes, refresh_token.resource)

    async def load_access_token(self, token: str) -> AccessToken | None:
        found = self.access.get(token)
        if found is None or (found.expires_at and found.expires_at < time.time()):
            return None
        return found

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self.access.pop(token.token, None)
        self.refresh.pop(token.token, None)


app = MCPServer(
    "signs-in",
    auth_server_provider=Approving(),
    auth=AuthSettings(
        issuer_url=BASE,  # type: ignore[arg-type]
        resource_server_url=f"{BASE}/mcp",  # type: ignore[arg-type]
        client_registration_options=ClientRegistrationOptions(enabled=True),
        validate_token_resource=False,
    ),
)


@app.tool(annotations=ToolAnnotations(read_only_hint=True))
def whoami() -> str:
    """Say who is signed in."""
    return "signed in"


if __name__ == "__main__":
    app.run("streamable-http", host="127.0.0.1", port=PORT)
