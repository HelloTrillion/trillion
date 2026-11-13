"""What a browsing task did, for the user to read (the her browser spec, decision 13).

Origins and actions only. A row takes a URL and keeps its origin (scheme and host):
never a path, a query or anything from the page. Its detail is one of the short words
below, made by the host, never anything the page or the model wrote. The one
exception the spec asks for is the search query (decision 9), cut to fit.

Writes go through a `Sink`: the database in the app, a list in tests. A failure to
write is logged by its kind and never stops the task.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

Event = Literal[
    "started",
    "opened",
    "searched",
    "followed",
    "back",
    "typed",
    "pressed",
    "sent",
    "refused",
    "chip",
    "card",
    "step",
    "control",
    "download",
    "signin",
    "done",
    "stopped",
]

# The only words a row's detail may hold, besides a search query.
DETAILS = frozenset(
    {
        # refusals, by kind
        "sends",
        "submits",
        "private_address",
        "unreachable",
        "bad_port",
        "bad_address",
        "off_site",
        "sensitive_field",
        "payment",
        "upload",
        "websocket",
        "tls",
        "not_act",
        "not_given",
        "search_refused",
        # chips, cards and steps, and their answers
        "asked",
        "allowed",
        "declined",
        "approved",
        "expired",
        "unattended",
        "taken",
        "returned",
        "sign_in",
        "captcha",
        "passkey",
        "other",
        # how a task ended
        "answered",
        "steps",
        "time",
        "failed",
        "search",
        "cancelled",
        "retry",
        "stronger",
        # a download
        "quarantined",
        "too_big",
    }
)
QUERY_MAX = 200


class Sink(Protocol):
    async def append(
        self,
        owner_id: str,
        task_id: str,
        at: int,
        event: str,
        origin: str | None,
        detail: str | None,
    ) -> None: ...


def origin_of(url: str | None) -> str | None:
    """`https://example.com` for any URL there; None for anything that isn't one."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".")
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    try:
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    default = {"http": 80, "https": 443}[parts.scheme]
    suffix = f":{port}" if port not in (None, default) else ""
    netloc = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{netloc}{suffix}"


class TaskLog:
    def __init__(
        self,
        sink: Sink | None,
        owner_id: str,
        task_id: str,
        clock: Callable[[], int],
        *,
        told: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._sink = sink
        self._owner = owner_id
        self.task_id = task_id
        self._clock = clock
        self._told = told

    async def event(self, event: Event, *, url: str | None = None, code: str | None = None) -> None:
        if code is not None and code not in DETAILS:
            raise ValueError(f"not a log detail: {code}")
        await self._write(event, origin_of(url), code)

    async def searched(self, url: str, query: str) -> None:
        await self._write("searched", origin_of(url), " ".join(query.split())[:QUERY_MAX])

    async def _write(self, event: str, origin: str | None, detail: str | None) -> None:
        if self._sink is None:
            return
        try:
            await self._sink.append(self._owner, self.task_id, self._clock(), event, origin, detail)
        except Exception as e:
            logger.warning("browser log not written: %s", type(e).__name__)
        if self._told is not None:
            try:
                await self._told()
            except Exception as e:
                logger.warning("browser log change not told: %s", type(e).__name__)
