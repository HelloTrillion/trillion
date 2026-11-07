"""A speaker embedder that runs on this computer (VOI-15, §5.3a).

WeSpeaker's ResNet34-LM, trained on VoxCeleb2 and published by the WeSpeaker
project under CC BY 4.0 (the dataset's licence). It is never shipped with
Trillion: the user fetches it from Settings → Your voice, from a pinned revision,
and it is checked against the SHA-256 below before it is ever loaded (ONB-7).
Nothing here reaches the network; `prepare` is the explicit download.

Optional, like the local recognizer: it needs `onnxruntime` and `numpy`
(`uv sync --extra speaker`). Without them, or without the model file, the
runtime keeps the stub that never knows who spoke.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any

from trillion import paths
from trillion.voice import models
from trillion.voice.models import ModelFile, ModelPack
from trillion.voice.types import (
    CAPTURE,
    SpeakerEmbedder,
    SpeakerEmbedding,
    UnknownSpeaker,
    VoiceError,
    Word,
)

NAME = "wespeaker-resnet34-lm"
# Credited in Settings → Your voice, as CC BY 4.0 asks.
CREDIT = "WeSpeaker ResNet34-LM, trained on VoxCeleb2 (CC BY 4.0)"
LICENCE_URL = "https://creativecommons.org/licenses/by/4.0/"

# The exact revision of the model's repository, and its file's checksum there.
PACK = ModelPack(
    id=NAME,
    base_url=(
        "https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM/resolve/"
        "f0c48c298fd835726c27956a5d617bad7115627e"
    ),
    files=(
        ModelFile(
            name="voxceleb_resnet34_LM.onnx",
            size=26530309,
            sha256="7bb2f06e9df17cdf1ef14ee8a15ab08ed28e8d0ef5054ee135741560df2ec068",
        ),
    ),
)

_RATE = CAPTURE.sample_rate
# Speech shorter than this is not enough to tell a voice by: the answer is "unknown".
MIN_SPEECH_S = 0.4
# Kept around the user's words, so their first and last sounds are whole.
_PAD_S = 0.15

_loaded: dict[Path, Any] = {}


def available() -> bool:
    """Whether the optional parts are installed at all."""
    return all(importlib.util.find_spec(m) is not None for m in ("onnxruntime", "numpy"))


def folder() -> Path:
    return paths.models_dir() / "speaker"


def ready(root: Path | None = None) -> bool:
    """Installed, and the model is on disk."""
    return available() and models.installed(PACK, root or folder())


async def prepare(root: Path | None = None) -> None:
    """The one download, when the user asks for it. Raises VoiceError."""
    if not available():
        raise VoiceError("not_installed")
    await models.download(PACK, root or folder())


def speech_span(pcm: bytes, start_s: float, words: tuple[Word, ...]) -> bytes:
    """The stretch of `pcm` the user's words cover, with a little either side. All of it
    when there are no timed words."""
    if not words:
        return pcm
    first = max(0.0, min(w.start_s for w in words) - _PAD_S - start_s)
    last = max(w.end_s for w in words) + _PAD_S - start_s
    a = int(first * _RATE) * 2
    b = int(max(last, 0.0) * _RATE) * 2
    cut = pcm[a:b]
    return cut if cut else pcm


def fbank(samples: Any) -> Any:
    """Kaldi-style log mel filterbank, as WeSpeaker computes it for inference: 25 ms
    Hamming frames every 10 ms, DC removed, pre-emphasis 0.97, a 512-point power
    spectrum, 80 mel bins from 20 Hz to Nyquist, no dither. `samples` are on the
    int16 scale. Returns (frames, 80), mean-normalized per bin."""
    import numpy as np

    frame, shift, nfft, bins = 400, 160, 512, 80
    x = np.asarray(samples, dtype=np.float64)
    if x.size < frame:
        return np.zeros((0, bins), dtype=np.float32)
    n = 1 + (x.size - frame) // shift
    idx = np.arange(frame)[None, :] + shift * np.arange(n)[:, None]
    frames = x[idx]
    frames = frames - frames.mean(axis=1, keepdims=True)
    prev = np.concatenate([frames[:, :1], frames[:, :-1]], axis=1)
    frames = frames - 0.97 * prev
    window = 0.54 - 0.46 * np.cos(2 * np.pi * np.arange(frame) / (frame - 1))
    spec = np.abs(np.fft.rfft(frames * window, n=nfft)) ** 2  # (n, 257)

    def mel(f: Any) -> Any:
        return 1127.0 * np.log(1.0 + np.asarray(f) / 700.0)

    low, high = mel(20.0), mel(_RATE / 2)
    centers = np.linspace(low, high, bins + 2)
    fft_mel = mel(np.arange(nfft // 2) * _RATE / nfft)
    left, mid, right = centers[:-2, None], centers[1:-1, None], centers[2:, None]
    up = (fft_mel[None, :] - left) / (mid - left)
    down = (right - fft_mel[None, :]) / (right - mid)
    banks = np.maximum(0.0, np.minimum(up, down))
    banks = np.concatenate([banks, np.zeros((bins, 1))], axis=1)  # Nyquist bin unused
    energies = spec @ banks.T
    feats = np.log(np.maximum(energies, np.finfo(np.float32).eps))
    feats = feats - feats.mean(axis=0, keepdims=True)
    return feats.astype(np.float32)


def _session(root: Path) -> Any:
    import onnxruntime as ort

    path = root / PACK.files[0].name
    session = _loaded.get(path)
    if session is None:
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.log_severity_level = 3
        session = ort.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        _loaded.clear()
        _loaded[path] = session
    return session


# Kept from one utterance for the replay check: its first this-many frames (10 ms).
PRINT_FRAMES = 200
# Enrolment's checks on one phrase, on the int16 scale and in seconds.
QUIET_RMS = 300.0
ENROL_SPEECH_S = 1.2
CLIPPED = 0.005


def fingerprint(feats: Any) -> tuple[int, ...]:
    """How the spectrum moves, frame by frame (after Haitsma and Kalker's audio
    fingerprint): 17 bands from the mel bins, and one bit for each pair of neighbouring
    bands, set when their difference grew since the frame before. Bit 16 marks a frame
    louder than the clip's median, so silence is never what matches. The same recording
    played again keeps these bits through gain, noise and a room; a person saying the
    same words again does not."""
    import numpy as np

    if feats.shape[0] < 3:
        return ()
    bands = np.stack([feats[:, i * 4 : i * 4 + 5].mean(axis=1) for i in range(17)], axis=1)
    d = np.diff(bands, axis=1)
    bits = (d[1:] - d[:-1]) > 0
    energy = feats.mean(axis=1)[1:]
    loud = energy > np.median(energy)
    weights = 1 << np.arange(16)
    values = (bits * weights).sum(axis=1) | (loud.astype(np.int64) << 16)
    return tuple(int(v) for v in values[:PRINT_FRAMES])


def _samples(pcm: bytes) -> Any:
    import numpy as np

    usable = len(pcm) - len(pcm) % 2
    return np.frombuffer(pcm[:usable], dtype=np.int16).astype(np.float32)


def _vector(feats: Any, root: Path) -> tuple[float, ...] | None:
    import numpy as np

    if feats.shape[0] == 0:
        return None
    session = _session(root)
    name = session.get_inputs()[0].name
    out = session.run(None, {name: feats[None, :, :]})[0]
    v = np.asarray(out, dtype=np.float64).reshape(-1)
    norm = float(np.linalg.norm(v))
    if not np.isfinite(norm) or norm == 0.0:
        return None
    return tuple(float(x) for x in v / norm)


def embed_pcm(pcm: bytes, root: Path) -> SpeakerEmbedding | None:
    """One embedding for PCM16 audio, unit length, with its fingerprint, or None when
    there is too little of it to tell a voice by."""
    samples = _samples(pcm)
    if samples.size < MIN_SPEECH_S * _RATE:
        return None
    feats = fbank(samples)
    vector = _vector(feats, root)
    if vector is None:
        return None
    return SpeakerEmbedding(vector=vector, model=NAME, fingerprint=fingerprint(feats))


def check_phrase(pcm: bytes, root: Path) -> tuple[SpeakerEmbedding | None, str | None]:
    """An enrolment phrase: its embedding, or the problem with it ("quiet", "short",
    "loud", "voices"). The two halves of a long enough phrase are compared, so two
    people taking turns are caught."""
    import numpy as np

    from trillion.voice.speaker import voices

    samples = _samples(pcm)
    if samples.size < ENROL_SPEECH_S * _RATE:
        return None, "short"
    frames = samples[: samples.size - samples.size % 160].reshape(-1, 160)
    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1))
    voiced = rms > QUIET_RMS / 3
    if float(np.percentile(rms, 90)) < QUIET_RMS:
        return None, "quiet"
    if voiced.sum() / 100 < ENROL_SPEECH_S:
        return None, "short"
    if float((np.abs(samples) >= 32700).mean()) > CLIPPED:
        return None, "loud"
    whole = embed_pcm(pcm, root)
    if whole is None:
        return None, "short"
    half = samples.size // 2
    if half >= 1.5 * _RATE:
        a = _vector(fbank(samples[:half]), root)
        b = _vector(fbank(samples[half:]), root)
        if a is not None and b is not None and voices((a, b)):
            return None, "voices"
    return whole, None


class LocalSpeaker:
    """The SpeakerEmbedder behind the VOI-15 seam, on this computer."""

    name = NAME

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or folder()

    async def embed(
        self, pcm: bytes, *, start_s: float, words: tuple[Word, ...]
    ) -> SpeakerEmbedding | None:
        cut = speech_span(pcm, start_s, words)
        return await asyncio.to_thread(embed_pcm, cut, self._root)

    async def phrase(self, pcm: bytes) -> tuple[SpeakerEmbedding | None, str | None]:
        return await asyncio.to_thread(check_phrase, pcm, self._root)


def embedder(enrolled: bool, root: Path | None = None) -> SpeakerEmbedder:
    """What new listeners ask who spoke: this model once a voice is enrolled and the
    model is here, else the stub. Nobody enrolled, nothing is computed per turn."""
    return LocalSpeaker(root) if enrolled and ready(root) else UnknownSpeaker()
