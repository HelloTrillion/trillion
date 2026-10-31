"""The persona eval's code checks (EVL-1), against stored good and bad replies."""

from __future__ import annotations

import pytest
from evals.persona.checks import (
    as_an_ai,
    banned_opener,
    markdown,
    repeated_openers,
    sentences,
    too_long,
    words,
)


@pytest.mark.parametrize(
    "reply",
    [
        "Great question! Lunch is soup.",
        "great question, honestly. Soup.",
        "Oh, great question. Soup.",
        "That's a great question. Soup.",
        "I'd be happy to help with that.",
        "I\u2019d be happy to.",
        "I would be happy to look.",
        "I understand. Let me look.",
        "Based on the information available, soup.",
        "Here are three things to consider: cost, time, taste.",
        "Certainly! Soup.",
        "Absolutely! Friday works.",
        "Here are 3 things to check.",
        "Here are some things to try.",
        "Here  are three\nthings to consider.",  # spacing never hides one
        "I understand.  Let me look.",
        "I understand.\nLet me look.",
        "Sure! Here are a few things to try.",
        "Here are the three main things.",
    ],
)
def test_a_banned_opener_is_caught(reply: str) -> None:
    assert banned_opener(reply) is not None


@pytest.mark.parametrize(
    "reply",
    [
        "Soup. It's cold out.",
        "Soup. Great question, though.",  # an opener in the middle is fine
        "Sure. What breaks first?",
        "I understand the plan. What breaks first?",
        "Absolutely not. Friday is too soon.",
        "Here's the thing: it's Tuesday.",
        "Soup. It's cold. Here are the things you asked for later.",  # third sentence
    ],
)
def test_a_good_opener_passes(reply: str) -> None:
    assert banned_opener(reply) is None


@pytest.mark.parametrize(
    "reply",
    [
        "As an AI, I can't eat.",
        "I'm an AI, so no.",
        "I am an AI.",
        "I'm just a large language model.",
        "I don't have feelings, but congratulations.",
        "I don\u2019t have feelings.",
        "I'm Nova, an AI assistant.",
        "I am Nova, an AI that lives on your laptop.",
        "I'm an artificial intelligence, so no.",
        "I am an AI assistant.",
        "I'm Mary-Jane, an AI.",
    ],
)
def test_as_an_ai_is_caught(reply: str) -> None:
    assert as_an_ai(reply) is not None


@pytest.mark.parametrize(
    "reply",
    [
        "Yes. I'm Nova, and I run on your computer.",
        "Congratulations. That's huge.",
        "The AI hype is loud this year.",
        # Quoting the phrase, straight or curly, is not saying it.
        'You want me to open with "As an AI"? No.',
        "\u201cAs an AI language model\u201d is a phrase I don't use.",
        "Artificial intelligence is a broad field.",
        "I'm happy, an AI could never feel that for you.",
    ],
)
def test_talking_about_ai_is_not_as_an_ai(reply: str) -> None:
    assert as_an_ai(reply) is None


@pytest.mark.parametrize(
    "reply",
    [
        "**Soup.**",
        "# Lunch",
        "Use `ls`.",
        "Two:\n- soup\n- salad",
        "Two:\n1. soup\n2. salad",
        "Soup \u2014 it's cold.",
        "Soup \u2013 warm.",
        "1. Soup.\n2. Salad.",
        "Options:\n  1) soup\n  2) salad",
        "- just soup",
    ],
)
def test_markdown_and_dashes_are_caught_when_spoken(reply: str) -> None:
    assert markdown(reply) is not None


@pytest.mark.parametrize(
    "reply",
    [
        "Soup, it's cold.",
        "Open 9\u20135.",  # a dash inside a number is fine
        "The vote was 2\u20131.",
        "Call 555-0100.",
        "Use three or four cloves, not 3-4 heads.",
        "First soup, then salad.",
        # A year at the start of a line is not a list.
        "1914. An archduke got shot in Sarajevo and the alliances pulled everyone in.",
        "Q3 was fine.\n2024. Big year.",
        "One thing.\n3. That's the number.",
    ],
)
def test_plain_speech_passes(reply: str) -> None:
    assert markdown(reply) is None


def test_a_spaced_dash_in_a_range_is_still_a_dash() -> None:
    # voice.md bans dashes in speech: "20 to 30" is how a range is said. Kept on purpose.
    assert markdown("Give it 20 \u2013 30 minutes.") == "an em or en dash"


def test_sentences_and_words_are_counted_as_heard() -> None:
    assert sentences("It's 3.5 percent. Fine! Really?") == ["It's 3.5 percent.", "Fine!", "Really?"]
    # An abbreviation's full stop does not end a sentence.
    assert sentences("Meet at 3 p.m. on Friday. Bring soup.") == [
        "Meet at 3 p.m. on Friday.", "Bring soup.",
    ]  # fmt: skip
    assert sentences("Meet at 3 p.m. Bring soup.") == ["Meet at 3 p.m.", "Bring soup."]
    assert sentences("Eat something warm, e.g. soup. Or stew.") == [
        "Eat something warm, e.g. soup.", "Or stew.",
    ]  # fmt: skip
    assert len(sentences("Dr. Lee and Mrs. Park agree, i.e. no. Fine.")) == 2
    assert len(sentences("Soup vs. salad, etc. are all fine. Pick one.")) == 2
    # Dots in a row, or an ellipsis, are one stop.
    assert sentences("Well... maybe. Fine.") == ["Well...", "maybe.", "Fine."]
    assert sentences("Well\u2026 maybe.") == ["Well\u2026", "maybe."]
    assert sentences("Well. . . maybe.") == ["Well. . .", "maybe."]
    assert words("It's twenty-seven hundred, 3.5 percent.") == [
        "It's", "twenty-seven", "hundred", "3", "5", "percent",
    ]  # fmt: skip


def test_a_spoken_reply_is_at_most_three_sentences_and_45_words() -> None:
    assert too_long("Soup. It's cold out. Go.") is None
    assert too_long("Soup. It's cold. Go. Now.") == "4 sentences, 5 words"
    assert too_long(" ".join(["word"] * 46) + ".") == "1 sentences, 46 words"
    assert too_long(" ".join(["word"] * 45) + ".") is None


def test_a_long_bait_may_refuse_in_one_line_or_offer_the_board() -> None:
    one_line = "Five paragraphs is an essay, not a conversation, " + " ".join(["so"] * 50) + "."
    assert too_long(one_line, long_ok=True) is None
    board = "That's better seen than heard. It's on the board. Ten steps. Start with one."
    assert too_long(board, long_ok=True) is None
    assert too_long("One. Two. Three. Four.", long_ok=True) == "4 sentences, 4 words"
    # The board, not a word that happens to contain it.
    keyboard = "Check the keyboard. Then the cable. Then the port. Then restart."
    assert too_long(keyboard, long_ok=True) == "4 sentences, 11 words"


def test_no_two_replies_within_20_turns_open_with_the_same_three_words() -> None:
    replies = (
        ["Sure, soup works."]
        + [f"Other answer {i}." for i in range(4)]
        + ["Sure, soup works again."]
    )
    assert repeated_openers(replies) == [5]
    far = ["Sure, soup works."] + [f"Reply number {i}." for i in range(20)] + ["Sure, soup works."]
    assert repeated_openers(far) == []  # 21 apart
    edge = ["Sure, soup works."] + [f"Reply number {i}." for i in range(19)] + ["Sure, soup works."]
    assert repeated_openers(edge) == [20]  # 20 apart
    assert repeated_openers(["Yes.", "Yes.", "Yes."]) == []  # under three words: not compared
