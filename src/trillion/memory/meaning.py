"""What a memory means, as numbers: MEM-2's search by meaning.

Optional twice over. The runtime is an optional install (`trillion[meaning]`), and
the model is a file fetched once by `trillion setup` from a pinned address and
checked against the checksum written here (ONB-7). Nothing here downloads while
she runs (ONB-8). Without either part, memory is found by its words alone.
"""

from __future__ import annotations

import hashlib
import importlib.util
import math
import struct
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx

from trillion.voice import models
from trillion.voice.models import ModelFile, ModelPack

Vector = tuple[float, ...]

# BAAI's own repository, at one exact revision (MIT licence). Changing the model
# means changing the revision, the checksums, and the floor below.
PACK = ModelPack(
    id="bge-small-en-v1.5",
    base_url=(
        "https://huggingface.co/BAAI/bge-small-en-v1.5/resolve/"
        "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
    ),
    files=(
        ModelFile(
            name="onnx/model.onnx",
            size=133093490,
            sha256="828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35",
        ),
        ModelFile(
            name="tokenizer.json",
            size=711396,
            sha256="d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
        ),
    ),
)
DIMENSIONS = 384
# Below this cosine similarity a memory is not about the question. Measured for this
# model with its search prefix: related pairs score 0.6 and up, unrelated ones under
# 0.55. See docs/specs/2026-11-07-recall-by-meaning.md.
FLOOR = 0.6
# bge's advice for a short question searched against longer passages.
_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
_MAX_TOKENS = 512
_RUNTIME = ("onnxruntime", "tokenizers", "numpy")

State = Literal["on", "no_runtime", "no_model"]


class MeaningError(Exception):
    """Meaning search cannot run. `kind` is safe to show and to log."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class Embedder(Protocol):
    # Stored with every vector, so a vector from another model is never compared.
    model: str
    # The similarity below which a memory is not about the question (see FLOOR).
    floor: float

    def embed(self, texts: Sequence[str], *, query: bool) -> list[Vector]:
        """One unit-length vector per text. `query` marks a question rather than a
        memory. Slow: call it off the event loop. Raises on any failure."""
        ...


# ── Vectors ──────────────────────────────────────────────────────────────


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pack(v: Vector) -> bytes:
    """Little-endian float32: the layout sqlite-vec reads too."""
    return struct.pack(f"<{len(v)}f", *v)


def unpack(raw: bytes) -> Vector:
    return struct.unpack(f"<{len(raw) // 4}f", raw)


def similarity(a: Vector, b: Vector) -> float:
    """Cosine similarity of two unit vectors."""
    return math.fsum(x * y for x, y in zip(a, b, strict=True))


# ── What is on this machine ──────────────────────────────────────────────


def runtime_installed() -> bool:
    return all(importlib.util.find_spec(name) is not None for name in _RUNTIME)


def folder(models_dir: Path) -> Path:
    return models_dir / PACK.id


def state(models_dir: Path) -> State:
    """Whether meaning search would run here. Looks only; loads nothing."""
    if not runtime_installed():
        return "no_runtime"
    if not models.installed(PACK, folder(models_dir)):
        return "no_model"
    return "on"


def local(models_dir: Path) -> Embedder | None:
    """The model on this machine, or None when either part is missing. Not loaded
    until first used, and checked against its checksums when it is."""
    if state(models_dir) != "on":
        return None
    return OnnxEmbedder(folder(models_dir))


async def fetch(
    models_dir: Path,
    *,
    client: httpx.AsyncClient | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> None:
    """Download the model into the models folder, refusing any file whose checksum
    differs (ONB-7). Only `trillion setup` calls this. Raises MeaningError."""
    from trillion.voice.types import VoiceError

    try:
        await models.download(PACK, folder(models_dir), client=client, progress=progress)
    except VoiceError as e:
        raise MeaningError(e.kind) from e


def verify(where: Path) -> None:
    """Every file present with the size and checksum it was fetched with."""
    for f in PACK.files:
        path = where / f.name
        h = hashlib.sha256()
        try:
            with open(path, "rb") as src:
                while chunk := src.read(1 << 20):
                    h.update(chunk)
        except OSError as e:
            raise MeaningError("no_model") from e
        if path.stat().st_size != f.size or h.hexdigest() != f.sha256:
            raise MeaningError("checksum")


# ── The model ────────────────────────────────────────────────────────────


class OnnxEmbedder:
    """bge-small-en-v1.5 on the CPU, through onnxruntime. Loaded on first use."""

    model = PACK.id
    floor = FLOOR

    def __init__(self, where: Path) -> None:
        self._where = where
        self._loaded: tuple[Any, Any] | None = None
        self._lock = threading.Lock()

    def _load(self) -> tuple[Any, Any]:
        with self._lock:
            if self._loaded is None:
                # A file changed since it was fetched is never loaded.
                verify(self._where)
                import onnxruntime
                from tokenizers import Tokenizer

                tokenizer = Tokenizer.from_file(str(self._where / "tokenizer.json"))
                tokenizer.enable_truncation(_MAX_TOKENS)
                tokenizer.enable_padding()
                session = onnxruntime.InferenceSession(
                    str(self._where / "onnx" / "model.onnx"),
                    providers=["CPUExecutionProvider"],
                )
                self._loaded = (tokenizer, session)
            return self._loaded

    def embed(self, texts: Sequence[str], *, query: bool) -> list[Vector]:
        if not texts:
            return []
        tokenizer, session = self._load()
        import numpy as np

        encoded = tokenizer.encode_batch([_QUERY_PREFIX + t if query else t for t in texts])
        ids = np.array([e.ids for e in encoded], dtype=np.int64)
        feeds = {
            "input_ids": ids,
            "attention_mask": np.array([e.attention_mask for e in encoded], dtype=np.int64),
        }
        if any(i.name == "token_type_ids" for i in session.get_inputs()):
            feeds["token_type_ids"] = np.zeros_like(ids)
        # bge uses the first token's output as the sentence, normalised.
        first = session.run(None, feeds)[0][:, 0]
        unit = first / np.linalg.norm(first, axis=1, keepdims=True)
        return [tuple(float(x) for x in row) for row in unit]
