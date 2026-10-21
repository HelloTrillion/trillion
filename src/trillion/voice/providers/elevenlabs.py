"""ElevenLabs text to speech, on the user's own account and key (VOI-11c)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import httpx

from trillion import net
from trillion.voice.types import AudioFormat, Voice, VoiceError, Where

NAME = "elevenlabs"
BASE_URL = "https://api.elevenlabs.io"
# Flash: ElevenLabs' low-latency model. Measured from here, its first audio arrives
# about 0.1s sooner than Turbo's, with less silence before the first word.
DEFAULT_MODEL = "eleven_flash_v2_5"
# 16 kHz PCM is available on every plan; higher PCM rates need a paid tier.
FORMAT = AudioFormat(sample_rate=16_000)
_OUTPUT = "pcm_16000"
_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
_PAGE = 100
_MAX_PAGES = 10
_KINDS = {
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    422: "bad_request",
    429: "rate_limited",
}


def _kind(status: int) -> str:
    return _KINDS.get(status, "provider_unavailable" if status >= 500 else "bad_request")


class ElevenLabs:
    name = NAME
    where: Where = "core"
    output_format: AudioFormat | None = FORMAT

    def __init__(
        self,
        *,
        api_key: Callable[[], str],
        model: str = DEFAULT_MODEL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # The key is fetched for each request and never held (SEC-6).
        self._api_key = api_key
        self._model = model
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT, limits=net.KEEP_ALIVE)

    async def warm(self) -> None:
        """Open the connection before the first sentence needs it. Sends nothing."""
        await net.warm(self._client, BASE_URL)

    async def synthesize(self, text: str, *, voice: str) -> AsyncIterator[bytes]:
        if not text.strip():
            return
        url = f"{BASE_URL}/v1/text-to-speech/{voice}/stream"
        try:
            async with self._client.stream(
                "POST",
                url,
                params={"output_format": _OUTPUT},
                headers={"xi-api-key": self._api_key()},
                json={"text": text, "model_id": self._model},
            ) as r:
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
        found: list[Voice] = []
        token: str | None = None
        try:
            for _ in range(_MAX_PAGES):
                params: dict[str, str | int] = {"page_size": _PAGE}
                if token:
                    params["next_page_token"] = token
                r = await self._client.get(
                    f"{BASE_URL}/v2/voices", params=params, headers={"xi-api-key": self._api_key()}
                )
                if r.status_code != 200:
                    raise VoiceError(_kind(r.status_code), f"HTTP {r.status_code}")
                body = r.json()
                for v in body.get("voices") or []:
                    if isinstance(v, dict) and v.get("voice_id"):
                        found.append(
                            Voice(
                                id=str(v["voice_id"]),
                                name=str(v.get("name") or v["voice_id"]),
                                preview_url=v.get("preview_url") or None,
                            )
                        )
                token = body.get("next_page_token")
                if not body.get("has_more") or not token:
                    break
        except httpx.TimeoutException as e:
            raise VoiceError("timeout", type(e).__name__) from e
        except httpx.HTTPError as e:
            raise VoiceError("unreachable", type(e).__name__) from e
        except (ValueError, AttributeError) as e:
            raise VoiceError("bad_response", type(e).__name__) from e
        return found
