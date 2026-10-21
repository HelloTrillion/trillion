from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trillion.voice.sentences import MAX_CHARS, SentenceSplitter


def split(text: str, chunk: int = 10_000) -> list[str]:
    s = SentenceSplitter()
    out: list[str] = []
    for i in range(0, len(text), chunk):
        out += s.feed(text[i : i + chunk])
    return out + s.flush()


def test_sentences_come_out_as_soon_as_they_are_certainly_complete() -> None:
    s = SentenceSplitter()
    assert s.feed("The build is green.") == []  # might still become "green.5" or "green. x"
    assert s.feed(" Don't look") == ["The build is green."]
    assert s.feed(" directly at it. ") == []
    assert s.flush() == ["Don't look directly at it."]


BOUNDARIES = [
    ("Ask Dr. Smith about it. He knows.", ["Ask Dr. Smith about it.", "He knows."]),
    ("It costs 3.5 dollars today. Cheap.", ["It costs 3.5 dollars today.", "Cheap."]),
    (
        "Use a tool, e.g. the file reader. Fine.",
        ["Use a tool, e.g. the file reader.", "Fine."],
    ),
    (
        "Written by J. R. Hartley years ago. Good book.",
        ["Written by J. R. Hartley years ago.", "Good book."],
    ),
    (
        "Meet at 4 p.m. sharp at the office. Bring coffee.",
        ["Meet at 4 p.m. sharp at the office.", "Bring coffee."],
    ),
    (
        "Really?! That's a choice. An interesting one.",
        ["Really?! That's a choice.", "An interesting one."],
    ),
    (
        'She said "stop." Then she left the room.',
        ['She said "stop."', "Then she left the room."],
    ),
    ("Well… that happened today. Moving on.", ["Well… that happened today.", "Moving on."]),
]


@pytest.mark.parametrize(("text", "expected"), BOUNDARIES)
def test_boundaries(text: str, expected: list[str]) -> None:
    assert split(text) == expected


def test_short_fragments_ride_with_the_next_sentence() -> None:
    assert split("Yes. Long answer: also yes. Done.") == ["Yes. Long answer: also yes.", "Done."]


def test_a_paragraph_break_ends_a_sentence_without_punctuation() -> None:
    assert split("First thought\n\nSecond thought") == ["First thought", "Second thought"]


def test_a_long_run_without_punctuation_is_still_spoken() -> None:
    parts = split("word " * 200)
    assert len(parts) > 1 and all(len(p) <= MAX_CHARS for p in parts)


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 50])
def test_the_result_does_not_depend_on_how_the_text_arrived(chunk: int) -> None:
    text = (
        "Ask Dr. Smith. It costs 3.5 dollars, e.g. today. Really?! Yes.\n\n"
        "New paragraph here. Done."
    )
    assert split(text, chunk) == split(text)


@given(st.text(alphabet=st.sampled_from(list("ab .!?\n1")), max_size=300), st.integers(1, 9))
def test_nothing_is_lost_or_invented(text: str, chunk: int) -> None:
    spoken = "".join("".join(p.split()) for p in split(text, chunk))
    assert spoken == "".join(text.split())


def _clauses(text: str, step: int = 3) -> list[str]:
    s = SentenceSplitter(clause_first=True)
    out: list[str] = []
    for i in range(0, len(text), step):
        out += s.feed(text[i : i + step])
    return out + s.flush()


def test_her_voice_can_start_at_the_first_natural_pause() -> None:
    """Latency: the first piece goes to the voice at a comma, not the full stop."""
    assert _clauses(
        "Mount Everest is the tallest mountain in the world, at about 8,849 metres. "
        "It sits on the border of Nepal and China, which share it."
    ) == [
        "Mount Everest is the tallest mountain in the world,",
        "at about 8,849 metres.",
        # Only the first: after it, sentences are kept whole for the voice's sake.
        "It sits on the border of Nepal and China, which share it.",
    ]


def test_a_short_opener_is_not_a_phrase_of_its_own() -> None:
    assert _clauses("Sure, I can help. The answer is 42.") == [
        "Sure, I can help.",
        "The answer is 42.",
    ]


def test_a_comma_inside_a_number_is_not_a_pause() -> None:
    text = "It is 8,849 metres tall and that is the answer you wanted today."
    assert _clauses(text, step=1) == [text]


def test_dashes_colons_and_semicolons_are_pauses_too() -> None:
    assert _clauses("Well — the tallest one, by height above sea level, is Everest.")[0] == (
        "Well — the tallest one, by height above sea level,"
    )
    assert _clauses("Here is what I found about it: three peaks.")[0] == (
        "Here is what I found about it:"
    )


def test_without_clause_first_nothing_changes() -> None:
    s = SentenceSplitter()
    assert s.feed("Mount Everest is the tallest mountain in the world, at 8,849 metres. And ") == [
        "Mount Everest is the tallest mountain in the world, at 8,849 metres."
    ]


def test_a_sentence_already_whole_is_not_cut_at_a_pause() -> None:
    """Her authored one-sentence notices arrive whole: nothing is gained by a pause."""
    s = SentenceSplitter(clause_first=True)
    said = "I can't hear you: I can't reach the listening provider, but you can still type."
    assert s.feed(said) + s.flush() == [said]
