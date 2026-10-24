"""Her playback placed on the recognizer's clock (#102)."""

from __future__ import annotations

from trillion.voice.timeline import FINAL_CAP_S, LEAD_S, TAIL_S, WINDOW_S, Timeline
from trillion.voice.types import Word


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


def test_her_level_matches_window_by_window() -> None:
    tl = Timeline()
    tl.sentence(1, written="a", spoken="a", levels=[i / 100 for i in range(50)])
    tl.sentence(2, written="b", spoken="b", levels=[0.5 - i / 100 for i in range(25)])
    tl.start(1, at_s=10.0)
    tl.start(2, at_s=11.3)

    def walked(t0: float, t1: float) -> float:
        got = [
            level
            for s in tl.sentences.values()
            if s.start_s is not None
            for i, level in enumerate(s.levels)
            if s.start_s + i * WINDOW_S + WINDOW_S > t0 and s.start_s + i * WINDOW_S < t1
        ]
        return sum(got) / len(got) if got else 0.0

    for t0, t1 in [
        (9.0, 9.5),
        (9.9, 10.05),
        (10.013, 10.047),
        (10.5, 11.5),
        (11.0, 11.3),
        (11.31, 11.9),
        (9.0, 13.0),
        (10.2, 10.2),
    ]:
        assert abs(tl.her_level(t0, t1) - walked(t0, t1)) < 1e-9, (t0, t1)


def test_a_later_reported_sentence_places_past_an_unreported_one() -> None:
    tl = _tl()
    tl.sentence(3, written="See you.", spoken="See you.", levels=[0.1] * 25)
    tl.start(1, at_s=10.0)
    tl.start(3, at_s=12.0)
    tl.stop()
    assert _seq(tl, 11.5) is None  # 2 was never reported played
    assert _seq(tl, 12.2) == 3


def test_a_first_sentence_reported_late_still_places_the_next() -> None:
    tl = _tl()  # no watch opened, sentence 1 not reported
    tl.start(2, at_s=11.0)
    assert _seq(tl, 11.2) == 2


def test_her_end_once_stopped_is_the_end_of_what_played() -> None:
    tl = _tl()
    tl.start(1, at_s=10.0)
    assert tl.ended_at() is None
    tl.stop()
    end = tl.ended_at()
    assert end is not None and abs(end - (11.0 + TAIL_S)) < 1e-9
    assert tl.ended_final(now_s=11.0) == end
    nothing = _tl()
    nothing.stop()
    assert nothing.ended_at() is None and nothing.ended_final(now_s=99.0) is None


def test_her_end_is_final_once_her_last_sentence_was_reported_played() -> None:
    tl = _tl()
    tl.start(1, at_s=10.0)
    tl.finish()
    estimate = 11.5 + TAIL_S  # sentence 2 assumed right after sentence 1
    end = tl.ended_at()
    assert end is not None and abs(end - estimate) < 1e-9
    assert tl.ended_final(now_s=estimate) is None  # the page may be playing behind
    tl.start(2, at_s=11.4)
    final = tl.ended_final(now_s=11.5)
    assert final is not None and abs(final - (11.9 + TAIL_S)) < 1e-9


def test_an_estimated_end_turns_final_once_it_is_old() -> None:
    tl = _tl()
    tl.start(1, at_s=10.0)
    tl.finish()
    end = tl.ended_at()
    assert end is not None
    assert tl.ended_final(now_s=end + FINAL_CAP_S - 0.01) is None
    assert tl.ended_final(now_s=end + FINAL_CAP_S) == end


def test_a_new_watch_forgets_starts_on_the_old_clock() -> None:
    tl = Timeline()
    tl.sentence(1, written="One.", spoken="One.", levels=[0.1] * 50)
    tl.sentence(2, written="Two.", spoken="Two.", levels=[0.1] * 50)
    tl.start(1, at_s=7.0)
    tl.begin(at_s=0.0)  # a new listener: its clock starts again
    assert tl.spans() == [(0.0, 1.0), (1.0, 2.0)]
    tl.start(2, at_s=1.5)
    assert tl.spans() == [(0.0, 1.0), (1.5, 2.5)]


def test_a_sentence_not_reported_cannot_start_before_its_audio_was_sent() -> None:
    """Review I1: a watch opened while she thinks; her audio comes three seconds later.
    The user's words in between are not placed inside her sentence."""
    tl = Timeline()
    tl.begin(at_s=0.0)
    tl.sentence(
        1,
        written="Sure, I can do that.",
        spoken="Sure, I can do that.",
        levels=[0.1] * 50,
        at_s=3.0,
    )
    assert tl.spans() == [(3.0, 4.0)]
    assert _seq(tl, 2.0) is None and _seq(tl, 3.5) == 1


def test_stopped_her_sentence_ends_where_she_was_stopped() -> None:
    """Review I2: what she never got to say has no span."""
    tl = Timeline()
    tl.sentence(1, written="Should I send it?", spoken="Should I send it?", levels=[0.1] * 300)
    tl.start(1, at_s=1.0)
    tl.finish()
    tl.stop(at_s=2.5)
    assert tl.spans() == [(1.0, 2.5)]
    assert tl.ended_at() == 2.5 + TAIL_S
    assert _seq(tl, 2.5 + TAIL_S + 0.01) is None
    # What the page had scheduled still plays for a moment.
    assert tl.her_level(2.6, 2.8) > 0.0


def test_a_sentence_under_way_when_she_is_stopped_keeps_its_span() -> None:
    tl = _tl()
    tl.start(1, at_s=10.0)
    tl.stop(at_s=11.2)  # sentence 2 assumed under way from 11.0, never reported
    assert tl.spans() == [(10.0, 11.0), (11.0, 11.2)]


def test_a_watch_opened_mid_reply_places_the_sentence_playing() -> None:
    """Review, deviation 4: sentences over before the watch have no span."""
    tl = _tl()
    tl.begin(at_s=0.0, playing=(2, 0.2))
    assert tl.spans() == [(-0.2, 0.3)]


def test_how_far_her_words_are_heard_from_the_reports_is_measured() -> None:
    """Reports half a second late: her words are heard 0.5 s before where she is placed."""
    tl = Timeline()
    tl.sentence(
        1,
        written="Alright, properly this time.",
        spoken="Alright, properly this time.",
        levels=[0.1] * 100,
    )
    tl.start(1, at_s=1.5)  # truly playing from 1.0 to 3.0
    assert tl.offset_s() is None
    for i, w in enumerate(("Alright", "properly", "this", "time")):
        at = 1.0 + 2.0 * (i + 0.5) / 4
        tl.align(Word(text=w, start_s=at - 0.1, end_s=at + 0.1))
    offset = tl.offset_s()
    assert offset is not None and abs(offset + 0.5) < 1e-9
