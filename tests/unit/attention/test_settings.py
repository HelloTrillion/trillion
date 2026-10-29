"""Settings → Speaking up: one preference, read leniently and saved strictly."""

from __future__ import annotations

from typing import Any

import pytest

from trillion.attention.settings import (
    DEFAULT,
    AttentionSettings,
    PaymentLimit,
    QuietHours,
    SettingsError,
    from_json,
    merge,
)


def test_the_defaults_say_meetings_on_the_rest_off() -> None:
    s = DEFAULT
    assert s.watchers == {"meetings": True, "email": False, "payments": False}
    assert s.meeting_lead_min == 5 and s.payment_limit is None
    assert s.quiet_hours == QuietHours(on=True, start="22:00", end="08:00")
    assert s.mic and s.focus
    assert s.on("meetings") and not s.on("email") and not s.on("nobody")


def test_it_round_trips_through_json() -> None:
    s = merge(
        DEFAULT,
        {
            "watchers": {"email": True},
            "meeting_lead_min": 10,
            "payment_limit": {"amount_minor": 50_000, "currency": "USD"},
            "quiet_hours": {"on": False, "from": "23:30", "to": "06:00"},
            "mic": False,
        },
    )
    assert s.watchers == {"meetings": True, "email": True, "payments": False}
    assert s.payment_limit == PaymentLimit(amount_minor=50_000, currency="usd")
    assert s.quiet_hours == QuietHours(on=False, start="23:30", end="06:00")
    assert from_json(s.to_json()) == s
    assert s.to_json()["payment_limit"] == {"amount_minor": 50_000, "currency": "usd"}


def test_nothing_saved_or_something_broken_reads_as_the_defaults() -> None:
    assert from_json(None) == DEFAULT
    assert from_json("nonsense") == DEFAULT
    # One bad field falls back alone; the rest is kept.
    s = from_json({"meeting_lead_min": 99, "mic": False})
    assert s.meeting_lead_min == 5 and s.mic is False


@pytest.mark.parametrize(
    ("patch", "says"),
    [
        ({"meeting_lead_min": 0}, "meeting_lead_min"),
        ({"meeting_lead_min": 31}, "meeting_lead_min"),
        ({"meeting_lead_min": True}, "meeting_lead_min"),
        ({"watchers": {"slack": True}}, "slack"),
        ({"watchers": {"email": "yes"}}, "email"),
        ({"payment_limit": {"amount_minor": 0, "currency": "usd"}}, "amount_minor"),
        ({"payment_limit": {"amount_minor": 100, "currency": "dollars"}}, "currency"),
        ({"payment_limit": 100}, "payment_limit"),
        ({"quiet_hours": {"from": "25:00"}}, "from"),
        ({"quiet_hours": {"to": "8am"}}, "to"),
        ({"quiet_hours": {"on": 1}}, "on"),
        ({"quiet_hours": {"from": "08:00"}}, "from and to must differ"),
        ({"quiet_hours": {"from": "09:30", "to": "09:30"}}, "from and to must differ"),
        ({"mic": "on"}, "mic"),
        ({"loud": True}, "loud"),
    ],
)
def test_a_bad_change_is_refused_by_name(patch: dict[str, Any], says: str) -> None:
    with pytest.raises(SettingsError, match=says):
        merge(DEFAULT, patch)


def test_the_payment_limit_can_be_turned_off_again() -> None:
    s = merge(DEFAULT, {"payment_limit": {"amount_minor": 100, "currency": "eur"}})
    assert merge(s, {"payment_limit": None}).payment_limit is None


def test_it_is_a_value() -> None:
    assert isinstance(DEFAULT, AttentionSettings)
    assert merge(DEFAULT, {}) == DEFAULT
