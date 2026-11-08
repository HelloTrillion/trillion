"""Whose voice it was (§5.3a: SPN-14a, SPN-15a, SPN-18, PER-13).

One enrolled owner per machine. Enrolment keeps a template (the mean of a few short
phrases' embeddings) in `voiceprint.json` in Trillion's own folder, owner-only (mode
600), which no tool can read (the deny floor guards the folder). It is never logged
and never sent anywhere. Each spoken utterance's embedding (VOI-15) is scored against
it with cosine similarity and judged with the thresholds below.

Pure scoring and one small store. The session asks `judge` once per spoken turn and
tells the spine what it found (`ToolContext.speaker`); the spine decides.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from trillion import owner_only, paths
from trillion.voice.types import SpeakerEmbedding

# ── The thresholds, in one place ─────────────────────────────────────────
#
# Cosine similarity between unit-length embeddings, from -1 to 1, for the WeSpeaker
# ResNet34-LM model (voice/providers/wespeaker.py). How they were chosen: the model
# was run on synthetic voices (six system text-to-speech voices, sentences and single
# words), each scored against a template of three of its own sentences.
#   - The same voice: sentences 0.82-0.91; "Yes, go ahead." 0.55-0.68; "Yes." 0.38-0.62.
#   - Another voice: mean 0.15-0.19; the highest single word 0.49; the highest
#     sentence 0.62, between two voices from one speech engine (the nearest thing to a
#     clone in the set).
# So one word is not enough to tell, and the accept bar sits above every other voice's
# score: an owner's short yes often lands in the band between, where she asks again
# for a longer one ("yes, go ahead"), then for a tap (SPN-15a). Real voices differ more
# than one engine's; a measurement on recorded speech should move these, here only.
ACCEPT = 0.65
# Below this the voice is someone else's: refused, with no second try.
REJECT_FLOOR = 0.40
# The replay check (SPN-14a): the share of loud frames whose fingerprint bits differ,
# at the best alignment. The same recording replayed with gain, noise, a 0.2% speed
# change or a small room's echo differed by 0-0.24; the same voice saying the same
# words again by 0.37-0.44, another voice 0.46, unrelated audio about 0.5.
REPLAY_BITS = 0.30
# A backstop for an embedder without fingerprints: only a digital copy is this close.
REPLAY_COSINE = 0.99
# Accepted utterances kept for the replay check, beside the enrolment phrases.
HEARD_KEPT = 16
# Enrolment: how many phrases make a template, and how far apart they may be.
PHRASES = 3
# Two phrases (or two halves of one) below this are two different voices, or noise.
ENROL_AGREE = 0.45
# Fingerprints overlap by at least this share of the shorter one, at any alignment.
_MIN_OVERLAP = 0.6
_MIN_LOUD = 20
_LOUD = 1 << 16
_BITS = _LOUD - 1

# What the spine is told about one spoken turn (ToolContext.speaker).
Who = Literal["owner", "unsure", "other", "replay"]


@dataclass(frozen=True, kw_only=True)
class Match:
    who: Who
    # The cosine against the template, or None when the utterance could not be scored.
    score: float | None


@dataclass(frozen=True, kw_only=True)
class Seen:
    """Something already heard in the owner's voice: an enrolment phrase or a turn."""

    vector: tuple[float, ...]
    fingerprint: tuple[int, ...] = ()


@dataclass(frozen=True, kw_only=True)
class Profile:
    model: str
    enrolled_ms: int
    template: tuple[float, ...]
    samples: tuple[Seen, ...]
    heard: tuple[Seen, ...] = ()


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return -1.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0 or not math.isfinite(dot):
        return -1.0
    return dot / (na * nb)


def unit(v: Sequence[float]) -> tuple[float, ...]:
    n = math.sqrt(sum(x * x for x in v))
    return tuple(x / n for x in v) if n > 0 else tuple(v)


def mean(vectors: Sequence[Sequence[float]]) -> tuple[float, ...]:
    units = [unit(v) for v in vectors]
    return unit([sum(col) / len(units) for col in zip(*units, strict=True)])


def bits_apart(a: Sequence[int], b: Sequence[int]) -> float:
    """The share of differing fingerprint bits over loud frames, at the best alignment
    of the two. 1.0 when they are too short to compare."""
    if not a or not b:
        return 1.0
    shorter = min(len(a), len(b))
    need = max(1, math.ceil(_MIN_OVERLAP * shorter))
    best = 1.0
    for off in range(-(len(b) - need), len(a) - need + 1):
        a0, b0 = max(0, off), max(0, -off)
        n = min(len(a) - a0, len(b) - b0)
        differ = loud = 0
        for x, y in zip(a[a0 : a0 + n], b[b0 : b0 + n], strict=True):
            if (x | y) & _LOUD:
                loud += 1
                differ += ((x ^ y) & _BITS).bit_count()
        if loud >= _MIN_LOUD:
            best = min(best, differ / (16 * loud))
    return best


def judge(embedding: SpeakerEmbedding | None, profile: Profile | None) -> Match | None:
    """Who said it. None when nobody is enrolled: the session is the anchor (SPN-14).

    An utterance that could not be scored (too short, the embedder failed, another
    model's vector) is "unsure", never "owner": not knowing is not a match. The replay
    check runs on anything that is not plainly someone else."""
    if profile is None:
        return None
    if embedding is None or embedding.model != profile.model:
        return Match(who="unsure", score=None)
    score = cosine(embedding.vector, profile.template)
    if score < REJECT_FLOOR:
        return Match(who="other", score=score)
    if replayed(embedding, profile):
        return Match(who="replay", score=score)
    return Match(who="owner" if score >= ACCEPT else "unsure", score=score)


def replayed(embedding: SpeakerEmbedding, profile: Profile) -> bool:
    """The same sound as something already heard: a recording, not a person."""
    for seen in (*profile.samples, *profile.heard):
        if cosine(embedding.vector, seen.vector) >= REPLAY_COSINE:
            return True
        if embedding.fingerprint and seen.fingerprint:
            if bits_apart(embedding.fingerprint, seen.fingerprint) <= REPLAY_BITS:
                return True
    return False


def remember(profile: Profile, embedding: SpeakerEmbedding) -> Profile:
    """The profile with an accepted utterance added for the replay check."""
    seen = Seen(vector=unit(embedding.vector), fingerprint=embedding.fingerprint)
    return replace(profile, heard=(*profile.heard, seen)[-HEARD_KEPT:])


# ── Enrolment ────────────────────────────────────────────────────────────


def enrol(
    model: str, phrases: Sequence[SpeakerEmbedding], now_ms: int
) -> tuple[Profile | None, int | None]:
    """A profile from the phrases, or None and the index of the phrase that does not
    agree with the others (said by someone else, or mostly noise)."""
    vectors = [unit(p.vector) for p in phrases]
    for i, v in enumerate(vectors):
        others = [w for j, w in enumerate(vectors) if j != i]
        if others and cosine(v, mean(others)) < ENROL_AGREE:
            return None, i
    samples = tuple(
        Seen(vector=v, fingerprint=p.fingerprint) for v, p in zip(vectors, phrases, strict=True)
    )
    return Profile(model=model, enrolled_ms=now_ms, template=mean(vectors), samples=samples), None


def voices(halves: tuple[Sequence[float], Sequence[float]]) -> bool:
    """Whether the two halves of one phrase sound like two people."""
    return cosine(*halves) < ENROL_AGREE


# ── The store: one file, owner-only ──────────────────────────────────────


def _vector(raw: object) -> tuple[float, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("vector")
    out = tuple(float(x) for x in raw)
    if not all(math.isfinite(x) for x in out):
        raise ValueError("vector")
    return out


def _print(raw: object) -> tuple[int, ...]:
    if not isinstance(raw, list) or not all(isinstance(x, int) for x in raw):
        raise ValueError("fingerprint")
    return tuple(raw)


def _seen(raw: object) -> Seen:
    if not isinstance(raw, dict):
        raise ValueError("seen")
    return Seen(vector=_vector(raw.get("v")), fingerprint=_print(raw.get("f", [])))


def _encode(profile: Profile) -> str:
    def seen(s: Seen) -> dict[str, object]:
        return {"v": [round(x, 6) for x in s.vector], "f": list(s.fingerprint)}

    return json.dumps(
        {
            "version": 1,
            "model": profile.model,
            "enrolled_ms": profile.enrolled_ms,
            "template": [round(x, 6) for x in profile.template],
            "samples": [seen(s) for s in profile.samples],
            "heard": [seen(s) for s in profile.heard],
        }
    )


def _decode(text: str) -> Profile:
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("version")
    model, at = raw.get("model"), raw.get("enrolled_ms")
    if not isinstance(model, str) or not isinstance(at, int):
        raise ValueError("fields")
    return Profile(
        model=model,
        enrolled_ms=at,
        template=_vector(raw.get("template")),
        samples=tuple(_seen(s) for s in raw.get("samples") or []),
        heard=tuple(_seen(h) for h in raw.get("heard") or [])[-HEARD_KEPT:],
    )


class Voiceprints:
    """The enrolled profile, read once and kept, written owner-only.

    The only reader and writer of `voiceprint.json`. A file that cannot be read is
    treated as nobody enrolled, so the gate falls back to the session (SPN-14), the
    Phase 0 guarantee, never to something weaker."""

    def __init__(self, path: Callable[[], Path] | None = None) -> None:
        self._path = path or (lambda: paths.voiceprint_path())
        self._lock = threading.Lock()
        self._cached: Profile | None = None
        self._read = False

    def get(self) -> Profile | None:
        with self._lock:
            if not self._read:
                self._cached = self._load()
                self._read = True
            return self._cached

    def _load(self) -> Profile | None:
        try:
            return _decode(self._path().read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None

    def save(self, profile: Profile) -> None:
        with self._lock:
            _write_private(self._path(), _encode(profile))
            self._cached, self._read = profile, True

    def heard(self, embedding: SpeakerEmbedding) -> None:
        """Keep an accepted utterance for the replay check."""
        current = self.get()
        if current is not None and embedding.model == current.model:
            self.save(remember(current, embedding))

    def delete(self) -> None:
        """Forget the voice. The file holds only the voiceprint, so it goes."""
        with self._lock:
            with contextlib.suppress(FileNotFoundError):
                self._path().unlink()
            self._cached, self._read = None, True


def _write_private(path: Path, text: str) -> None:
    """Write `text` to `path` readable by its owner only, replacing it whole."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        # Owner-only before a byte of the voice is in it.
        tmp.unlink(missing_ok=True)
        tmp.touch(mode=0o600)
        os.chmod(tmp, 0o600)
        # Windows ignores the mode: give the file an access list of its own (SUR-3c).
        owner_only.restrict(tmp)
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
