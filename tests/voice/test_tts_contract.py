"""One suite every speech provider must pass (VOI-11).

A new provider adds one line to PROVIDERS. If it passes, the core can use it
without knowing anything about it.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

import httpx
import pytest

from trillion.secrets.store import MemoryStore
from trillion.voice.catalog import SPEECH, speech_option
from trillion.voice.providers.elevenlabs import ElevenLabs
from trillion.voice.providers.openai_compat import CompatibleSpeech
from trillion.voice.providers.system import SystemVoice
from trillion.voice.types import TTSProvider, Voice, VoiceError

SAMPLE_KEY = "voice-" + "test" * 5
PCM = bytes(range(256)) * 4  # 512 whole 16-bit samples


@dataclass
class Fake:
    """A stand-in for a provider's server: what to answer, and what it was asked."""

    status: int = 200
    audio: bytes = PCM
    requests: list[httpx.Request] = field(default_factory=list)
    fail: type[Exception] | None = None

    def client(self) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if self.fail is not None:
                raise self.fail("down")
            if self.status != 200:
                return httpx.Response(self.status, json={"detail": f"you sent {SAMPLE_KEY}"})
            if request.method == "GET":
                voices = [
                    {"voice_id": "v1", "name": "Ada", "preview_url": "https://x.example/a.mp3"}
                ]
                return httpx.Response(200, json={"voices": voices, "has_more": False})
            # Odd-sized chunks, as a network delivers them.
            return httpx.Response(200, content=_chunks(self.audio, 333))

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _chunks(data: bytes, size: int) -> AsyncIterator[bytes]:
    for i in range(0, len(data), size):
        yield data[i : i + size]


PROVIDERS: dict[str, Callable[[Fake], TTSProvider]] = {
    "system": lambda fake: SystemVoice(),
    "elevenlabs": lambda fake: ElevenLabs(api_key=lambda: SAMPLE_KEY, client=fake.client()),
    "custom": lambda fake: CompatibleSpeech(
        base_url="http://192.168.1.20:8880/v1/", client=fake.client()
    ),
}


def test_every_catalog_entry_is_under_contract() -> None:
    assert set(PROVIDERS) == {o.kind for o in SPEECH}


@pytest.fixture(params=sorted(PROVIDERS))
def kind(request: pytest.FixtureRequest) -> str:
    return str(request.param)


async def said(p: TTSProvider, text: str = "Short answer: yes.") -> bytes:
    return b"".join([chunk async for chunk in p.synthesize(text, voice="v1")])


async def test_it_says_where_the_sound_is_made_and_in_what_format(kind: str) -> None:
    p = PROVIDERS[kind](Fake())
    option = speech_option(kind)
    assert option is not None and p.name == kind and p.where == option.where
    if p.where == "client":
        assert p.output_format is None and await said(p) == b""
    else:
        assert p.output_format is not None and p.output_format.sample_rate >= 8000


async def test_audio_arrives_in_whole_samples_and_nothing_is_lost(kind: str) -> None:
    fake = Fake()
    p = PROVIDERS[kind](fake)
    if p.where == "client":
        return
    chunks = [c async for c in p.synthesize("Short answer: yes.", voice="v1")]
    assert chunks and all(len(c) % 2 == 0 for c in chunks)
    assert b"".join(chunks) == PCM


async def test_empty_text_costs_nothing(kind: str) -> None:
    fake = Fake()
    assert await said(PROVIDERS[kind](fake), "   ") == b""
    assert fake.requests == []


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, "unauthorized"), (429, "rate_limited"), (500, "provider_unavailable")],
)
async def test_failures_are_named_and_never_carry_the_reply_or_the_key(
    kind: str, status: int, expected: str
) -> None:
    p = PROVIDERS[kind](Fake(status=status))
    if p.where == "client":
        return
    for attempt in (said(p, "a private sentence"), p.voices()):
        with pytest.raises(VoiceError) as e:
            await attempt
        assert e.value.kind == expected
        assert SAMPLE_KEY not in str(e.value) and "private" not in str(e.value)


async def test_an_unreachable_provider_is_a_named_failure_not_a_crash(kind: str) -> None:
    p = PROVIDERS[kind](Fake(fail=httpx.ConnectError))
    if p.where == "client":
        return
    with pytest.raises(VoiceError) as e:
        await said(p)
    assert e.value.kind == "unreachable"


async def test_voices_are_listed_with_stable_ids(kind: str) -> None:
    voices = await PROVIDERS[kind](Fake()).voices()
    assert all(isinstance(v, Voice) and v.id and v.name for v in voices)
    assert len({v.id for v in voices}) == len(voices)


def test_each_option_says_what_it_costs_and_free_ones_need_no_key() -> None:
    assert any(o.free for o in SPEECH)  # nobody needs a paid account to hear her
    for o in SPEECH:
        assert o.note and o.title
        assert not (o.free and o.needs_key)
        assert (o.key_link is not None) == o.needs_key
        # Catalog entries build against the keychain, never a literal key.
        assert o.build(MemoryStore(), "http://127.0.0.1:8880/v1").name == o.kind


async def test_elevenlabs_request_shape() -> None:
    fake = Fake()
    p = ElevenLabs(api_key=lambda: SAMPLE_KEY, client=fake.client())
    await said(p)
    assert (await p.voices()) == [Voice(id="v1", name="Ada", preview_url="https://x.example/a.mp3")]
    speak, listing = fake.requests
    assert speak.method == "POST"
    assert speak.url.path == "/v1/text-to-speech/v1/stream"
    assert speak.url.params["output_format"] == "pcm_16000"
    assert speak.headers["xi-api-key"] == SAMPLE_KEY
    assert listing.url.path == "/v2/voices" and listing.headers["xi-api-key"] == SAMPLE_KEY


async def test_compatible_request_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            voices = ["af_heart", {"id": "bm_lewis", "name": "Lewis"}, {"name": "no id"}, 7]
            return httpx.Response(200, json={"voices": voices})
        return httpx.Response(200, content=PCM)

    seen: list[httpx.Request] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p = CompatibleSpeech(base_url="http://192.168.1.20:8880/v1/", client=client)
    assert await said(p) == PCM
    assert await p.voices() == [
        Voice(id="af_heart", name="af_heart"),
        Voice(id="bm_lewis", name="Lewis"),
    ]
    speak, listing = seen
    assert speak.method == "POST" and str(speak.url) == "http://192.168.1.20:8880/v1/audio/speech"
    assert json.loads(speak.content) == {
        "model": "tts-1",
        "input": "Short answer: yes.",
        "voice": "v1",
        "response_format": "pcm",
    }
    assert str(listing.url) == "http://192.168.1.20:8880/v1/audio/voices"
    # No key is ever sent: this provider is for services that need none.
    assert all("authorization" not in r.headers for r in seen)
