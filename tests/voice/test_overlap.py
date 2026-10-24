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


# ── Duck, then confirm: a candidate alone never stops her ──

ALRIGHT = ("Alright, properly this time.", "We start with the basics.")


def test_a_misheard_fragment_of_hers_alone_does_not_stop_her() -> None:
    """Live: her "Alright, properly this time." came back as "or writes properly this"."""
    cand = "or writes properly this"
    assert confirm(cand, "", done=False, elapsed_s=0.3, playing=ALRIGHT) == "wait"
    # Ended by the recognizer, it is still half hers: not enough.
    assert confirm(cand, "", done=True, elapsed_s=0.5, playing=ALRIGHT) == "wait"
    assert confirm(cand, "", done=False, elapsed_s=CONFIRM_S, playing=ALRIGHT) == "carry_on"
    # More of her, or one more misheard word, is not them either.
    assert confirm(cand, "time we start", done=False, elapsed_s=0.6, playing=ALRIGHT) == "wait"
    assert confirm("or", "writes", done=False, elapsed_s=0.6, playing=ALRIGHT) == "wait"


def test_new_words_of_theirs_after_the_duck_stop_her() -> None:
    cand = "or writes"
    assert confirm(cand, "what about", done=False, elapsed_s=0.4, playing=ALRIGHT) == "interrupt"


def test_a_stop_word_not_hers_stops_her_at_once() -> None:
    assert confirm("wait what", "", done=False, elapsed_s=0.1, playing=ALRIGHT) == "interrupt"
    assert confirm("or writes", "stop", done=False, elapsed_s=0.2, playing=ALRIGHT) == "interrupt"


def test_a_stop_word_of_hers_does_not() -> None:
    playing = ("No, the train leaves at six.",)
    assert confirm("no the trained", "", done=False, elapsed_s=0.2, playing=playing) == "wait"


def test_acknowledgements_carry_on_as_before() -> None:
    assert confirm("yeah sure", "", done=True, elapsed_s=0.3, playing=ALRIGHT) == "carry_on"
    assert confirm("yeah sure", "", done=False, elapsed_s=0.3, playing=ALRIGHT) == "wait"
    assert confirm("um uh", "", done=True, elapsed_s=0.3, playing=ALRIGHT) == "carry_on"
    assert confirm("yeah yeah", "yeah yeah", done=True, elapsed_s=0.3, playing=ALRIGHT) == (
        "interrupt"
    )


def test_a_long_candidate_of_theirs_needs_no_more() -> None:
    assert (
        confirm("what about the bus instead", "", done=True, elapsed_s=0.2, playing=ALRIGHT)
        == "interrupt"
    )


PLATFORM = ("We leave at six from platform two.", "Bring your ticket.")


@pytest.mark.parametrize(
    ("cand", "after", "done"),
    [
        ("what about", "tuesday", True),
        ("what about", "tuesday", False),  # three words of theirs need no end
        ("skip that", "", True),
        ("go back", "", True),
        ("tell me", "more", True),
        ("what time does the last", "bus", False),  # more after the duck still counts
    ],
)
def test_short_real_cut_ins_stop_her(cand: str, after: str, done: bool) -> None:
    assert confirm(cand, after, done=done, elapsed_s=1.1, playing=PLATFORM) == "interrupt"


def test_two_words_of_theirs_not_yet_ended_wait_then_carry_on() -> None:
    assert confirm("skip that", "", done=False, elapsed_s=0.5, playing=PLATFORM) == "wait"
    assert confirm("skip that", "", done=False, elapsed_s=1.1, playing=PLATFORM) == "carry_on"


# ── Three of theirs, or two in an ended turn ──


def test_three_words_of_theirs_must_be_half_of_what_was_heard() -> None:
    # Her echo with three misheard words in it is not the user.
    cand, after = "or writes properly", "this time tame we start"
    assert confirm(cand, after, done=False, elapsed_s=0.5, playing=ALRIGHT) == "wait"


def test_an_ended_turn_of_two_counts_only_if_the_whole_turn_is_not_hers() -> None:
    """The recognizer ends echo turns at her pauses: the whole turn, her words it let go
    included, must be less than half hers."""
    assert (
        confirm("skip that", "", done=True, elapsed_s=0.3, playing=PLATFORM, turn="skip that")
        == "interrupt"
    )
    assert (
        confirm(
            "skip that",
            "",
            done=True,
            elapsed_s=0.3,
            playing=PLATFORM,
            turn="we leave at six from skip that",
        )
        == "wait"
    )


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


def test_her_stop_word_is_not_theirs_in_confirm() -> None:
    """Live: her "Stopped." came back as "Stop." and stopped her."""
    stopped = ("Stopped. For real this time,", "here is the plan.")
    assert confirm("stop", "", done=True, elapsed_s=0.2, playing=stopped) != "interrupt"
    playing = ("Let me check the times.",)
    assert confirm("stop", "", done=False, elapsed_s=0.1, playing=playing) == "interrupt"
