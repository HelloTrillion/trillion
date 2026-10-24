"""Times: the user's wall clock, kept across daylight-saving changes (AGT-9)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trillion.scheduler.when import (
    Repeat,
    WhenError,
    next_after,
    parse_local,
    read_repeat,
    repeat_words,
    spoken,
    summary,
    to_ms,
)

NY = "America/New_York"
DAILY = Repeat(every="day")


def ms(local: str, zone: str = NY) -> int:
    return to_ms(parse_local(local, zone))


def utc(value: int | None) -> str:
    assert value is not None
    return datetime.fromtimestamp(value / 1000, UTC).strftime("%Y-%m-%d %H:%M")


def test_a_local_time_is_read_in_its_zone_with_or_without_seconds() -> None:
    assert utc(ms("2026-10-24T16:00")) == "2026-10-24 20:00"
    assert parse_local("2026-10-24T16:00:30", NY).second == 30


@pytest.mark.parametrize("text", ["2026-10-24 16:00", "tomorrow at four", "2026-02-30T09:00"])
def test_a_time_that_cannot_be_read_is_refused(text: str) -> None:
    with pytest.raises(WhenError):
        parse_local(text, NY)


def test_an_unknown_zone_is_refused() -> None:
    with pytest.raises(WhenError, match="not a timezone"):
        parse_local("2026-10-24T16:00", "Mars/Olympus")


def test_the_skipped_hour_moves_to_the_first_real_minute_after_it() -> None:
    # 2:30 on 8 March 2026 never happens in New York: the clocks go from 2:00 to 3:00.
    assert utc(ms("2026-03-08T02:30")) == "2026-03-08 07:00"  # 3:00 EDT


def test_the_repeated_hour_means_the_first_one() -> None:
    assert utc(ms("2026-11-01T01:30")) == "2026-11-01 05:30"  # 1:30 EDT, not EST


def test_a_one_off_is_next_until_it_has_passed() -> None:
    at = ms("2026-10-24T16:00")
    assert next_after("2026-10-24T16:00", None, NY, at - 1) == at
    assert next_after("2026-10-24T16:00", None, NY, at) is None


def test_nine_stays_nine_across_both_changes() -> None:
    spring = next_after("2026-03-07T09:00", DAILY, NY, ms("2026-03-07T09:00"))
    assert utc(spring) == "2026-03-08 13:00"  # 9:00 EDT; the day before was 14:00 UTC
    autumn = next_after("2026-10-31T09:00", DAILY, NY, ms("2026-10-31T09:00"))
    assert utc(autumn) == "2026-11-01 14:00"  # 9:00 EST


def test_every_repeat_finds_its_next_day() -> None:
    friday = "2026-10-23T09:00"
    weekday = next_after(friday, Repeat(every="weekday"), NY, ms(friday))
    assert utc(weekday) == "2026-10-26 13:00"  # Monday
    monday = "2026-10-26T09:00"
    week = Repeat(every="week", days=("mon", "thu"))
    assert utc(next_after(monday, week, NY, ms(monday))) == "2026-10-29 13:00"  # Thursday


def test_the_31st_in_february_is_its_last_day() -> None:
    month = Repeat(every="month", day=31)
    assert utc(next_after("2027-01-31T09:00", month, NY, ms("2027-01-31T09:00"))) == (
        "2027-02-28 14:00"
    )
    assert utc(next_after("2028-01-31T09:00", month, NY, ms("2028-01-31T09:00"))) == (
        "2028-02-29 14:00"
    )


def test_catching_up_skips_every_occurrence_already_past() -> None:
    # Set for 9:00 every day from Monday; the computer was off until Saturday 9:30.
    found = next_after("2026-10-19T09:00", DAILY, NY, ms("2026-10-24T09:30"))
    assert utc(found) == "2026-10-25 13:00"


@pytest.mark.parametrize(
    "raw",
    [
        {"every": "year"},
        {"every": "week"},
        {"every": "week", "days": ["someday"]},
        {"every": "month", "day": 32},
        {"every": "month", "day": True},
        {"every": "day", "at": 1},
        "daily",
    ],
)
def test_a_repeat_that_cannot_be_read_is_refused(raw: object) -> None:
    with pytest.raises(WhenError):
        read_repeat(raw)


def test_repeats_are_read_and_written_back_the_same() -> None:
    for raw in ({"every": "day"}, {"every": "weekday"}, {"every": "month", "day": 15}):
        found = read_repeat(raw)
        assert found is not None and found.to_json() == raw
    week = read_repeat({"every": "week", "days": ["thu", "mon"]})
    assert week == Repeat(every="week", days=("mon", "thu"))
    assert read_repeat(None) is None


def test_times_and_repeats_in_words() -> None:
    assert spoken(ms("2026-10-24T16:00"), NY) == "Sat 24 Oct, 4:00 pm"
    assert summary("2026-10-24T16:00", None, NY) == "Sat 24 Oct at 4:00 pm"
    assert summary("2026-10-26T09:00", Repeat(every="weekday"), NY) == "Every weekday at 9:00 am"
    assert repeat_words(Repeat(every="week", days=("mon", "thu"))) == "every Mon and Thu"
    assert repeat_words(Repeat(every="month", day=31)) == (
        "every month on the 31st (or the month's last day)"
    )
    assert repeat_words(None) == "once"
