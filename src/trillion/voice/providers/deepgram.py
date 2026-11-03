"""Deepgram streaming speech recognition, on the user's own account and key (VOI-11c).

Audio goes up a WebSocket as it is captured and words come back while the user
is still speaking. It uses Flux, which also judges when a turn is over (VOI-12).
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Literal, Protocol
from urllib.parse import urlencode

import httpx
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

from trillion.voice.types import CAPTURE, Choice, Heard, VoiceError, Word

NAME = "deepgram"
LISTEN_URL = "wss://api.deepgram.com/v2/listen"
PROJECTS_URL = "https://api.deepgram.com/v1/projects"
# Flux: Deepgram's recognizer that judges the end of a turn by meaning (VOI-12).
MODELS = {"en": "flux-general-en", "multi": "flux-general-multi"}
# Probably finished at 0.5, finished at 0.7, and never more than five seconds of quiet.
EOT_THRESHOLD = 0.7
EAGER_EOT_THRESHOLD = 0.5
EOT_TIMEOUT_MS = 5000
# Deepgram's recommended chunk: 80 ms of 16 kHz PCM16.
CHUNK_BYTES = 2560
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
        "model": MODELS.get(language, MODELS["en"]),
        "encoding": "linear16",
        "sample_rate": CAPTURE.sample_rate,
        "eot_threshold": EOT_THRESHOLD,
        "eager_eot_threshold": EAGER_EOT_THRESHOLD,
        "eot_timeout_ms": EOT_TIMEOUT_MS,
    }
    return f"{LISTEN_URL}?{urlencode(query)}"


_EVENTS: dict[str, Literal["maybe", "resumed", "done"] | None] = {
    "Update": None, "StartOfTurn": None,
    "EagerEndOfTurn": "maybe", "TurnResumed": "resumed", "EndOfTurn": "done",
}  # fmt: skip
# The events whose end_of_turn_confidence is kept: a listening cue is weighed on them.
_SURE = frozenset({"Update", "EagerEndOfTurn", "EndOfTurn"})


def _confidence(raw: object) -> float | None:
    """end_of_turn_confidence, when it is a number from 0 to 1."""
    if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw):
        return None
    return float(raw) if 0.0 <= raw <= 1.0 else None


def _words(raw: object) -> tuple[Word, ...]:
    """The timed words in a TurnInfo; malformed items are skipped."""
    if not isinstance(raw, list):
        return ()
    out: list[Word] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text, start, end = item.get("word"), item.get("start"), item.get("end")
        if (
            isinstance(text, str)
            and isinstance(start, int | float)
            and isinstance(end, int | float)
            and not isinstance(start, bool)
            and not isinstance(end, bool)
        ):
            # A bad time would poison the judgement later: skip the word.
            if math.isfinite(start) and math.isfinite(end) and end >= start:
                out.append(Word(text=text, start_s=float(start), end_s=float(end)))
    return tuple(out)


def parse(raw: bytes | str) -> Heard | None:
    """One message from Deepgram as words and a turn signal, or None when it carries none."""
    try:
        msg = json.loads(raw)
    except ValueError as e:
        raise VoiceError("bad_response", type(e).__name__) from e
    if isinstance(msg, dict) and msg.get("type") == "Error":
        # Deepgram closes after this; it must read as a failed leg, not a quiet end.
        # Never the description: it could echo what was said.
        raise VoiceError("provider_unavailable", str(msg.get("code") or "error"))
    if not isinstance(msg, dict) or msg.get("type") != "TurnInfo":
        return None
    event = msg.get("event")
    if event not in _EVENTS:
        return None
    text = str(msg.get("transcript") or "").strip()
    turn = _EVENTS[event]
    sure = _confidence(msg.get("end_of_turn_confidence")) if event in _SURE else None
    # An update with no words still says how sure Flux is the turn is over: through a
    # pause, that is what a listening cue is weighed on.
    if turn is None and not text and (event != "Update" or sure is None):
        return None
    return Heard(
        text=text,
        final=turn == "done",
        turn=turn,
        words=_words(msg.get("words")),
        confidence=sure,
    )


class _Stream:
    def __init__(self, socket: Socket) -> None:
        self._socket = socket
        self._closed = False
        # Audio waits here until there is a whole chunk to send.
        self._rest = b""

    async def send(self, pcm: bytes) -> None:
        if self._closed or not pcm:
            return
        self._rest += pcm
        try:
            while len(self._rest) >= CHUNK_BYTES:
                piece, self._rest = self._rest[:CHUNK_BYTES], self._rest[CHUNK_BYTES:]
                await self._socket.send(piece)
        except ConnectionClosed as e:
            raise VoiceError("unreachable", type(e).__name__) from e

    async def finish(self) -> None:
        if self._closed:
            return
        try:
            if self._rest:
                await self._socket.send(self._rest)
                self._rest = b""
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
    turns = True
    word_times = True

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
