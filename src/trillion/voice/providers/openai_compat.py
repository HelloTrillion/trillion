"""Text to speech from any service that speaks OpenAI's speech API (VOI-11).

Kokoro-FastAPI, for one, runs a natural neural voice on a machine of the user's
own. The address is checked by the same rule as a compatible model's (#33), and
no key is sent: this provider is for services that need none.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from trillion import net
from trillion.voice.types import AudioFormat, Voice, VoiceError, Where

NAME = "custom"
# The model name OpenAI's own API documents. Compatible servers accept it as an alias.
# ponytail: fixed model; offer a choice when a server needs a different name.
MODEL = "tts-1"
# "pcm" in this API is headerless 16-bit mono at 24 kHz.
FORMAT = AudioFormat(sample_rate=24_000)
_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
_KINDS = {
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    422: "bad_request",
    429: "rate_limited",
}


def _kind(status: int) -> str:
    return _KINDS.get(status, "provider_unavailable" if status >= 500 else "bad_request")


class CompatibleSpeech:
    name = NAME
    where: Where = "core"
    output_format: AudioFormat | None = FORMAT

    def __init__(self, *, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT, limits=net.KEEP_ALIVE)

    async def warm(self) -> None:
        """Open the connection before the first sentence needs it. Sends nothing."""
        await net.warm(self._client, self._base)

    async def synthesize(self, text: str, *, voice: str) -> AsyncIterator[bytes]:
        if not text.strip():
            return
        body = {"model": MODEL, "input": text, "voice": voice, "response_format": "pcm"}
        try:
            async with self._client.stream("POST", f"{self._base}/audio/speech", json=body) as r:
                if r.status_code != 200:
                    await r.aread()
                    # The status only: an error body can echo the text it was sent.
                    raise VoiceError(_kind(r.status_code), f"HTTP {r.status_code}")
                odd = b""
                async for chunk in r.aiter_bytes():
                    data = odd + chunk
                    # A network chunk can end mid-sample; the page needs whole ones.
                    cut = len(data) - (len(data) % 2)
                    odd = data[cut:]
                    if cut:
                        yield data[:cut]
        except httpx.TimeoutException as e:
            raise VoiceError("timeout", type(e).__name__) from e
        except httpx.HTTPError as e:
            raise VoiceError("unreachable", type(e).__name__) from e

    async def voices(self) -> list[Voice]:
        # Not part of OpenAI's own API, but the compatible servers that run local
        # voices offer it, as a list of names or of {id, name}.
        try:
            r = await self._client.get(f"{self._base}/audio/voices")
            if r.status_code != 200:
                raise VoiceError(_kind(r.status_code), f"HTTP {r.status_code}")
            found: list[Voice] = []
            for v in r.json().get("voices") or []:
                vid = v.get("id") if isinstance(v, dict) else v
                if isinstance(vid, str) and vid:
                    name = v.get("name") if isinstance(v, dict) else None
                    found.append(Voice(id=vid, name=str(name or vid)))
        except httpx.TimeoutException as e:
            raise VoiceError("timeout", type(e).__name__) from e
        except httpx.HTTPError as e:
            raise VoiceError("unreachable", type(e).__name__) from e
        except (ValueError, AttributeError) as e:
            raise VoiceError("bad_response", type(e).__name__) from e
        return list({v.id: v for v in found}.values())
