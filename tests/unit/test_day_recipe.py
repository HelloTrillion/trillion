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
from trillion.workspace.home import DEFAULT, TILES, arrange

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
    assert MAX_DAY_WIDGETS == 9
    assert (
        "most 9 widgets, in Home's order: next_up, free_time, inbox, tasks, mentions, "
        "due_soon, kpis, files, checks." in text
    )
    assert "Leave off" not in text
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


def test_drive_has_a_row_only_when_it_is_read() -> None:
    assert not any("drive_search" in r for r in rows(SetUp(), "Sam"))
    lines = rows(SetUp(calendar=True, slack=True, drive=True), "Sam")
    drive = (
        "Google Drive (drive_search with changed_since set to yesterday): files changed since "
        "yesterday. Show them as files."
    )
    # After Slack, before the packs and her reminders.
    assert lines[2] == drive and lines[1].startswith("Slack")
    text = recipe(SetUp(drive=True), DAY, "UTC", SAM)
    assert drive in text and NOTHING_CONNECTED not in text and no_dashes(text)


# --- The briefing follows Home (D15) -----------------------------------------------

EVERYTHING = SetUp(
    calendar=True,
    gmail=True,
    slack=True,
    drive=True,
    catalog=frozenset({"linear", "stripe", "github"}),
)


def test_a_row_whose_every_tile_is_removed_is_not_read() -> None:
    layout = arrange(DEFAULT, remove=["inbox", "kpis", "checks", "files"])
    text = recipe(EVERYTHING, DAY, "UTC", SAM, layout=layout)
    assert "gmail_search" not in text and "drive_search" not in text
    assert "Stripe" not in text and "GitHub" not in text
    assert "slack_search" in text and "Linear, with their tools" in text
    assert no_dashes(text)


def test_the_calendar_row_names_only_the_tiles_shown() -> None:
    both = rows(EVERYTHING, "Sam")[0]
    assert "next_up" in both and "free_time" in both
    no_free = rows(EVERYTHING, "Sam", arrange(DEFAULT, remove=["free_time"]))[0]
    assert no_free == (
        "Google Calendar (calendar_events): today's events. Show the next one as next_up."
    )
    no_next = rows(EVERYTHING, "Sam", arrange(DEFAULT, remove=["next_up"]))[0]
    assert no_next == (
        "Google Calendar (calendar_events): today's events. Show today's open time as free_time."
    )
    neither = rows(EVERYTHING, "Sam", arrange(DEFAULT, remove=["next_up", "free_time"]))
    assert not any("calendar_events" in r for r in neither)


def test_leave_off_names_the_removed_widgets() -> None:
    layout = arrange(DEFAULT, remove=["checks", "kpis"])
    text = recipe(EVERYTHING, DAY, "UTC", SAM, layout=layout)
    assert "Leave off: Revenue (kpis), What's broken (checks). Sam took them off Home." in text
    one = recipe(EVERYTHING, DAY, "UTC", SAM, layout=arrange(DEFAULT, remove=["inbox"]))
    assert "Leave off: Inbox (inbox). Sam took it off Home." in one
    assert no_dashes(text)


def test_the_board_is_asked_for_in_homes_order() -> None:
    layout = arrange(DEFAULT, order=["kpis", "checks"], remove=["files"])
    text = recipe(EVERYTHING, DAY, "UTC", SAM, layout=layout)
    assert (
        "in Home's order: kpis, checks, next_up, free_time, inbox, tasks, mentions, due_soon."
        in text
    )


def test_reminders_removed_means_schedule_list_is_not_read() -> None:
    layout = arrange(DEFAULT, remove=["tasks"])
    text = recipe(EVERYTHING, DAY, "UTC", SAM, layout=layout)
    assert "schedule_list" not in text
    # Nothing connected and no reminders: still the callout, and nothing else to read.
    bare = recipe(SetUp(), DAY, "UTC", SAM, layout=layout)
    assert "schedule_list" not in bare and NOTHING_CONNECTED in bare


def test_a_source_that_asks_first_is_not_named_when_its_tiles_are_removed() -> None:
    setup = SetUp(calendar=True, asks_first=("gmail", "slack"))
    text = recipe(setup, DAY, "UTC", SAM, layout=arrange(DEFAULT, remove=["inbox"]))
    assert "- Slack: do not call its tools" in text and "Gmail" not in text
    gone = recipe(setup, DAY, "UTC", SAM, layout=arrange(DEFAULT, remove=["inbox", "mentions"]))
    assert "tools, because each read asks" not in gone and "callout" not in gone


def test_every_widget_removed() -> None:
    layout = arrange(DEFAULT, remove=[t.id for t in TILES])
    text = recipe(EVERYTHING, DAY, "UTC", SAM, layout=layout)
    assert "Nothing to read: Sam took every widget off Home." in text
    assert "_search" not in text and "in Home's order" not in text and no_dashes(text)
