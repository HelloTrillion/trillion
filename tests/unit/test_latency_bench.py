"""The latency bench's own arithmetic (EVL-3): where speech ends, where sound starts."""

from __future__ import annotations

from array import array

from evals.latency.run import RATE, last_sound_s, loud_samples


def pcm(*samples: int) -> bytes:
    return array("h", samples).tobytes()


def test_quiet_samples_are_not_sound() -> None:
    assert loud_samples(pcm(0, 10, -300, 700, -9000, 5)) == [3, 4]
    assert loud_samples(pcm(0, 1, -2)) == []
    assert loud_samples(b"\x01") == []  # half a sample


def test_speech_ends_at_its_last_loud_sample() -> None:
    speech = pcm(*([0] * 100 + [8000] * RATE + [0] * RATE))
    assert last_sound_s(speech) == (100 + RATE) / RATE
    assert last_sound_s(pcm(0, 0)) == 0.0
