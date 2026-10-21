"""Connections to the user's providers, kept open between turns.

Opening a connection to a hosted service (a TCP connect and a TLS handshake) costs
a round trip or two before the first byte of a reply, every time. httpx keeps an
idle connection for only five seconds, and turns are usually further apart than
that, so each turn paid it again. A minute covers a conversation's pauses.

`warm` opens the connection ahead of need, while the user is still talking. It
sends nothing of theirs and needs no key: it asks the service's front door for its
headers, and whatever comes back is ignored.
"""

from __future__ import annotations

import contextlib
from typing import Any

import httpx

IDLE_S = 60.0
KEEP_ALIVE = httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=IDLE_S)
WARM_TIMEOUT_S = 5.0


async def warm(client: Any, url: str) -> None:
    """Open a connection to `url`'s host, if one is not open already. Never raises.

    `client` is an httpx client, or the copy of httpx a provider's SDK carries."""
    with contextlib.suppress(Exception):
        await client.head(url, timeout=WARM_TIMEOUT_S)
