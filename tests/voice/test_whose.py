"""Hers or theirs, by when a word was said (#102)."""

from __future__ import annotations

from collections.abc import Callable

from trillion.voice.overlap import is_stop, matches, words_of
from trillion.voice.timeline import Timeline
from trillion.voice.types import Word
from trillion.voice.whose import OVER_ECHO, OVER_ROOM, ROOM, EchoShare, matched, whose


def _tl(text: str = "Give me the actual ask.") -> Timeline:
    tl = Timeline()
    tl.sentence(1, written=text, spoken=text, levels=[0.2] * 75)
    tl.start(1, at_s=10.0)  # plays 10.0 to 11.5
    tl.finish()
    return tl


def mic(level: float) -> Callable[[float, float], float]:
    return lambda t0, t1: level


def W(text: str, t: float) -> Word:
    return Word(text=text, start_s=t, end_s=t + 0.3)


def test_said_while_she_was_silent_is_theirs() -> None:
    tl = _tl()
    assert whose(W("yes", 12.5), tl, mic(0.05), share=0.1) == "theirs"
    assert whose(W("hello", 9.0), tl, mic(0.05), share=0.1) == "theirs"
    # Even her own word, and quietly: outside her playback it is the user.
    assert whose(W("actual", 12.5), tl, mic(0.0), share=0.1) == "theirs"


def test_her_word_said_while_she_said_it_is_hers() -> None:
    assert whose(W("actual", 10.8), _tl(), mic(0.02), share=0.1) == "hers"


def test_her_word_is_hers_however_loud() -> None:
    assert whose(W("Actual,", 10.8), _tl(), mic(0.9), share=0.1) == "hers"


def test_her_last_word_misheard_at_echo_level_is_hers() -> None:
    # "ask" heard as "ass" during her playback, as loud as her echo.
    assert whose(W("ass", 11.2), _tl(), mic(0.02), share=0.1) == "hers"


def test_a_loud_word_over_her_is_theirs() -> None:
    assert whose(W("tuesday", 10.6), _tl(), mic(0.2), share=0.1) == "theirs"


def test_loud_enough_is_over_her_echo_and_over_the_room() -> None:
    # Her echo would be 0.2 * 0.1 = 0.02; theirs takes 2.5 times that.
    tl = _tl()
    edge = 0.2 * 0.1 * OVER_ECHO
    assert whose(W("tuesday", 10.6), tl, mic(edge), share=0.1) == "theirs"
    assert whose(W("tuesday", 10.6), tl, mic(edge * 0.9), share=0.1) == "hers"
    # A loud room raises the bar.
    assert whose(W("tuesday", 10.6), tl, mic(edge), share=0.1, room=0.02) == "hers"
    assert whose(W("tuesday", 10.6), tl, mic(0.02 * OVER_ROOM), share=0.1, room=0.02) == "theirs"


def test_the_room_floor_holds_where_her_echo_is_tiny() -> None:
    tl = _tl()
    assert whose(W("tuesday", 10.6), tl, mic(ROOM * OVER_ROOM * 0.9), share=0.01) == "hers"
    assert whose(W("tuesday", 10.6), tl, mic(ROOM * OVER_ROOM), share=0.01) == "theirs"


def test_a_stop_word_not_in_her_sentence_is_theirs_if_loud_enough() -> None:
    assert whose(W("stop", 10.6), _tl(), mic(0.2), share=0.1) == "theirs"


def test_a_quiet_stop_word_not_hers_is_a_misheard_echo() -> None:
    assert whose(W("stop", 10.6), _tl(), mic(0.02), share=0.1) == "hers"


def test_a_stop_word_she_said_is_hers() -> None:
    tl = _tl("No, the train left. I stopped it.")
    assert whose(W("no", 10.6), tl, mic(0.9), share=0.1) == "hers"
    assert whose(W("Stop.", 10.6), tl, mic(0.9), share=0.1) == "hers"


def test_a_stop_word_is_not_hers_by_a_shorter_word_of_hers() -> None:
    # Her "can" does not make the user's "cancel" hers; her "waiter" is not "wait"...
    # but "wait" in "waiting" is.
    tl = _tl("You can call the waiter.")
    assert whose(W("cancel", 10.6), tl, mic(0.9), share=0.1) == "theirs"
    tl = _tl("I am waiting here.")
    assert whose(W("wait", 10.6), tl, mic(0.9), share=0.1) == "hers"


def test_a_stem_matches_either_way() -> None:
    tl = _tl("We are leaving at six.")
    assert whose(W("leave", 10.6), tl, mic(0.9), share=0.1) == "hers"
    tl = _tl("We leave at six.")
    assert whose(W("leaving", 10.6), tl, mic(0.9), share=0.1) == "hers"
    tl = _tl("Did you ask?")
    assert whose(W("asked", 10.6), tl, mic(0.9), share=0.1) == "hers"
    tl = _tl("I asked.")
    assert whose(W("ask", 10.6), tl, mic(0.9), share=0.1) == "hers"


def test_a_short_word_is_not_matched_by_a_longer_different_one() -> None:
    # Her "the", "for", "you" don't make the user's "then", "forget", "your" hers.
    tl = _tl("Is the plan for you?")
    for heard in ("then", "forget", "your", "android", "note"):
        assert whose(W(heard, 10.6), tl, mic(0.9), share=0.1) == "theirs", heard
    tl = _tl("Then forget your android note.")
    for heard in ("the", "for", "you", "and", "not"):
        assert whose(W(heard, 10.6), tl, mic(0.9), share=0.1) == "theirs", heard


def test_numbers_match_spelled_or_written() -> None:
    tl = _tl("It costs $26 at 10:30.")
    assert whose(W("twenty", 10.6), tl, mic(0.9), share=0.1) == "hers"
    assert whose(W("twenty-six", 10.6), tl, mic(0.9), share=0.1) == "hers"
    assert whose(W("26", 10.6), tl, mic(0.9), share=0.1) == "hers"
    tl = _tl("It costs twenty six dollars.")
    assert whose(W("$26", 10.6), tl, mic(0.9), share=0.1) == "hers"


def test_a_word_of_several_tokens_is_hers_only_if_all_are() -> None:
    tl = _tl("It costs twenty dollars.")
    assert whose(W("twenty-six", 10.6), tl, mic(0.9), share=0.1) == "theirs"
    assert whose(W("twenty-six", 10.6), tl, mic(0.02), share=0.1) == "hers"


def test_only_the_sentence_playing_then_counts() -> None:
    tl = Timeline()
    tl.sentence(1, written="Give me the ask.", spoken="Give me the ask.", levels=[0.2] * 50)
    tl.sentence(2, written="Then Tuesday.", spoken="Then Tuesday.", levels=[0.2] * 50)
    tl.start(1, at_s=10.0)
    tl.start(2, at_s=12.0)
    tl.finish()
    assert whose(W("tuesday", 10.3), tl, mic(0.9), share=0.1) == "theirs"
    assert whose(W("tuesday", 12.3), tl, mic(0.9), share=0.1) == "hers"


def test_her_spoken_form_counts_as_well_as_her_written_one() -> None:
    tl = Timeline()
    tl.sentence(1, written="See the README.", spoken="See the read me.", levels=[0.2] * 75)
    tl.start(1, at_s=10.0)
    assert whose(W("read", 10.6), tl, mic(0.9), share=0.1) == "hers"
    assert whose(W("README", 10.6), tl, mic(0.9), share=0.1) == "hers"


def test_echo_share_is_learned_from_her_words() -> None:
    share = EchoShare()
    for _ in range(5):
        share.learn(mic_level=0.02, her_level=0.2)
    assert abs(share.value - 0.1) < 1e-9


def test_echo_share_starts_cautious_and_is_clamped() -> None:
    share = EchoShare()
    assert share.value == 0.5
    share.learn(mic_level=0.0, her_level=0.2)
    assert share.value == 0.02
    share = EchoShare()
    share.learn(mic_level=0.9, her_level=0.2)
    assert share.value == 1.0


def test_echo_share_ignores_her_silence() -> None:
    share = EchoShare()
    share.learn(mic_level=0.1, her_level=0.0)
    assert share.value == 0.5


def test_echo_share_keeps_the_last_twenty() -> None:
    share = EchoShare()
    for _ in range(20):
        share.learn(mic_level=0.02, her_level=0.2)
    for _ in range(20):
        share.learn(mic_level=0.04, her_level=0.2)
    assert abs(share.value - 0.2) < 1e-9
    assert share.learned == 20


def test_echo_share_is_not_jumped_by_the_user_saying_her_words() -> None:
    share = EchoShare()
    for _ in range(5):
        share.learn(mic_level=0.02, her_level=0.2)
    for _ in range(3):
        share.learn(mic_level=0.2, her_level=0.2)  # ten times her echo: the user
    assert abs(share.value - 0.1) < 1e-9


def test_a_few_loud_user_words_among_her_echo_barely_move_it() -> None:
    share = EchoShare()
    for i in range(40):
        share.learn(mic_level=0.9 if i % 5 == 4 else 0.1, her_level=1.0)
    assert abs(share.value - 0.1) < 1e-9


def test_echo_share_climbs_when_her_echo_gets_louder() -> None:
    # Speakers turned up mid-session: her echo was 8%, now 35%.
    share = EchoShare()
    for _ in range(20):
        share.learn(mic_level=0.08, her_level=1.0)
    for _ in range(10):
        share.learn(mic_level=0.35, her_level=1.0)
    assert share.value > 0.1  # climbing, a step at a time
    for _ in range(11):
        share.learn(mic_level=0.35, her_level=1.0)
    assert abs(share.value - 0.35) < 1e-9  # there in 21 words


def test_matched_is_her_words_while_she_says_them() -> None:
    tl = _tl()
    assert matched(W("actual", 10.8), tl)
    assert not matched(W("actual", 12.5), tl)  # she had finished
    assert not matched(W("tuesday", 10.8), tl)
    assert not matched(W("...", 10.8), tl)


def _send() -> Timeline:
    return _tl("Should I send it or wait?")


def test_her_words_shouted_over_her_are_theirs_once_her_echo_is_known() -> None:
    tl = _send()
    for t, text in ((10.6, "send"), (10.9, "it")):
        assert whose(W(text, t), tl, mic(0.2), share=0.1, learned=5) == "theirs"
        # Not before her echo is learned: then a matched word is hers.
        assert whose(W(text, t), tl, mic(0.2), share=0.1, learned=4) == "hers"


def test_her_words_at_echo_level_stay_hers_once_learned() -> None:
    tl = _send()
    assert whose(W("send", 10.6), tl, mic(0.02), share=0.1, learned=50) == "hers"
    # Louder than an unmatched word needs, but not twice that.
    assert whose(W("send", 10.6), tl, mic(0.09), share=0.1, learned=50) == "hers"


def test_her_word_timed_just_past_her_sentence_is_judged_by_her_level_nearby() -> None:
    # Heard 11.6 to 11.9, just after she ended at 11.5: her level on the bare word is 0,
    # so only the widened span shows this is her echo.
    tl = _send()
    word = Word(text="wait", start_s=11.6, end_s=11.75)
    assert tl.her_level(word.start_s, word.end_s) == 0.0
    assert whose(word, tl, mic(0.02), share=0.1, learned=50) == "hers"


def test_matching_helpers() -> None:
    assert words_of("It's $5, OK?") == ["it's", "dollars", "five", "ok"]
    hers = set(words_of("I stopped the leaving train."))
    assert matches("leavin", hers)
    assert matches("trains", hers)
    assert not matches("tr", hers)
    assert matches("stop", hers, stop=True)
    assert not matches("trains", hers, stop=True)  # strict: only her word or its inflection
    assert is_stop("wait") and not is_stop("train")


def test_one_low_first_word_does_not_lock_the_echo_share() -> None:
    share = EchoShare()
    share.learn(mic_level=0.0, her_level=0.2)  # clamped to 0.02
    for _ in range(20):
        share.learn(mic_level=0.02, her_level=0.2)
    assert abs(share.value - 0.1) < 1e-9


def test_her_peak_nearby_sets_the_bar_for_her_own_words() -> None:
    # Quiet then loud: her mean over the span is lower than her peak, and the peak counts.
    tl = Timeline()
    text = "Should I send it or wait?"
    tl.sentence(1, written=text, spoken=text, levels=[0.05] * 40 + [0.4] * 35)
    tl.start(1, at_s=10.0)
    # Peak 0.4: the bar is 2 x 2.5 x 0.1 x 0.4 = 0.2. The mean (0.145) would make it 0.07.
    assert whose(W("send", 10.3), tl, mic(0.15), share=0.1, learned=5) == "hers"
    assert whose(W("send", 10.3), tl, mic(0.21), share=0.1, learned=5) == "theirs"


# ── Her words count where she was saying them, not anywhere in the sentence ──


def _long() -> Timeline:
    # Twelve words over 3 s: she says about four a second.
    text = "What I would do first is check the invoices before you pay them"
    tl = Timeline()
    tl.sentence(1, written=text, spoken=text, levels=[0.2] * 150)
    tl.start(1, at_s=10.0)  # plays 10.0 to 13.0
    tl.finish()
    return tl


def test_her_word_heard_where_she_said_it_is_hers() -> None:
    tl = _long()
    # "invoices" is her ninth word: said around 12.1 s, heard a little after.
    assert matched(W("invoices", 12.1), tl)
    assert matched(W("invoices", 12.5), tl)  # her echo, a moment late


def test_a_word_of_hers_said_far_from_where_she_was_is_theirs_to_judge() -> None:
    # Live: the user's own "what" or "you" over her taken for her echo because the word
    # was somewhere in her sentence, and dropped from what they said.
    tl = _long()
    assert not matched(W("what", 12.4), tl)  # she said "what" at the start
    assert not matched(W("invoices", 10.2), tl)  # she had not got there yet


def test_an_unlearned_echo_does_not_swallow_their_words() -> None:
    # Before her echo is learned, a word that is not hers is theirs at a normal
    # speaking level: the page cancels her echo, so it rarely reaches the microphone.
    tl = _tl()
    share = EchoShare()
    assert share.learned == 0
    level = 0.2 * 0.6  # their voice at sixty percent of hers
    assert whose(W("tuesday", 10.8), tl, mic(level), share=share.value, learned=0) == "theirs"
    # A whisper at echo level is still her echo misheard.
    assert whose(W("tuesday", 10.8), tl, mic(0.02), share=share.value, learned=0) == "hers"
