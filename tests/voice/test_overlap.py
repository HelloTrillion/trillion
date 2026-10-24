"""Talking over her (VOI-9): an acknowledgement lets her carry on, anything else stops her."""

import pytest

from trillion.voice.overlap import (
    ACK_AFTER_S,
    CONFIRM_S,
    DECIDE_BY_S,
    confirm,
    decide,
    is_filler,
    words_of,
)

PLAYING = ("We could leave at six.", "Or if traffic is bad, at half past.")


@pytest.mark.parametrize(
    "words",
    [
        "yeah",
        "mm-hm",
        "mm-hmm",
        "Mm-hmm.",
        "mmhmm",
        "mhm",
        "Right.",
        "okay",
        "got it",
        "i see",
        "uh-huh yeah",
        "Uh-huh.",
        "uh huh",
        "uh huh yeah",
    ],
)
def test_acknowledgements_carry_on_once_done_or_after_a_moment(words: str) -> None:
    assert decide(words, done=False, elapsed_s=0.3, playing=PLAYING) == "wait"
    assert decide(words, done=True, elapsed_s=0.3, playing=PLAYING) == "carry_on"
    assert decide(words, done=False, elapsed_s=ACK_AFTER_S, playing=PLAYING) == "carry_on"


@pytest.mark.parametrize(
    "words",
    [
        "wait",
        "no",
        "stop",
        "yeah but",
        "actually",
        "tell me more",
        "forgot it",
        "got it wrong",
        "i seem lost",
        "uh huh no",
    ],
)
def test_anything_else_interrupts_at_once(words: str) -> None:
    assert decide(words, done=False, elapsed_s=0.1, playing=PLAYING) == "interrupt"


def test_four_acknowledgements_are_more_than_an_acknowledgement() -> None:
    assert decide("yeah yeah yeah yeah", done=True, elapsed_s=0.5, playing=PLAYING) == "interrupt"
    assert decide("yeah yeah yeah", done=True, elapsed_s=0.5, playing=PLAYING) == "carry_on"
    assert decide("uh huh uh huh uh huh uh huh", done=True, elapsed_s=0.5, playing=PLAYING) == (
        "interrupt"
    )


def test_no_words_carry_on_only_at_the_limit() -> None:
    assert decide("", done=False, elapsed_s=0.5, playing=PLAYING) == "wait"
    assert decide("", done=False, elapsed_s=DECIDE_BY_S, playing=PLAYING) == "carry_on"


def test_her_own_voice_coming_back_is_ignored() -> None:
    assert decide("traffic is bad", done=False, elapsed_s=0.4, playing=PLAYING) == "wait"
    assert decide("leave at six", done=True, elapsed_s=0.4, playing=PLAYING) == "wait"
    assert decide("leave at six", done=False, elapsed_s=DECIDE_BY_S, playing=PLAYING) == "carry_on"


def test_an_acknowledgement_she_also_said_still_ends_in_carry_on() -> None:
    playing = ("Okay, we could leave at six.",)
    assert decide("okay", done=False, elapsed_s=DECIDE_BY_S, playing=playing) == "carry_on"


def test_a_stop_word_is_never_taken_for_echo() -> None:
    playing = ("You can stop the timer any time.",)
    assert decide("stop", done=False, elapsed_s=0.1, playing=playing) == "interrupt"
    assert decide("no", done=False, elapsed_s=0.1, playing=("No, the train.",)) == "interrupt"


def test_a_growing_transcript_changes_its_answer() -> None:
    assert decide("yeah", done=False, elapsed_s=0.3, playing=PLAYING) == "wait"
    assert decide("yeah but", done=False, elapsed_s=0.6, playing=PLAYING) == "interrupt"


def test_nothing_playing_means_nothing_is_echo() -> None:
    assert decide("six", done=False, elapsed_s=0.1, playing=()) == "interrupt"
    assert decide("yeah", done=True, elapsed_s=0.1, playing=()) == "carry_on"


@pytest.mark.parametrize("words", ["停", "нет", "10", "don't"])
def test_words_in_any_script_or_digits_are_words(words: str) -> None:
    assert decide(words, done=False, elapsed_s=0.1, playing=PLAYING) == "interrupt"


def test_another_language_interrupts() -> None:
    assert decide("oui", done=True, elapsed_s=0.3, playing=PLAYING) == "interrupt"


def test_curly_and_straight_apostrophes_match() -> None:
    playing = ("We don\u2019t need to leave yet.",)
    assert decide("don't need", done=False, elapsed_s=0.4, playing=playing) == "wait"


# ── Review of VOI-9: numbers, fillers ──


def test_fillers_let_her_carry_on() -> None:
    for words in ("um", "uh", "hmm", "erm", "uh um"):
        assert decide(words, done=True, elapsed_s=0.3, playing=PLAYING) == "carry_on"


# ── Duck, then confirm: their words, once her echo is told apart ──


def test_one_word_of_theirs_alone_waits_then_carries_on() -> None:
    assert confirm("tuesday", "", done=False, elapsed_s=0.3) == "wait"
    assert confirm("tuesday", "", done=True, elapsed_s=0.5) == "wait"
    assert confirm("tuesday", "", done=False, elapsed_s=CONFIRM_S) == "carry_on"
    assert confirm("", "", done=False, elapsed_s=0.3) == "wait"
    assert confirm("", "", done=True, elapsed_s=CONFIRM_S) == "carry_on"


def test_new_words_of_theirs_after_the_duck_stop_her() -> None:
    assert confirm("what", "about tuesday", done=False, elapsed_s=0.4) == "interrupt"


def test_a_stop_word_stops_her_at_once() -> None:
    assert confirm("wait what", "", done=False, elapsed_s=0.1) == "interrupt"
    assert confirm("tuesday", "stop", done=False, elapsed_s=0.2) == "interrupt"


def test_acknowledgements_carry_on_as_before() -> None:
    assert confirm("yeah sure", "", done=True, elapsed_s=0.3) == "carry_on"
    assert confirm("yeah sure", "", done=False, elapsed_s=0.3) == "wait"
    assert confirm("um uh", "", done=True, elapsed_s=0.3) == "carry_on"
    assert confirm("yeah yeah", "yeah yeah", done=True, elapsed_s=0.3) == "interrupt"


@pytest.mark.parametrize(
    ("cand", "after", "done"),
    [
        ("what about", "tuesday", True),
        ("what about", "tuesday", False),  # three words of theirs need no end
        ("skip that", "", True),
        ("go back", "", True),
        ("tell me", "more", True),
        ("what time does the last", "bus", False),  # more after the duck still counts
        ("what about the bus instead", "", True),
    ],
)
def test_short_real_cut_ins_stop_her(cand: str, after: str, done: bool) -> None:
    assert confirm(cand, after, done=done, elapsed_s=1.1) == "interrupt"


def test_two_words_of_theirs_not_yet_ended_wait_then_carry_on() -> None:
    assert confirm("skip that", "", done=False, elapsed_s=0.5) == "wait"
    assert confirm("skip that", "", done=False, elapsed_s=1.1) == "carry_on"


def test_numbers_and_years_are_spelled_out() -> None:
    assert words_of("It costs $5 and starts at 10:30.") == (
        "it costs dollars five and starts at ten thirty".split()
    )
    assert words_of("3pm") == ["three", "pm"]
    assert words_of("2026") == ["twenty", "twenty", "six"]
    assert words_of("twenty twenty-six") == ["twenty", "twenty", "six"]
    assert words_of("2005") == ["two", "thousand", "five"]
    assert words_of("No, the TRAIN.") == ["no", "the", "train"]


def test_fillers_are_known() -> None:
    assert all(is_filler(w) for w in ("um", "uh", "hmm", "erm"))
    assert not is_filler("yeah") and not is_filler("stop")
