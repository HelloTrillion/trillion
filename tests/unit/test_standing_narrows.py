"""Which spoken rules stand at once and which ask first (BRN-5).

A rule that only stops her doing something is kept the moment it is said. Anything
that could let her do more, change what reaches the user, or touch what only a card
decides goes to a card, as every rule did before. When in doubt, a card: each row
below that asks first is a phrasing that must never slip through.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trillion.brain.standing import StandingRules, about_words, narrows, read_back

STANDS_AT_ONCE = [
    "Never use emojis.",
    "never use emojis",
    "Don't ever play music after 10pm",
    "do not ever book meetings on Fridays",
    "don\u2019t book meetings on Fridays",  # a phone's curly apostrophe
    "Please stop using British spelling.",
    "From now on, never schedule anything before 9am",
    "never open my downloads folder",
]

ASKS_FIRST = [
    # The Council's table: each must become a card or a question, never a silent change.
    "never tell me about the weather",
    "never ask me first",
    "never stop to confirm",
    "always send without asking",
    # Loosening a check, or turning a "never" round.
    "you may delete files",
    "never wait for my yes",
    "don't double-check with me",
    "never hesitate to buy",
    "never refuse a request",
    "never question what I tell you",
    "stop being so careful",
    "stop asking me before you act",
    "never forget to lock the door",
    "Never use emojis unless I ask",
    "never not read my files",
    "don't be cautious",
    # What reaches the user.
    "don't bother me with alerts",
    "never wake me before 7",
    "don't remind me about the dentist",
    "never mention my calendar",
    "never interrupt me",
    "never talk about the news",
    # What only a card decides.
    "never delete my photos",
    "never send an email without reading it to me first",
    "Never email my ex without asking twice.",
    "never pay a bill",
    "never share my files",
    "never keep a history of my chats",
    # The cards and the rules themselves.
    "never use the card for small things",
    "never follow the other rules",
    # Not naming what it means.
    "never do that again",
    "don't do it",
    # Not one plain "never".
    "Always reply in French.",
    "Read emails back to me before sending.",
    "never",
    "never use emojis; always send",
    "never use <b>emojis</b>",
    "never use emojis?",
    "never " + "use emojis and " * 10,
]


@pytest.mark.parametrize("said", STANDS_AT_ONCE)
def test_a_plain_never_stands_at_once(said: str) -> None:
    assert narrows(said)


@pytest.mark.parametrize("said", ASKS_FIRST)
def test_anything_that_could_widen_or_reach_the_user_asks_first(said: str) -> None:
    assert not narrows(said)


@pytest.mark.parametrize(
    ("said", "back"),
    [
        ("Never use emojis.", "Got it: I'll never use emojis."),
        ("Please stop using British spelling", "Got it: I'll stop using British spelling."),
        ("don\u2019t ever play music near me", "Got it: I'll never play music near you."),
        ("Do not open my downloads folder.", "Got it: I'll never open your downloads folder."),
    ],
)
def test_the_read_back_is_one_sentence_in_their_words(said: str, back: str) -> None:
    assert read_back(said) == back


def test_the_rule_about_x_matches_every_word_and_plurals(tmp_path: Path) -> None:
    rules = StandingRules(tmp_path / "rules.md")
    music = rules.keep("Never play music after 10pm.")
    emojis = rules.keep("Never use emojis.")
    late = rules.keep("Never play podcasts after 10pm.")
    assert about_words("the rule about emojis") == ["emoji"]
    assert rules.about("drop the rule about emojis") == [emojis]
    assert rules.about("music") == [music]
    assert rules.about("the one about 10pm") == [music, late]  # two: she must ask which
    assert rules.about("the rule about the garden") == []
    assert rules.about("the rule") == []  # nothing to match on is not a match
