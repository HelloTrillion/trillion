"""Server-sent events over an httpx response, shared by both provider adapters."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

from trillion.brain.types import ProviderError

# Connecting is quick or it is broken; a model may think for a while between tokens.
TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)

_STATUS_KINDS = {
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    408: "timeout",
    413: "too_large",
    429: "rate_limited",
}


def status_kind(status: int) -> str:
    if status in _STATUS_KINDS:
        return _STATUS_KINDS[status]
    return "provider_unavailable" if status >= 500 else "bad_request"


async def events(
    client: httpx.AsyncClient, url: str, *, headers: Mapping[str, str], body: Mapping[str, Any]
) -> AsyncIterator[tuple[str, str]]:
    """Yield `(event, data)` pairs. Raises ProviderError with a kind and no body text:
    an error body can echo the request, and the request holds the conversation."""
    try:
        async with client.stream("POST", url, headers=dict(headers), json=dict(body)) as r:
            if r.status_code != 200:
                await r.aread()
                raise ProviderError(status_kind(r.status_code), f"HTTP {r.status_code}")
            event = ""
            data: list[str] = []
            async for line in r.aiter_lines():
                if not line:
                    if data:
                        yield event, "\n".join(data)
                    event = ""
                    data = []
                elif line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].removeprefix(" "))
            if data:
                yield event, "\n".join(data)
    except httpx.TimeoutException as e:
        raise ProviderError("timeout", type(e).__name__) from e
    except httpx.HTTPError as e:
        raise ProviderError("unreachable", type(e).__name__) from e
    except httpx.InvalidURL as e:
        raise ProviderError("bad_url", type(e).__name__) from e


def parse_object(raw: str) -> dict[str, Any] | None:
    """A JSON object, or None. Providers stream tool arguments as text; they can be bad."""
    try:
        value = json.loads(raw) if raw.strip() else {}
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None
