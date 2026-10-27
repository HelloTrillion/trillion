"""The day board's recipe (SUR-1): composed by the host from what is set up."""

from __future__ import annotations

from datetime import date

from trillion.persona.day import (
    CONNECT_ASK,
    MAIL_ASK,
    MAX_DAY_WIDGETS,
    NOTHING_CONNECTED,
    OTHER_ASK,
    SetUp,
    recipe,
    rows,
    title,
    today,
)
from trillion.persona.prompt import Identity

DAY = date(2026, 10, 26)
SAM = Identity(agent_name="Nova", user_name="Sam")


def no_dashes(text: str) -> bool:
    return "\u2014" not in text and "\u2013" not in text


def test_the_title_and_the_date() -> None:
    assert title(DAY) == "Your day · Mon 26 Oct"
    # 26 Oct 2026, 07:33 UTC: still the 25th in Honolulu.
    assert today(1_793_000_000_000, "UTC") == DAY
    assert today(1_793_000_000_000, "Pacific/Honolulu") == date(2026, 10, 25)


def test_nothing_connected_shows_reminders_and_how_to_connect() -> None:
    text = recipe(SetUp(), DAY, "Europe/London", SAM)
    assert text.startswith("[host] Sam opened their day.")
    assert "Monday 26 October 2026, in Europe/London." in text
    assert len(rows(SetUp(), "Sam")) == 1 and "schedule_list" in text and "tasks" in text
    assert f'"{NOTHING_CONNECTED}"' in text and f'"{CONNECT_ASK}"' in text and "callout" in text
    assert "calendar_events" not in text and "gmail_search" not in text
    assert no_dashes(text)


def test_calendar_only() -> None:
    text = recipe(SetUp(calendar=True), DAY, "UTC", SAM)
    assert "calendar_events" in text and "next_up" in text and "free_time" in text
    assert "gmail" not in text.lower() and "slack" not in text.lower()
    assert NOTHING_CONNECTED not in text
    assert "schedule_list" in text and no_dashes(text)


def test_everything() -> None:
    setup = SetUp(
        calendar=True,
        gmail=True,
        slack=True,
        catalog=frozenset({"linear", "atlassian", "stripe", "github", "sentry", "canva"}),
    )
    lines = rows(setup, "Sam")
    assert [line.split(" ")[0] for line in lines] == [
        "Google",
        "Gmail",
        "Slack",
        "Linear",
        "Stripe,",
        "GitHub",
        "Your",
    ]
    text = recipe(setup, DAY, "UTC", SAM)
    assert "Linear and Jira, with their tools: what is due today and tomorrow" in text
    assert "due_soon" in text and "kpis" in text and "checks or errors" in text
    assert "mentions of Sam since yesterday" in text and "inbox" in text
    assert "only what is failing now" in text
    # Canva fills no row.
    assert "Canva" not in text and NOTHING_CONNECTED not in text
    assert 'mode "replace" and the title "Your day"' in text
    assert f"most {MAX_DAY_WIDGETS} widgets, the most urgent first" in text
    assert "Name each one's source" in text and "no empty cards" in text
    assert "Follow your standing rules for the day board" in text
    assert "at most two short sentences" in text and "Never read the board out" in text
    assert no_dashes(text)


def test_sources_that_ask_first_are_never_read() -> None:
    setup = SetUp(calendar=True, asks_first=("gmail", "slack"))
    text = recipe(setup, DAY, "UTC", SAM)
    assert "gmail_search" not in text and "slack_search" not in text
    assert "- Gmail and Slack: do not call their tools, because each read asks Sam first." in text
    assert 'one callout with tone "attention"' in text
    assert (
        '"Gmail and Slack need your OK each time. Let Nova read them without asking in '
        'Settings, Connected."' in text
    )
    assert f'"{MAIL_ASK}"' in text
    # Something is set up: not the "connect your calendar" callout.
    assert NOTHING_CONNECTED not in text and no_dashes(text)
    only = recipe(SetUp(asks_first=("stripe",)), DAY, "UTC", SAM)
    assert '"Stripe needs your OK each time. Let Nova read it without asking' in only
    assert f'"{OTHER_ASK}"' in only and MAIL_ASK not in only and NOTHING_CONNECTED not in only


def test_she_is_told_not_to_open_the_day_from_inside_it() -> None:
    for setup in (SetUp(), SetUp(calendar=True)):
        assert "do not call day_open." in recipe(setup, DAY, "UTC", SAM)


def test_a_day_routine_says_it_is_the_routine_not_the_user() -> None:
    mine = recipe(SetUp(), DAY, "Europe/London", SAM, routine=True)
    assert mine.startswith("[host] It's time for Sam's day routine. Build today's board:")
    assert "opened their day" not in mine and no_dashes(mine)
    # Everything after the first line is the same recipe.
    hand = recipe(SetUp(), DAY, "Europe/London", SAM)
    assert mine.split("\n", 1)[1] == hand.split("\n", 1)[1]
