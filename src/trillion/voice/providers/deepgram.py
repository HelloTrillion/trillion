"""Deepgram streaming speech recognition, on the user's own account and key (VOI-11c).

Audio goes up a WebSocket as it is captured and words come back while the user
is still speaking.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Protocol
from urllib.parse import urlencode

import httpx
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

from trillion.voice.types import CAPTURE, Choice, Heard, VoiceError

NAME = "deepgram"
LISTEN_URL = "wss://api.deepgram.com/v1/listen"
PROJECTS_URL = "https://api.deepgram.com/v1/projects"
MODEL = "nova-3"
CHOICES = (
    Choice(id="en", name="English"),
    Choice(id="multi", name="Many languages, detected as you speak"),
)
_TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0)
_OPEN_TIMEOUT_S = 10.0
_KINDS = {400: "bad_request", 401: "unauthorized", 402: "payment_required", 403: "forbidden",
          429: "rate_limited"}  # fmt: skip


def _kind(status: int) -> str:
    return _KINDS.get(status, "provider_unavailable" if status >= 500 else "bad_request")


class Socket(Protocol):
    async def send(self, data: bytes | str) -> None: ...
    async def recv(self) -> bytes | str: ...
    async def close(self) -> None: ...


Connect = Callable[[str, Mapping[str, str]], Awaitable[Socket]]


async def _connect(url: str, headers: Mapping[str, str]) -> Socket:
    return await ws_connect(url, additional_headers=dict(headers), open_timeout=_OPEN_TIMEOUT_S)


def listen_url(language: str) -> str:
    query = {
        "model": MODEL,
        "language": language,
        "encoding": "linear16",
        "sample_rate": CAPTURE.sample_rate,
        "channels": CAPTURE.channels,
        "interim_results": "true",
        "smart_format": "true",
        # Deepgram's own view of a pause; the listener still decides when a turn is over.
        "endpointing": 300,
    }
    return f"{LISTEN_URL}?{urlencode(query)}"


def parse(raw: bytes | str) -> Heard | None:
    """One message from Deepgram as words, or None when it carries none."""
    try:
        msg = json.loads(raw)
    except ValueError as e:
        raise VoiceError("bad_response", type(e).__name__) from e
    if not isinstance(msg, dict) or msg.get("type") != "Results":
        return None
    alternatives = (msg.get("channel") or {}).get("alternatives") or [{}]
    text = str(alternatives[0].get("transcript") or "").strip()
    final = msg.get("is_final") is True
    return Heard(text=text, final=final) if text or final else None


class _Stream:
    def __init__(self, socket: Socket) -> None:
        self._socket = socket
        self._closed = False

    async def send(self, pcm: bytes) -> None:
        if self._closed or not pcm:
            return
        try:
            await self._socket.send(pcm)
        except ConnectionClosed as e:
            raise VoiceError("unreachable", type(e).__name__) from e

    async def finish(self) -> None:
        if self._closed:
            return
        try:
            # Deepgram answers with whatever it still holds, then closes the socket.
            await self._socket.send(json.dumps({"type": "CloseStream"}))
        except ConnectionClosed:
            pass

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await asyncio.wait_for(self._socket.close(), timeout=2.0)
        except (TimeoutError, WebSocketException, OSError):
            pass

    async def events(self) -> AsyncIterator[Heard]:
        while not self._closed:
            try:
                raw = await self._socket.recv()
            except ConnectionClosed:
                return
            heard = parse(raw)
            if heard is not None:
                yield heard


class Deepgram:
    name = NAME
    streaming = True

    def __init__(
        self,
        *,
        api_key: Callable[[], str],
        connect: Connect = _connect,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # The key is fetched for each request and never held (SEC-6).
        self._api_key = api_key
        self._connect = connect
        self._client = client

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Token {self._api_key()}"}

    async def open(self, *, choice: str) -> _Stream:
        language = choice if any(c.id == choice for c in CHOICES) else CHOICES[0].id
        try:
            socket = await self._connect(listen_url(language), self._auth())
        except InvalidStatus as e:
            raise VoiceError(_kind(e.response.status_code), "handshake refused") from e
        except TimeoutError as e:
            raise VoiceError("timeout", type(e).__name__) from e
        except (WebSocketException, OSError) as e:
            raise VoiceError("unreachable", type(e).__name__) from e
        return _Stream(socket)

    async def choices(self) -> list[Choice]:
        http = self._client or httpx.AsyncClient(timeout=_TIMEOUT)
        try:
            r = await http.get(PROJECTS_URL, headers=self._auth())
        except httpx.TimeoutException as e:
            raise VoiceError("timeout", type(e).__name__) from e
        except httpx.HTTPError as e:
            raise VoiceError("unreachable", type(e).__name__) from e
        finally:
            if self._client is None:
                await http.aclose()
        if r.status_code != 200:
            raise VoiceError(_kind(r.status_code), f"HTTP {r.status_code}")
        return list(CHOICES)

    async def prepare(self, choice: str) -> None:
        return None
