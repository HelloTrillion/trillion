from __future__ import annotations

import pytest

from trillion.voice.speakable import speakable


@pytest.mark.parametrize(
    ("written", "said"),
    [
        ("**Done.** `hello.txt` is *there*.", "Done. hello.txt is there."),
        ("***Really*** __sure__ ~~no~~", "Really sure no"),
        ("# Heading\nBody text", "Heading\nBody text"),
        ("- one\n- two\n3. three", "one\ntwo\nthree"),
        ("> quoted line", "quoted line"),
        ("See [the docs](https://example.com/a?b=1) for more.", "See the docs for more."),
        ("Go to https://example.com/long/path now.", "Go to now."),
        ("![a chart](x.png) shows it", "a chart shows it"),
        ("Run this:\n```bash\nrm -rf /\n```\nThen stop.", "Run this:\n\nThen stop."),
        ("Great 🎉 work 👍🏽 team 👩‍💻!", "Great work team !"),
        ("| a | b |\n|---|---|\n| 1 | 2 |", "a b\n\n1 2"),
        ("one\n\n\n\n\ntwo", "one\n\ntwo"),
        ("2 * 3 = 6 and snake_case stay", "2 3 = 6 and snake case stay"),
    ],
)
def test_markup_is_removed_and_the_words_are_kept(written: str, said: str) -> None:
    assert speakable(written) == said


def test_plain_speech_is_untouched() -> None:
    line = "Short answer: yes. Long answer: also yes."
    assert speakable(line) == line


def test_a_code_block_still_being_streamed_is_not_read_out() -> None:
    assert speakable("Here:\n```python\nprint('half") == "Here:"


def test_it_never_adds_words() -> None:
    written = "**Bold** [link](http://x.example) `code` 🎉 # not a heading"
    assert set(speakable(written).split()) <= {"Bold", "link", "code", "not", "a", "heading"}


@pytest.mark.parametrize(
    ("written", "said"),
    [
        ("Leave at six \u2014 traffic is bad.", "Leave at six, traffic is bad."),
        ("Leave at six\u2014traffic is bad.", "Leave at six, traffic is bad."),
        ("Mondays \u2013 Fridays only.", "Mondays, Fridays only."),
        ("Bring milk \u2014 eggs \u2014 and bread.", "Bring milk, eggs, and bread."),
        ("Wait \u2014", "Wait"),
        ("A well-known place, 9-5.", "A well-known place, 9-5."),
    ],
)
def test_a_dash_between_words_is_a_pause_not_a_sound(written: str, said: str) -> None:
    """Em and en dashes are read aloud as noise by some voices; she pauses instead."""
    assert speakable(written) == said
