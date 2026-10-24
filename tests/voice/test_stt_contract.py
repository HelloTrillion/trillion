"""One suite every recognizer must pass (VOI-11).

Two very different ones ship: Deepgram streams words back over a socket while the
user speaks, and local Whisper transcribes the whole utterance once they stop.
The core treats them the same way, which is what this file proves. A new
provider adds one line to PROVIDERS.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from websockets.exceptions import ConnectionClosedOK, InvalidStatus
from websockets.http11 import Response

from tests.voice.audio import tone
from trillion.secrets.store import MemoryStore
from trillion.voice import models
from trillion.voice.catalog import LISTENING, listen_option
from trillion.voice.models import ModelFile, ModelPack
from trillion.voice.providers import deepgram
from trillion.voice.providers.deepgram import Deepgram
from trillion.voice.providers.whisper import PACKS, LocalWhisper
from trillion.voice.types import Heard, STTProvider, VoiceError

SAMPLE_KEY = "listen-" + "test" * 5
SAID = "write a note saying hi"
WEIGHTS = b"not really a model"
PACK = ModelPack(
    id="tiny-test",
    base_url="https://models.example/tiny-test/resolve/abc123",
    files=(
        ModelFile(name="model.bin", size=len(WEIGHTS), sha256=hashlib.sha256(WEIGHTS).hexdigest()),
    ),
)


def turn_info(event: str, text: str) -> str:
    return json.dumps({"type": "TurnInfo", "event": event, "turn_index": 0,
                       "transcript": text, "words": [], "end_of_turn_confidence": 0.9})  # fmt: skip


class FakeSocket:
    """Deepgram's end of the socket: it reports words as audio arrives."""

    def __init__(self) -> None:
        self.sent: list[bytes | str] = []
        self._out: asyncio.Queue[str | None] = asyncio.Queue()
        self.closed = False

    async def send(self, data: bytes | str) -> None:
        if self.closed:
            raise ConnectionClosedOK(None, None)
        self.sent.append(data)
        if isinstance(data, bytes) and len([s for s in self.sent if isinstance(s, bytes)]) == 1:
            await self._out.put(turn_info("Update", "write a note"))
        if isinstance(data, str) and json.loads(data)["type"] == "CloseStream":
            await self._out.put(json.dumps({"type": "Connected"}))
            await self._out.put(turn_info("EndOfTurn", SAID))
            await self._out.put(None)

    async def recv(self) -> str:
        item = await self._out.get()
        if item is None:
            self.closed = True
            raise ConnectionClosedOK(None, None)
        return item

    async def close(self) -> None:
        self.closed = True
        await self._out.put(None)


@dataclass
class Fake:
    """A stand-in for a recognizer's outside world, and a record of what it was asked."""

    root: Path
    status: int = 200
    socket: FakeSocket = field(default_factory=FakeSocket)
    urls: list[str] = field(default_factory=list)
    headers: list[Mapping[str, str]] = field(default_factory=list)
    heard_bytes: int = 0
    installed: bool = True

    async def connect(self, url: str, headers: Mapping[str, str]) -> FakeSocket:
        self.urls.append(url)
        self.headers.append(headers)
        if self.status != 200:
            raise InvalidStatus(Response(self.status, "no", httpx.Headers()))  # type: ignore[arg-type]
        return self.socket

    def client(self) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.headers.append(dict(request.headers))
            body = {"projects": []} if self.status == 200 else {"err": f"you sent {SAMPLE_KEY}"}
            return httpx.Response(self.status, json=body)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def transcribe(self, folder: Path, pcm: bytes) -> str:
        self.heard_bytes += len(pcm)
        return SAID if pcm else ""


def _deepgram(fake: Fake) -> STTProvider:
    return Deepgram(api_key=lambda: SAMPLE_KEY, connect=fake.connect, client=fake.client())


def _whisper(fake: Fake) -> STTProvider:
    folder = fake.root / PACK.id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "model.bin").write_bytes(WEIGHTS)
    return LocalWhisper(
        root=lambda: fake.root,
        transcribe=fake.transcribe,
        installed=lambda: fake.installed,
        packs=((PACK, "Test"),),
    )


PROVIDERS: dict[str, Callable[[Fake], STTProvider]] = {"deepgram": _deepgram, "whisper": _whisper}
CHOICE = {"deepgram": "en", "whisper": PACK.id}


def test_every_catalog_entry_is_under_contract() -> None:
    assert set(PROVIDERS) == {o.kind for o in LISTENING}
    assert listen_option("nope") is None
    # At least one free, local way to be heard and one hosted (VOI-11, VOI-11b).
    assert {o.free for o in LISTENING} == {True, False}
    for o in LISTENING:
        assert o.note and o.title and o.needs_key == (o.key_link is not None)
        built = o.build(MemoryStore())
        assert built.name == o.kind


@pytest.fixture(params=sorted(PROVIDERS))
def kind(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def fake(tmp_path: Path) -> Fake:
    return Fake(root=tmp_path)


async def listen(p: STTProvider, choice: str, pcm: bytes = tone(1.0)) -> list[Heard]:
    stream = await p.open(choice=choice)
    heard: list[Heard] = []

    async def read() -> None:
        async for h in stream.events():
            heard.append(h)

    reader = asyncio.create_task(read())
    for i in range(0, len(pcm), 3200):
        await stream.send(pcm[i : i + 3200])
    await stream.finish()
    await asyncio.wait_for(reader, timeout=2)
    await stream.close()
    await stream.close()  # twice is fine
    return heard


async def test_audio_in_words_out_and_the_stream_ends(kind: str, fake: Fake) -> None:
    p = PROVIDERS[kind](fake)
    heard = await listen(p, CHOICE[kind])
    finals = [h.text for h in heard if h.final]
    assert " ".join(finals) == SAID
    # A streaming recognizer reports words before the end; the other one cannot.
    assert any(not h.final for h in heard) == p.streaming


async def test_abandoning_a_stream_ends_its_events(kind: str, fake: Fake) -> None:
    stream = await PROVIDERS[kind](fake).open(choice=CHOICE[kind])

    async def read() -> list[Heard]:
        return [h async for h in stream.events()]

    reader = asyncio.create_task(read())
    await stream.send(tone(0.2))
    await asyncio.sleep(0.01)
    await stream.close()
    assert all(not h.final for h in await asyncio.wait_for(reader, timeout=2))
    await stream.send(tone(0.1))  # late audio is dropped, not an error
    await stream.finish()


async def test_choices_are_offered_and_nothing_needs_setup_twice(kind: str, fake: Fake) -> None:
    p = PROVIDERS[kind](fake)
    found = await p.choices()
    assert CHOICE[kind] in {c.id for c in found} and all(c.name for c in found)
    assert all(c.download is None for c in found)
    await p.prepare(CHOICE[kind])


# ── Deepgram ─────────────────────────────────────────────────────────────


async def test_deepgram_asks_for_what_the_page_sends(fake: Fake) -> None:
    await listen(_deepgram(fake), "multi")
    url = fake.urls[0]
    assert url.startswith("wss://api.deepgram.com/v2/listen?")
    for part in ("model=flux-general-multi", "encoding=linear16", "sample_rate=16000",
                 "eot_threshold=0.7", "eager_eot_threshold=0.5",
                 "eot_timeout_ms=5000"):  # fmt: skip
        assert part in url
    assert "language=" not in url and "interim_results" not in url
    assert fake.headers[0]["Authorization"] == f"Token {SAMPLE_KEY}"
    assert SAMPLE_KEY not in url  # the key is a header, never part of the address
    sent = fake.socket.sent
    assert sum(len(s) for s in sent if isinstance(s, bytes)) == len(tone(1.0))
    assert json.loads(sent[-1]) == {"type": "CloseStream"}
    # An unknown language falls back to the first, rather than reaching the URL.
    await _deepgram(Fake(root=fake.root)).open(choice="en&keywords=x")
    assert "keywords" not in fake.urls[-1]


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "unauthorized"), (402, "payment_required"), (429, "rate_limited"),
     (503, "provider_unavailable")],
)  # fmt: skip
async def test_deepgram_failures_have_a_kind_and_never_the_key(
    fake: Fake, status: int, kind: str
) -> None:
    fake.status = status
    p = _deepgram(fake)
    for attempt in (p.open(choice="en"), p.choices()):
        with pytest.raises(VoiceError) as e:
            await attempt
        assert e.value.kind == kind and SAMPLE_KEY not in str(e.value)


async def test_deepgram_unreachable_and_garbled() -> None:
    async def refuse(url: str, headers: Mapping[str, str]) -> FakeSocket:
        raise OSError("no route")

    with pytest.raises(VoiceError) as e:
        await Deepgram(api_key=lambda: SAMPLE_KEY, connect=refuse).open(choice="en")
    assert e.value.kind == "unreachable"
    with pytest.raises(VoiceError) as e:
        deepgram.parse("not json")
    assert e.value.kind == "bad_response"
    assert deepgram.parse(json.dumps({"type": "SpeechStarted"})) is None
    assert deepgram.parse(turn_info("Update", "")) is None


# ── Local Whisper ────────────────────────────────────────────────────────


async def test_whisper_hears_everything_once_and_only_after_the_end(fake: Fake) -> None:
    await listen(_whisper(fake), PACK.id, tone(0.5) + b"\x01")  # an odd trailing byte
    assert fake.heard_bytes == len(tone(0.5))
    assert await listen(_whisper(fake), PACK.id, b"") == []  # nothing said, nothing invented


async def test_whisper_says_what_is_missing(fake: Fake) -> None:
    p = _whisper(fake)
    fake.installed = False
    for attempt in (p.open(choice=PACK.id), p.choices(), p.prepare(PACK.id)):
        with pytest.raises(VoiceError) as e:
            await attempt
        assert e.value.kind == "not_installed"
    fake.installed = True
    (fake.root / PACK.id / "model.bin").unlink()
    assert [c.download for c in await p.choices()] == [PACK.size]
    with pytest.raises(VoiceError) as e:
        await p.open(choice=PACK.id)
    assert e.value.kind == "needs_download"
    with pytest.raises(VoiceError) as e:
        await p.open(choice="other")
    assert e.value.kind == "unknown_choice"


async def test_whisper_reports_a_recognizer_crash_without_its_message(fake: Fake) -> None:
    def crash(folder: Path, pcm: bytes) -> str:
        raise RuntimeError("heard: my password is hunter2")

    p = LocalWhisper(
        root=lambda: fake.root, transcribe=crash, installed=lambda: True, packs=((PACK, "T"),)
    )
    _whisper(fake)  # writes the model file
    with pytest.raises(VoiceError) as e:
        await listen(p, PACK.id)
    assert e.value.kind == "recognizer_failed" and "hunter2" not in str(e.value)


def test_the_shipped_models_are_pinned_to_a_revision_and_a_checksum() -> None:
    assert [p.id for p, _ in PACKS] == ["base.en", "base", "small"]
    for pack, label in PACKS:
        assert label
        revision = pack.base_url.rsplit("/", 1)[-1]
        assert len(revision) == 40 and "/resolve/" in pack.base_url  # a commit, not a branch
        assert pack.base_url.startswith("https://huggingface.co/Systran/faster-whisper-")
        assert {f.name for f in pack.files} >= {"model.bin", "config.json"}
        assert all(len(f.sha256) == 64 and f.size > 0 for f in pack.files)


# ── Model files (ONB-7) ──────────────────────────────────────────────────


def _host(content: bytes, status: int = 200) -> tuple[httpx.AsyncClient, list[str]]:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(status, content=content)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), asked


async def test_a_model_is_fetched_once_checked_and_then_left_alone(tmp_path: Path) -> None:
    client, asked = _host(WEIGHTS)
    seen: list[tuple[int, int]] = []
    assert not models.installed(PACK, tmp_path)
    await models.download(PACK, tmp_path, client=client, progress=lambda a, b: seen.append((a, b)))
    assert asked == ["https://models.example/tiny-test/resolve/abc123/model.bin"]
    assert (tmp_path / "model.bin").read_bytes() == WEIGHTS and models.installed(PACK, tmp_path)
    assert seen[-1] == (PACK.size, PACK.size)
    await models.download(PACK, tmp_path, client=client)
    assert len(asked) == 1  # already here: nothing is fetched again


@pytest.mark.parametrize(
    ("content", "status", "kind"),
    [
        (b"x" * len(WEIGHTS), 200, "download_mismatch"),  # right size, wrong contents
        (WEIGHTS + b"more", 200, "download_mismatch"),
        (WEIGHTS[:5], 200, "download_mismatch"),
        (b"gone", 404, "download_failed"),
    ],
)
async def test_a_file_that_is_not_the_pinned_one_is_never_kept(
    tmp_path: Path, content: bytes, status: int, kind: str
) -> None:
    client, _ = _host(content, status)
    with pytest.raises(VoiceError) as e:
        await models.download(PACK, tmp_path, client=client)
    assert e.value.kind == kind
    assert list(tmp_path.iterdir()) == [] and not models.installed(PACK, tmp_path)


def test_deepgram_turn_events_become_turn_signals() -> None:
    assert deepgram.parse(turn_info("Update", "so I")) == Heard(text="so I", final=False)
    assert deepgram.parse(turn_info("EagerEndOfTurn", "so I said")) == Heard(
        text="so I said", final=False, turn="maybe"
    )
    assert deepgram.parse(turn_info("TurnResumed", "so I said")) == Heard(
        text="so I said", final=False, turn="resumed"
    )
    assert deepgram.parse(turn_info("EndOfTurn", "so I said no")) == Heard(
        text="so I said no", final=True, turn="done"
    )
    assert deepgram.parse(turn_info("StartOfTurn", "")) is None
    assert deepgram.parse(json.dumps({"type": "Connected"})) is None


async def test_deepgram_sends_audio_in_80ms_chunks(fake: Fake) -> None:
    await listen(_deepgram(fake), "en")
    chunks = [s for s in fake.socket.sent if isinstance(s, bytes)]
    assert chunks and all(len(c) == 2560 for c in chunks[:-1]) and len(chunks[-1]) <= 2560
