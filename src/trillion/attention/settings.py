"""Settings → Speaking up: one JSON preference (the speaking up spec).

Its shape, as saved and as the page sends it (every key optional on a change):

    {
      "watchers": {"meetings": true, "email": false, "payments": false},
      "meeting_lead_min": 5,                       # 1-30
      "payment_limit": null | {"amount_minor": 50000, "currency": "usd"},
      "quiet_hours": {"on": true, "from": "22:00", "to": "08:00"},
      "mic": true,                                 # hold for another app on the mic
      "focus": true                                # hold while Focus is on
    }

The payment limit is in the currency's minor unit (cents for usd), as Stripe counts,
so no amount is ever a float; None is off. A saved value is read leniently, field by
field, since a bad one must not switch everything off; a change is checked strictly
and refused with the field it is about.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

PREF = "attention"
WATCHERS = ("meetings", "email", "payments")
# Meetings are on once Calendar is connected; email and payments send content out, so
# they wait for the user (D2).
_WATCHERS_ON: Mapping[str, bool] = MappingProxyType(
    {"meetings": True, "email": False, "payments": False}
)
LEAD_MIN, LEAD_MAX = 1, 30
_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_CURRENCY = re.compile(r"^[a-z]{3}$")
_KEYS = frozenset({"watchers", "meeting_lead_min", "payment_limit", "quiet_hours", "mic", "focus"})


class SettingsError(ValueError):
    """A change that can't be saved; the message names the field."""


@dataclass(frozen=True, kw_only=True)
class QuietHours:
    on: bool = True
    # "HH:MM" in the user's zone; from after to means the hours cross midnight.
    start: str = "22:00"
    end: str = "08:00"


@dataclass(frozen=True, kw_only=True)
class PaymentLimit:
    amount_minor: int
    currency: str


@dataclass(frozen=True, kw_only=True)
class AttentionSettings:
    watchers: Mapping[str, bool] = field(default_factory=lambda: dict(_WATCHERS_ON))
    meeting_lead_min: int = 5
    payment_limit: PaymentLimit | None = None
    quiet_hours: QuietHours = field(default_factory=QuietHours)
    mic: bool = True
    focus: bool = True

    def on(self, watcher: str) -> bool:
        return bool(self.watchers.get(watcher, False))

    def to_json(self) -> dict[str, Any]:
        limit = self.payment_limit
        return {
            "watchers": {name: self.on(name) for name in WATCHERS},
            "meeting_lead_min": self.meeting_lead_min,
            "payment_limit": (
                {"amount_minor": limit.amount_minor, "currency": limit.currency}
                if limit is not None
                else None
            ),
            "quiet_hours": {
                "on": self.quiet_hours.on,
                "from": self.quiet_hours.start,
                "to": self.quiet_hours.end,
            },
            "mic": self.mic,
            "focus": self.focus,
        }


DEFAULT = AttentionSettings()


def _bool(raw: object, name: str) -> bool:
    if not isinstance(raw, bool):
        raise SettingsError(f"{name} must be true or false")
    return raw


def _watchers(current: Mapping[str, bool], raw: object) -> dict[str, bool]:
    if not isinstance(raw, dict):
        raise SettingsError("watchers must be an object of switches")
    out = {name: bool(current.get(name, False)) for name in WATCHERS}
    for name, value in raw.items():
        if name not in WATCHERS:
            raise SettingsError(f"{name} is not a watcher; there are {', '.join(WATCHERS)}")
        out[name] = _bool(value, name)
    return out


def _lead(raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or not LEAD_MIN <= raw <= LEAD_MAX:
        raise SettingsError(
            f"meeting_lead_min must be a whole number from {LEAD_MIN} to {LEAD_MAX}"
        )
    return raw


def _limit(raw: object) -> PaymentLimit | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {"amount_minor", "currency"}:
        raise SettingsError("payment_limit must be null or {amount_minor, currency}")
    amount, currency = raw["amount_minor"], raw["currency"]
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        raise SettingsError("amount_minor must be a positive whole number, in cents or the like")
    if not isinstance(currency, str) or not _CURRENCY.fullmatch(currency.strip().lower()):
        raise SettingsError("currency must be a three-letter code such as usd")
    return PaymentLimit(amount_minor=amount, currency=currency.strip().lower())


def _clock(raw: object, name: str) -> str:
    if not isinstance(raw, str) or not _CLOCK.fullmatch(raw):
        raise SettingsError(f"{name} must be a time such as 22:00")
    return raw


def _quiet(current: QuietHours, raw: object) -> QuietHours:
    if not isinstance(raw, dict):
        raise SettingsError("quiet_hours must be {on, from, to}")
    unknown = set(raw) - {"on", "from", "to"}
    if unknown:
        raise SettingsError(f"{sorted(unknown)[0]} is not part of quiet_hours")
    hours = QuietHours(
        on=_bool(raw["on"], "on") if "on" in raw else current.on,
        start=_clock(raw["from"], "from") if "from" in raw else current.start,
        end=_clock(raw["to"], "to") if "to" in raw else current.end,
    )
    if hours.start == hours.end:
        # Either all day or never: neither is what anyone means.
        raise SettingsError("quiet_hours from and to must differ")
    return hours


def merge(current: AttentionSettings, patch: object) -> AttentionSettings:
    """`current` with the change applied. Raises `SettingsError` naming what is wrong."""
    if not isinstance(patch, dict):
        raise SettingsError("settings must be an object")
    unknown = set(patch) - _KEYS
    if unknown:
        raise SettingsError(f"{sorted(unknown)[0]} is not a setting here")
    s = current
    if "watchers" in patch:
        s = replace(s, watchers=_watchers(s.watchers, patch["watchers"]))
    if "meeting_lead_min" in patch:
        s = replace(s, meeting_lead_min=_lead(patch["meeting_lead_min"]))
    if "payment_limit" in patch:
        s = replace(s, payment_limit=_limit(patch["payment_limit"]))
    if "quiet_hours" in patch:
        s = replace(s, quiet_hours=_quiet(s.quiet_hours, patch["quiet_hours"]))
    if "mic" in patch:
        s = replace(s, mic=_bool(patch["mic"], "mic"))
    if "focus" in patch:
        s = replace(s, focus=_bool(patch["focus"], "focus"))
    return s


def from_json(raw: Any) -> AttentionSettings:
    """What was saved, field by field: one that no longer reads is its default."""
    s = DEFAULT
    if not isinstance(raw, dict):
        return s
    for key in _KEYS & set(raw):
        try:
            s = merge(s, {key: raw[key]})
        except SettingsError:
            continue
    return s
