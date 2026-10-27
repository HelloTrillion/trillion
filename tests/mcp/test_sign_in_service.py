"""A sign-in is renewed at the service's own token address after a restart.

The library learns where a service takes its tokens while the person signs in, and
keeps it in memory only. Started again, it guessed "/token" on the server's own
address; a service that keeps it elsewhere answered 404, the sign-in was dropped, and
the person had to sign in again every time Trillion started. The service's address is
kept beside the sign-in now, and put back on start."""

# Every token and address here is a made-up test value, never a credential.
# ruff: noqa: S105, S106

from __future__ import annotations

import json
import time

import httpx2
import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthMetadata, OAuthToken

from trillion.connectors.mcp.signin import SignIns, provider, sign_in_key
from trillion.secrets.store import MemoryStore

SERVER = "https://mcp.example.com/mcp"
REDIRECT = "http://127.0.0.1:7790/mcp/callback"
METADATA = OAuthMetadata(
    issuer="https://auth.example.com/",  # type: ignore[arg-type]
    authorization_endpoint="https://auth.example.com/oauth/authorize",  # type: ignore[arg-type]
    token_endpoint="https://auth.example.com/oauth/token",  # type: ignore[arg-type]
)


def make(secrets: MemoryStore):  # type: ignore[no-untyped-def]
    return provider(
        server="stripe",
        url=SERVER,
        secrets=secrets,
        redirect=REDIRECT,
        on_open=lambda url: None,
        sign_ins=SignIns(),
    )


def signed_in(secrets: MemoryStore, *, service: bool, expired: bool) -> None:
    entry = {
        "tokens": {"access_token": "a1", "token_type": "Bearer", "refresh_token": "r1"},
        "expires_at": time.time() + (-60 if expired else 3600),
        "client": OAuthClientInformationFull(
            client_id="c1",
            redirect_uris=[REDIRECT],  # type: ignore[list-item]
            token_endpoint_auth_method="none",
        ).model_dump(mode="json", exclude_none=True),
    }
    if service:
        entry["service"] = {
            "metadata": METADATA.model_dump(mode="json", exclude_none=True),
            "auth_server_url": "https://auth.example.com/",
        }
    secrets.set(sign_in_key("stripe"), json.dumps(entry))


async def test_a_finished_sign_in_keeps_the_services_address() -> None:
    secrets = MemoryStore()
    p = make(secrets)
    await p._initialize()
    p.context.oauth_metadata = METADATA
    p.context.auth_server_url = "https://auth.example.com/"
    p.context.client_info = OAuthClientInformationFull(
        client_id="c1",
        redirect_uris=[REDIRECT],  # type: ignore[list-item]
        token_endpoint_auth_method="none",
    )
    token = OAuthToken(access_token="a2", token_type="Bearer", refresh_token="r2", expires_in=60)
    response = httpx2.Response(200, json=token.model_dump(mode="json", exclude_none=True))
    await p._handle_token_response(response)
    saved = json.loads(secrets.get(sign_in_key("stripe")) or "{}")
    assert saved["service"]["metadata"]["token_endpoint"] == "https://auth.example.com/oauth/token"
    assert saved["service"]["auth_server_url"] == "https://auth.example.com/"


async def test_after_a_restart_an_expired_sign_in_is_renewed_at_the_services_address() -> None:
    secrets = MemoryStore()
    signed_in(secrets, service=True, expired=True)
    p = make(secrets)
    await p._initialize()
    assert p.context.oauth_metadata is not None
    assert not p.context.is_token_valid() and p.context.can_refresh_token()
    request = await p._refresh_token()
    assert str(request.url) == "https://auth.example.com/oauth/token"


async def test_a_sign_in_kept_before_this_is_not_renewed_at_a_guessed_address() -> None:
    # Without the service's address, a renewal would go to a guessed "/token" and, when
    # that is wrong, drop the sign-in. Its expiry is left unknown instead, so the token
    # is tried as it is and the server says if it needs a new sign-in.
    secrets = MemoryStore()
    signed_in(secrets, service=False, expired=True)
    p = make(secrets)
    await p._initialize()
    assert p.context.oauth_metadata is None
    assert p.context.token_expiry_time is None


@pytest.mark.parametrize("bad", ["not json", {"metadata": {"issuer": 3}}, {"metadata": None}])
async def test_a_damaged_service_entry_is_ignored(bad: object) -> None:
    secrets = MemoryStore()
    signed_in(secrets, service=False, expired=True)
    entry = json.loads(secrets.get(sign_in_key("stripe")) or "{}")
    entry["service"] = bad
    secrets.set(sign_in_key("stripe"), json.dumps(entry))
    p = make(secrets)
    await p._initialize()
    assert p.context.oauth_metadata is None and p.context.token_expiry_time is None
