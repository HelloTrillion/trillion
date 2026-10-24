"""Whisper running on this machine (VOI-11b): free, and nothing said leaves it.

Uses faster-whisper, an optional install because it is large. It is not a
streaming recognizer: the utterance is transcribed once the user stops, so there
are no words to show while they speak, and it is slower than a hosted service.
"""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from trillion import paths
from trillion.voice import models
from trillion.voice.models import ModelFile, ModelPack
from trillion.voice.types import Choice, Heard, VoiceError

NAME = "whisper"
_HOST = "https://huggingface.co/Systran"


def _pack(model: str, revision: str, sizes: tuple[tuple[str, int, str], ...]) -> ModelPack:
    return ModelPack(
        id=model,
        base_url=f"{_HOST}/faster-whisper-{model}/resolve/{revision}",
        files=tuple(ModelFile(name=n, size=s, sha256=h) for n, s, h in sizes),
    )


_TOKENIZER = ("tokenizer.json", 2203239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab")  # fmt: skip  # noqa: E501
_VOCABULARY = ("vocabulary.txt", 459861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913")  # fmt: skip  # noqa: E501

# Checksums are of the files at these exact revisions (MIT-licensed conversions of
# OpenAI's Whisper). Changing a model means changing its revision and its checksums.
PACKS: tuple[tuple[ModelPack, str], ...] = (
    (
        _pack(
            "base.en",
            "3d3d5dee26484f91867d81cb899cfcf72b96be6c",
            (
                (
                    "config.json",
                    2227,
                    "f3bc3821e9fc76a27bae538e11ae5b677dcdd352b4600429ce7951d398569aeb",
                ),
                (
                    "model.bin",
                    145216508,
                    "2a166925539a16005f14ff328359f9b9adb9dc4fb631bb3b227526862e93e2ef",
                ),
                (
                    "tokenizer.json",
                    2128466,
                    "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df",
                ),
                (
                    "vocabulary.txt",
                    422309,
                    "ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf",
                ),
            ),
        ),
        "English, fast",
    ),
    (
        _pack(
            "base",
            "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
            (
                (
                    "config.json",
                    2309,
                    "56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a",
                ),
                (
                    "model.bin",
                    145217532,
                    "d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9",
                ),
                _TOKENIZER,
                _VOCABULARY,
            ),
        ),
        "Many languages, fast",
    ),
    (
        _pack(
            "small",
            "536b0662742c02347bc0e980a01041f333bce120",
            (
                (
                    "config.json",
                    2370,
                    "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828",
                ),
                (
                    "model.bin",
                    483546902,
                    "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
                ),
                _TOKENIZER,
                _VOCABULARY,
            ),
        ),
        "Many languages, more accurate, slower",
    ),
)

# (folder, pcm) -> text. Runs in a worker thread.
Transcribe = Callable[[Path, bytes], str]

_loaded: dict[Path, Any] = {}


def available() -> bool:
    """Whether the optional recognizer is installed at all."""
    return importlib.util.find_spec("faster_whisper") is not None


def _transcribe(folder: Path, pcm: bytes) -> str:
    import numpy as np
    from faster_whisper import (
        WhisperModel,
    )

    model = _loaded.get(folder)
    if model is None:
        # Loaded from the verified folder only; it never reaches the network.
        model = WhisperModel(str(folder), device="cpu", compute_type="int8", local_files_only=True)
        _loaded.clear()  # one model in memory at a time
        _loaded[folder] = model
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    english_only = folder.name.endswith(".en")
    segments, _ = model.transcribe(
        audio,
        language="en" if english_only else None,
        beam_size=5,
        # Each utterance stands alone: earlier text must not leak into this one.
        condition_on_previous_text=False,
    )
    return " ".join(s.text.strip() for s in segments).strip()


class _Stream:
    def __init__(self, folder: Path, transcribe: Transcribe) -> None:
        self._folder = folder
        self._transcribe = transcribe
        self._audio = bytearray()
        self._events: asyncio.Queue[Heard | VoiceError | None] = asyncio.Queue()
        self._closed = False
        self._finished = False

    async def send(self, pcm: bytes) -> None:
        if not self._finished and not self._closed:
            self._audio.extend(pcm)

    async def finish(self) -> None:
        if self._finished or self._closed:
            return
        self._finished = True
        pcm = bytes(self._audio[: len(self._audio) - (len(self._audio) % 2)])
        self._audio.clear()
        try:
            text = await asyncio.to_thread(self._transcribe, self._folder, pcm) if pcm else ""
        except Exception as e:
            # The class name only: a recognizer's message can quote what it heard.
            await self._events.put(VoiceError("recognizer_failed", type(e).__name__))
        else:
            if text and not self._closed:
                await self._events.put(Heard(text=text, final=True))
        await self._events.put(None)

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._audio.clear()
            await self._events.put(None)

    async def events(self) -> AsyncIterator[Heard]:
        while True:
            item = await self._events.get()
            if item is None:
                return
            if isinstance(item, VoiceError):
                raise item
            yield item


class LocalWhisper:
    name = NAME
    streaming = False
    turns = False

    def __init__(
        self,
        *,
        root: Callable[[], Path] | None = None,
        transcribe: Transcribe = _transcribe,
        installed: Callable[[], bool] = available,
        packs: tuple[tuple[ModelPack, str], ...] = PACKS,
    ) -> None:
        self._packs = packs
        self._root = root or (lambda: paths.models_dir() / "whisper")
        self._transcribe = transcribe
        self._installed = installed

    def _folder(self, pack: ModelPack) -> Path:
        return self._root() / pack.id

    def _pack(self, choice: str) -> ModelPack:
        pack = next((p for p, _ in self._packs if p.id == choice), None)
        if pack is None:
            raise VoiceError("unknown_choice")
        return pack

    async def open(self, *, choice: str) -> _Stream:
        if not self._installed():
            raise VoiceError("not_installed")
        pack = self._pack(choice)
        if not models.installed(pack, self._folder(pack)):
            raise VoiceError("needs_download")
        return _Stream(self._folder(pack), self._transcribe)

    async def choices(self) -> list[Choice]:
        if not self._installed():
            raise VoiceError("not_installed")
        return [
            Choice(
                id=pack.id,
                name=label,
                download=None if models.installed(pack, self._folder(pack)) else pack.size,
            )
            for pack, label in self._packs
        ]

    async def prepare(self, choice: str) -> None:
        if not self._installed():
            raise VoiceError("not_installed")
        pack = self._pack(choice)
        await models.download(pack, self._folder(pack))
