"""The Focus probe: every system's logic on recorded outputs, on every runner."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from trillion.attention.probes import focus

# Shape of ~/Library/DoNotDisturb/DB/Assertions.json with Do Not Disturb on by hand.
ASSERTED = {
    "data": [
        {
            "storeAssertionRecords": [
                {
                    "assertionUUID": "5D3C7A0E-2B1F-4C59-9B5E-8E1A3F6D2C40",
                    "assertionStartDateTimestamp": 783412345.12,
                    "assertionSource": {"assertionClientIdentifier": "com.apple.controlcenter"},
                    "assertionDetails": {
                        "assertionDetailsModeIdentifier": "com.apple.donotdisturb.mode.default",
                        "assertionDetailsReason": "user-action",
                        "assertionDetailsIdentifier": "com.apple.controlcenter.dnd",
                    },
                }
            ],
            "storeInvalidationRecords": [],
        }
    ],
    "header": {"timestamp": 783412345.2, "version": 3},
}
CLEARED = {
    "data": [{"storeInvalidationRecords": [{"invalidationReason": 2}]}],
    "header": {"timestamp": 783419999.0, "version": 3},
}


def reader(text: str | None = None, error: OSError | None = None) -> focus.Read:
    def read(path: Path) -> str:
        assert path == Path("/home/sam") / focus.ASSERTIONS
        if error is not None:
            raise error
        assert text is not None
        return text

    return read


HOME = Path("/home/sam")


def test_macos_focus_on() -> None:
    assert focus._macos(reader(json.dumps(ASSERTED)), HOME) is True


def test_macos_focus_off() -> None:
    assert focus._macos(reader(json.dumps(CLEARED)), HOME) is False


@pytest.mark.parametrize(
    "error", [PermissionError(1, "Operation not permitted"), FileNotFoundError(2, "gone")]
)
def test_macos_unreadable_cannot_tell(error: OSError) -> None:
    assert focus._macos(reader(error=error), HOME) is None


@pytest.mark.parametrize("garbage", ["", "{", "[]", '{"data": 3}', "null"])
def test_macos_garbage_cannot_tell(garbage: str) -> None:
    assert focus._macos(reader(garbage), HOME) is None


def test_windows_cannot_tell() -> None:
    assert focus._windows() is None


class FakeRun:
    def __init__(self, gsettings: str | None = None, gdbus: str | None = None) -> None:
        self.out = {"gsettings": gsettings, "gdbus": gdbus}
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> str | None:
        self.calls.append(list(args))
        return self.out[args[0]]


def test_gnome_banners_off_is_focus_on() -> None:
    run = FakeRun(gsettings="false\n")
    assert focus._linux(run) is True
    assert run.calls == [focus.GSETTINGS]


def test_gnome_banners_on_and_no_daemon_property_is_off() -> None:
    assert focus._linux(FakeRun(gsettings="true\n")) is False


def test_banners_on_but_daemon_inhibited_is_on() -> None:
    # KDE with GLib installed: gsettings answers GNOME's default, the daemon knows.
    assert focus._linux(FakeRun(gsettings="true\n", gdbus="(<true>,)\n")) is True


@pytest.mark.parametrize(("out", "answer"), [("(<true>,)\n", True), ("(<false>,)\n", False)])
def test_gdbus_without_gsettings(out: str, answer: bool) -> None:
    run = FakeRun(gdbus=out)
    assert focus._linux(run) is answer
    assert run.calls == [focus.GSETTINGS, focus.GDBUS]


def test_neither_cannot_tell() -> None:
    assert focus._linux(FakeRun()) is None


@pytest.mark.parametrize("garbage", ["", "No such key “show-banners”", "(<uint32 1>,)"])
def test_garbage_cannot_tell(garbage: str) -> None:
    assert focus._linux(FakeRun(gsettings=garbage, gdbus=garbage)) is None
