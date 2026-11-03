"""When a listening cue may play (PER-11, VOI-12): only in a real pause mid-thought,
sparingly, and never the same one twice in a row."""

from __future__ import annotations

import random
from itertools import pairwise
from typing import Any

from trillion.voice.cues import (
    CUE_GAP_S,
    CUE_MAX,
    CUES,
    GO_ON_AFTER_S,
    MAX_CONFIDENCE,
    MIN_SPEECH_S,
    PAUSE_MAX_S,
    PAUSE_MIN_S,
    CuePlan,
)


class Always(random.Random):
    """Every coin lands yes; the pick after it is still the seeded one. Each cue asks
    for the coin first, then the pick."""

    flips = 0

    def random(self) -> float:
        self.flips += 1
        return 0.0 if self.flips % 2 else super().random()


class Never(random.Random):
    def random(self) -> float:
        return 0.999


# A pause that should get a cue: 6 s said, the last word 0.8 s ago, sure it goes on.
GOOD: dict[str, Any] = {
    "now_s": 10.0,
    "heard_s": 6.0,
    "last_word_end_s": 9.2,
    "confidence": 0.1,
    "eager_since_word": False,
    "last_words": "and then I was thinking about the launch",
}


def plan(rng: random.Random | None = None) -> CuePlan:
    return CuePlan(rng or Always(7))


def test_the_constants_are_the_spec() -> None:
    assert dict(CUES) == {"mm-hm": 0.4, "mm": 0.3, "yeah": 0.2, "go on": 0.1}
    assert (MIN_SPEECH_S, PAUSE_MIN_S, PAUSE_MAX_S) == (4.0, 0.5, 1.0)
    assert (MAX_CONFIDENCE, CUE_GAP_S, CUE_MAX, GO_ON_AFTER_S) == (0.3, 8.0, 3, 12.0)


def test_all_met_plays_one() -> None:
    assert plan().consider(**GOOD) in ("mm-hm", "mm", "yeah")


def test_each_condition_alone_blocks_a_cue() -> None:
    blockers: list[dict[str, Any]] = [
        {"heard_s": MIN_SPEECH_S - 0.1},
        {"last_word_end_s": GOOD["now_s"] - PAUSE_MIN_S + 0.1},  # still talking
        {"last_word_end_s": GOOD["now_s"] - PAUSE_MAX_S - 0.1},  # a stop, not a pause
        {"last_word_end_s": None},
        {"confidence": MAX_CONFIDENCE},
        {"confidence": None},
        {"eager_since_word": True},
        {"last_words": "what do you think?"},
        {"last_words": "okay thanks"},
        {"last_words": "that's all, thank you."},
        {"last_words": ""},
    ]
    for change in blockers:
        assert plan().consider(**{**GOOD, **change}) is None, change
    assert plan(Never(1)).consider(**GOOD) is None


def test_a_rising_confidence_under_the_bar_still_cues() -> None:
    """Flux reports every quarter second and grows surer through any pause: below the
    bar that is still a pause. The live test played no cue at all."""
    p = plan()
    assert p.consider(**{**GOOD, "now_s": 9.6, "confidence": 0.05}) is None  # too soon
    assert p.consider(**{**GOOD, "confidence": 0.2}) is not None


def test_each_refusal_is_counted_by_its_first_reason() -> None:
    cases: list[tuple[dict[str, Any], str]] = [
        ({"last_word_end_s": None}, "no_words"),
        ({"heard_s": MIN_SPEECH_S - 0.1}, "short"),
        ({"last_word_end_s": GOOD["now_s"] - PAUSE_MIN_S + 0.1}, "pause"),
        ({"confidence": MAX_CONFIDENCE}, "confident"),
        ({"confidence": None}, "confident"),
        ({"eager_since_word": True}, "eager"),
        ({"last_words": "what do you think?"}, "ends"),
        # Two at once: only the first is counted.
        ({"heard_s": 1.0, "eager_since_word": True}, "short"),
    ]
    for change, reason in cases:
        p = plan()
        assert p.consider(**{**GOOD, **change}) is None
        assert p.skipped == {reason: 1}, change
    p = plan(Never(1))
    assert p.consider(**GOOD) is None and p.skipped == {"coin": 1}
    assert p.consider(**{**GOOD, "now_s": 10.1}) is None
    assert p.skipped == {"coin": 1, "decided": 1}


def test_gap_and_max_are_counted() -> None:
    p = plan()
    assert p.consider(**at(10.0, 6.0)) is not None and not p.skipped
    assert p.consider(**at(12.0, 8.0)) is None and p.skipped == {"gap": 1}
    p.count = CUE_MAX
    assert p.consider(**at(30.0, 26.0)) is None and p.skipped["max"] == 1


def test_one_flip_per_pause() -> None:
    """Flux reports every quarter second: a pause gets one chance, not four."""
    p = plan(Never(1))
    assert p.consider(**GOOD) is None
    p._rng = Always(1)
    assert p.consider(**{**GOOD, "now_s": 10.3}) is None  # the same pause
    later = {**GOOD, "now_s": 14.0, "last_word_end_s": 13.2, "heard_s": 9.0}
    assert p.consider(**later) is not None  # a new pause


def at(t: float, heard: float) -> dict[str, Any]:
    return {**GOOD, "now_s": t, "last_word_end_s": t - 0.8, "heard_s": heard}


def test_never_two_within_the_gap_and_at_most_three_a_turn() -> None:
    p = plan()
    assert p.consider(**at(10.0, 6.0)) is not None
    assert p.consider(**at(10.0 + CUE_GAP_S - 0.5, 13.0)) is None
    assert p.consider(**at(10.0 + CUE_GAP_S, 14.0)) is not None
    assert p.consider(**at(10.0 + 2 * CUE_GAP_S, 20.0)) is not None
    assert p.count == CUE_MAX
    assert p.consider(**at(10.0 + 3 * CUE_GAP_S, 30.0)) is None


def test_never_the_same_twice_in_a_row_and_go_on_only_late() -> None:
    for seed in range(200):
        p = CuePlan(Always(seed))
        said: list[str] = []
        for n in range(CUE_MAX):
            heard = 5.0 + n * 9.0
            cue = p.consider(**at(10.0 + n * CUE_GAP_S, heard))
            assert cue is not None
            if heard < GO_ON_AFTER_S:
                assert cue != "go on"
            said.append(cue)
        assert all(a != b for a, b in pairwise(said)), said


def test_the_pick_is_weighted() -> None:
    rng = random.Random(3)  # noqa: S311 - a test's seed
    counts = dict.fromkeys(dict(CUES), 0)
    for _ in range(4000):
        p = CuePlan(Always(rng.randrange(1 << 30)))
        cue = p.consider(**{**GOOD, "heard_s": 20.0})
        assert cue is not None
        counts[cue] += 1
    assert counts["mm-hm"] > counts["mm"] > counts["yeah"] > counts["go on"] > 0
