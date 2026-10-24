"""Her playback placed on the recognizer's clock (#102)."""

from __future__ import annotations

from trillion.voice.timeline import LEAD_S, TAIL_S, Timeline


def _tl() -> Timeline:
    tl = Timeline()
    tl.sentence(1, written="We leave at six.", spoken="We leave at six.", levels=[0.2] * 50)  # 1 s
    tl.sentence(2, written="Bring your ticket.", spoken="Bring your ticket.", levels=[0.1] * 25)
    return tl


def _seq(tl: Timeline, t: float) -> int | None:
    s = tl.at(t)
    return None if s is None else s.seq


def test_sentences_sit_where_they_were_reported_played() -> None:
    tl = _tl()
    tl.start(1, at_s=10.0)
    tl.start(2, at_s=11.2)
    assert _seq(tl, 10.5) == 1
    assert _seq(tl, 11.1) is not None  # between them, inside her playback
    assert _seq(tl, 11.4) == 2
    assert _seq(tl, 11.7 + TAIL_S + 0.01) is None
    assert _seq(tl, 10.0 - LEAD_S - 0.01) is None


def test_her_level_over_a_span() -> None:
    tl = _tl()
    tl.start(1, at_s=10.0)
    assert abs(tl.her_level(10.2, 10.4) - 0.2) < 1e-9
    assert tl.her_level(20.0, 20.5) == 0.0


def test_a_sentence_not_reported_yet_follows_the_one_before() -> None:
    tl = _tl()
    tl.start(1, at_s=10.0)
    # Sentence 2 sent but its report hasn't arrived: assumed right after sentence 1.
    assert _seq(tl, 11.2) == 2


def test_her_first_sentence_before_any_report_starts_at_the_watch() -> None:
    tl = _tl()
    tl.begin(at_s=4.0)
    assert _seq(tl, 4.3) == 1


def test_more_audio_for_a_known_sentence_makes_it_longer() -> None:
    tl = _tl()
    tl.sentence(1, written="We leave at six.", spoken="We leave at six.", levels=[0.2] * 50)
    tl.start(1, at_s=10.0)
    assert _seq(tl, 11.9) == 1


def test_her_end() -> None:
    tl = _tl()
    tl.start(1, at_s=10.0)
    tl.start(2, at_s=11.0)
    assert tl.ended_at() is None
    tl.finish()
    end = tl.ended_at()
    assert end is not None and abs(end - (11.5 + TAIL_S)) < 1e-9


def test_a_sentence_never_played_has_no_span_once_she_is_stopped() -> None:
    tl = _tl()
    tl.start(1, at_s=10.0)
    assert _seq(tl, 11.4) == 2  # assumed to follow sentence 1
    tl.stop()
    assert _seq(tl, 11.4) is None
