"""Whose voice it was (§5.3a): the scoring, the replay and clone fixtures, enrolment
and the voiceprint file. Synthetic embeddings only: no model, no audio."""

# ruff: noqa: S311 - seeded randomness makes synthetic voices, not secrets

from __future__ import annotations

import math
import os
import random
import stat
import sys
from pathlib import Path

import pytest

from trillion.voice import speaker as sp
from trillion.voice.speaker import (
    ACCEPT,
    HEARD_KEPT,
    REJECT_FLOOR,
    REPLAY_BITS,
    Profile,
    Voiceprints,
    bits_apart,
    enrol,
    judge,
    remember,
)
from trillion.voice.types import SpeakerEmbedding

DIM = 256
MODEL = "fake-embedder"


def direction(seed: int) -> tuple[float, ...]:
    rnd = random.Random(seed)
    return sp.unit([rnd.gauss(0, 1) for _ in range(DIM)])


OWNER = direction(1)
STRANGER = direction(2)


def near(base: tuple[float, ...], similarity: float, seed: int) -> tuple[float, ...]:
    """A unit vector whose cosine with `base` is about `similarity`."""
    noise = direction(1000 + seed)
    # Take out noise's part along base, so the mix's angle is exact.
    dot = sum(a * b for a, b in zip(base, noise, strict=True))
    ortho = sp.unit([n - dot * b for n, b in zip(noise, base, strict=True)])
    s = similarity
    return sp.unit([s * b + math.sqrt(1 - s * s) * o for b, o in zip(base, ortho, strict=True)])


def sound(seed: int, frames: int = 80) -> tuple[int, ...]:
    """A fingerprint: 16 random bits a frame, every frame loud."""
    rnd = random.Random(seed)
    return tuple(rnd.getrandbits(16) | (1 << 16) for _ in range(frames))


def played_back(fp: tuple[int, ...], flip: float, shift: int, seed: int) -> tuple[int, ...]:
    """The same sound through a speaker and a room: some bits flipped, a little later."""
    rnd = random.Random(seed)
    out = []
    for v in fp:
        for bit in range(16):
            if rnd.random() < flip:
                v ^= 1 << bit
        out.append(v)
    return (sound(seed + 1, shift) + tuple(out))[: len(fp) + shift]


def said(vector: tuple[float, ...], fp: tuple[int, ...] = ()) -> SpeakerEmbedding:
    return SpeakerEmbedding(vector=vector, model=MODEL, fingerprint=fp)


def enrolled() -> Profile:
    phrases = [said(near(OWNER, 0.9, i), sound(10 + i)) for i in range(3)]
    profile, odd = enrol(MODEL, phrases, now_ms=1)
    assert profile is not None and odd is None
    return profile


def test_the_thresholds_are_ordered() -> None:
    assert -1 < REJECT_FLOOR < ACCEPT < sp.REPLAY_COSINE <= 1
    assert 0 < REPLAY_BITS < 0.5  # unrelated sound differs in about half its bits


def test_nobody_enrolled_judges_nothing() -> None:
    assert judge(said(OWNER), None) is None


def test_the_owner_is_accepted() -> None:
    m = judge(said(near(OWNER, 0.85, 50), sound(50)), enrolled())
    assert m is not None and m.who == "owner" and m.score is not None and m.score >= ACCEPT


def test_another_voice_is_someone_else() -> None:
    m = judge(said(STRANGER, sound(51)), enrolled())
    assert m is not None and m.who == "other"


def test_between_the_thresholds_is_unsure() -> None:
    m = judge(said(near(OWNER, 0.55, 52), sound(52)), enrolled())
    assert m is not None and m.who == "unsure"


def test_not_scored_is_unsure_never_the_owner() -> None:
    profile = enrolled()
    assert judge(None, profile) == sp.Match(who="unsure", score=None)
    other_model = SpeakerEmbedding(vector=OWNER, model="another-model")
    assert judge(other_model, profile) == sp.Match(who="unsure", score=None)


# ── SPN-14a's fixtures ───────────────────────────────────────────────────


def test_fixture_a_recording_of_the_owners_earlier_yes_fails() -> None:
    # The owner says yes, and it is accepted and kept.
    yes = said(near(OWNER, 0.8, 60), sound(60))
    profile = enrolled()
    first = judge(yes, profile)
    assert first is not None and first.who == "owner"
    profile = remember(profile, yes)
    # Someone plays that yes back later: the voice still matches, the sound is the same.
    replay = said(near(yes.vector, 0.95, 61), played_back(yes.fingerprint, 0.12, 4, 61))
    again = judge(replay, profile)
    assert again is not None and again.who == "replay"


def test_fixture_an_enrolment_phrase_played_back_fails() -> None:
    profile = enrolled()
    phrase = profile.samples[0]
    replay = said(near(phrase.vector, 0.95, 62), played_back(phrase.fingerprint, 0.1, 0, 62))
    m = judge(replay, profile)
    assert m is not None and m.who == "replay"


def test_fixture_a_digital_copy_fails_without_a_fingerprint() -> None:
    profile = remember(enrolled(), said(near(OWNER, 0.8, 63)))
    m = judge(said(profile.heard[-1].vector), profile)
    assert m is not None and m.who == "replay"


def test_the_owner_saying_yes_again_is_not_a_replay() -> None:
    yes = said(near(OWNER, 0.8, 64), sound(64))
    profile = remember(enrolled(), yes)
    again = said(near(OWNER, 0.8, 65), sound(65))
    m = judge(again, profile)
    assert m is not None and m.who == "owner"


def test_fixture_a_synthetic_clone_that_misses_the_voice_fails() -> None:
    # A text-to-speech clone: near the owner, but not as near as the owner. What this
    # fixture proves is the threshold, not liveness: a clone that scores like the owner
    # would pass (the spec's "What is not protected").
    for similarity, expected in ((0.6, "unsure"), (0.3, "other")):
        m = judge(said(near(OWNER, similarity, 66), sound(66)), enrolled())
        assert m is not None and m.who == expected


def test_bits_apart() -> None:
    a = sound(70)
    assert bits_apart(a, a) == 0.0
    assert bits_apart(a, played_back(a, 0.1, 6, 70)) < REPLAY_BITS
    assert bits_apart(a, sound(71)) > 0.4
    assert bits_apart((), a) == 1.0
    # Silence never matches: frames that are quiet on both sides are not counted.
    quiet = tuple(v & 0xFFFF for v in a)
    assert bits_apart(quiet, quiet) == 1.0


# ── Enrolment ────────────────────────────────────────────────────────────


def test_a_phrase_in_another_voice_spoils_enrolment() -> None:
    phrases = [said(near(OWNER, 0.9, 80)), said(STRANGER), said(near(OWNER, 0.9, 81))]
    profile, odd = enrol(MODEL, phrases, now_ms=1)
    assert profile is None and odd == 1


def test_two_halves_in_two_voices() -> None:
    assert sp.voices((OWNER, STRANGER))
    assert not sp.voices((near(OWNER, 0.9, 82), near(OWNER, 0.9, 83)))


def test_heard_is_bounded() -> None:
    profile = enrolled()
    for i in range(HEARD_KEPT + 5):
        profile = remember(profile, said(near(OWNER, 0.8, 100 + i)))
    assert len(profile.heard) == HEARD_KEPT


# ── The voiceprint file ──────────────────────────────────────────────────


def test_saved_owner_only_read_back_and_deleted(tmp_path: Path) -> None:
    path = tmp_path / "home" / "voiceprint.json"
    prints = Voiceprints(lambda: path)
    assert prints.get() is None
    profile = enrolled()
    prints.save(profile)
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    else:  # an access list of its own: nobody else is let in (SUR-3c)
        from trillion import owner_only

        assert owner_only.exposed(path) == []
    again = Voiceprints(lambda: path).get()
    assert again is not None
    assert again.model == MODEL and len(again.samples) == 3
    assert sp.cosine(again.template, profile.template) > 0.9999
    assert again.samples[0].fingerprint == profile.samples[0].fingerprint
    prints.heard(said(near(OWNER, 0.8, 90), sound(90)))
    assert len(Voiceprints(lambda: path).get().heard) == 1  # type: ignore[union-attr]
    prints.delete()
    assert not path.exists() and prints.get() is None
    prints.delete()  # twice is fine


def test_another_models_voice_is_not_kept(tmp_path: Path) -> None:
    prints = Voiceprints(lambda: tmp_path / "v.json")
    prints.save(enrolled())
    prints.heard(SpeakerEmbedding(vector=OWNER, model="another"))
    assert prints.get().heard == ()  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"version": 2}',
        '{"version": 1, "model": "m", "enrolled_ms": 1, "template": []}',
        '{"version": 1, "model": "m", "enrolled_ms": 1, "template": [NaN]}',
        '{"version": 1, "model": 3, "enrolled_ms": 1, "template": [1.0]}',
        '{"version": 1, "model": "m", "enrolled_ms": 1, "template": [1.0], "samples": [1]}',
        '{"version": 1, "model": "m", "enrolled_ms": 1, "template": [1.0],'
        ' "samples": [{"v": [1.0], "f": ["x"]}]}',
    ],
)
def test_an_unreadable_file_is_nobody_enrolled(tmp_path: Path, text: str) -> None:
    # The gate then falls back to the session (SPN-14), never to something weaker.
    path = tmp_path / "v.json"
    path.write_text(text)
    assert Voiceprints(lambda: path).get() is None


def test_the_default_path_is_in_trillions_folder(tmp_path: Path) -> None:
    # tests/conftest.py points it at the test's own folder.
    assert Voiceprints().get() is None


def test_cosine_edges() -> None:
    assert sp.cosine((), ()) == -1.0
    assert sp.cosine((1.0,), (1.0, 0.0)) == -1.0
    assert sp.cosine((0.0, 0.0), (1.0, 0.0)) == -1.0
    assert sp.unit((0.0, 0.0)) == (0.0, 0.0)
