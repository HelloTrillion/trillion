"""The local speaker embedder (§5.3a). CI has neither the model nor onnxruntime: what
needs numpy is skipped without it, and nothing here downloads anything."""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from trillion.voice.providers import wespeaker
from trillion.voice.types import UnknownSpeaker, VoiceError, Word

RATE = 16_000


def pcm_of(seconds: float) -> bytes:
    return b"\x01\x00" * int(seconds * RATE)


def test_the_model_is_pinned_and_checked() -> None:
    # An exact revision, a size and a SHA-256: never a moving "main" (ONB-7).
    assert re.search(r"/resolve/[0-9a-f]{40}$", wespeaker.PACK.base_url)
    assert wespeaker.PACK.base_url.startswith("https://huggingface.co/Wespeaker/")
    (f,) = wespeaker.PACK.files
    assert re.fullmatch(r"[0-9a-f]{64}", f.sha256) and f.size > 1_000_000
    assert "CC BY 4.0" in wespeaker.CREDIT


def test_speech_span_cuts_to_the_users_words() -> None:
    pcm = pcm_of(10.0)
    words = (Word(text="yes", start_s=12.0, end_s=12.5),)
    cut = wespeaker.speech_span(pcm, 10.0, words)
    # 2.0 - 0.15 to 2.5 + 0.15 seconds into the audio.
    assert abs(len(cut) - 0.8 * RATE * 2) <= 4
    assert wespeaker.speech_span(pcm, 10.0, ()) == pcm
    # Words outside the audio: all of it, never nothing.
    assert wespeaker.speech_span(pcm, 0.0, (Word(text="x", start_s=50, end_s=51),)) == pcm


def test_nobody_enrolled_computes_nothing(tmp_path: Path) -> None:
    assert isinstance(wespeaker.embedder(False, tmp_path), UnknownSpeaker)


def test_without_the_model_the_stub_stays(tmp_path: Path) -> None:
    # Enrolled, but the model is not on disk: the stub, so every voice is "unsure".
    assert isinstance(wespeaker.embedder(True, tmp_path), UnknownSpeaker)


def test_with_the_model_the_local_embedder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wespeaker, "ready", lambda root=None: True)
    chosen = wespeaker.embedder(True, tmp_path)
    assert isinstance(chosen, wespeaker.LocalSpeaker) and chosen.name == wespeaker.NAME


async def test_download_needs_the_optional_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wespeaker, "available", lambda: False)
    with pytest.raises(VoiceError) as e:
        await wespeaker.prepare(Path("unused"))
    assert e.value.kind == "not_installed"
    assert not wespeaker.ready(Path("unused"))


def test_the_folder_is_under_models(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path))
    assert wespeaker.folder() == tmp_path / "models" / "speaker"


# ── With numpy (installed with `--extra speaker`) ─────────────────────────


def tone(seconds: float, hz: float, amp: float = 3000.0) -> list[float]:
    return [amp * math.sin(2 * math.pi * hz * i / RATE) for i in range(int(seconds * RATE))]


def test_fbank_shape_and_normalized() -> None:
    np = pytest.importorskip("numpy")
    feats = wespeaker.fbank(np.array(tone(1.0, 440.0)))
    assert feats.shape == (1 + (RATE - 400) // 160, 80)
    assert abs(float(feats.mean(axis=0).max())) < 1e-3
    assert wespeaker.fbank(np.zeros(100)).shape == (0, 80)


def test_fingerprint_survives_gain_but_not_other_sound() -> None:
    np = pytest.importorskip("numpy")
    rnd = np.random.default_rng(0)
    voice = rnd.normal(0, 2000, RATE * 2) * np.sin(np.linspace(0, 30, RATE * 2)) ** 2
    a = wespeaker.fingerprint(wespeaker.fbank(voice))
    b = wespeaker.fingerprint(wespeaker.fbank(voice * 0.5 + rnd.normal(0, 20, voice.size)))
    c = wespeaker.fingerprint(wespeaker.fbank(rnd.normal(0, 2000, RATE * 2)))
    from trillion.voice.speaker import REPLAY_BITS, bits_apart

    assert len(a) <= wespeaker.PRINT_FRAMES
    assert bits_apart(a, b) < REPLAY_BITS < bits_apart(a, c)


def test_a_phrase_too_short_or_too_quiet(tmp_path: Path) -> None:
    np = pytest.importorskip("numpy")
    short = (np.array(tone(0.5, 200.0))).astype(np.int16).tobytes()
    assert wespeaker.check_phrase(short, tmp_path) == (None, "short")
    quiet = (np.array(tone(3.0, 200.0, amp=50.0))).astype(np.int16).tobytes()
    assert wespeaker.check_phrase(quiet, tmp_path) == (None, "quiet")
    loud = np.clip(np.array(tone(3.0, 200.0, amp=60000.0)), -32768, 32767)
    assert wespeaker.check_phrase(loud.astype(np.int16).tobytes(), tmp_path) == (None, "loud")
