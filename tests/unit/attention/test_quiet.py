"""When she holds her voice (D8): quiet hours for watcher notices only, a meeting, another
app on the mic, Focus. Each probe is a fake here; can't tell never holds her voice."""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from datetime import datetime

import pytest

from tests.scheduler.conftest import NY, ms
from trillion.attention import quiet as quiet_module
from trillion.attention.probes import Answer
from trillion.attention.probes import focus as focus_probe
from trillion.attention.probes import mic as mic_probe
from trillion.attention.quiet import FOCUS, MEETING, MIC, QUIET_HOURS, Quiet, Threads, probe_answer
from trillion.attention.settings import DEFAULT, AttentionSettings, QuietHours
from trillion.scheduler.store import Notice


def notice(source: str = "attention", key: str = "email:m1") -> Notice:
    return Notice(
        id="n1",
        owner_id="local",
        source_id=key,
        kind="heads_up" if source == "attention" else "reminder",
        title="From Sam",
        text="Sam wrote.",
        due_ms=0,
        late=False,
        created_ms=0,
        status="held",
        delivered_ms=None,
        delivered_to=None,
        notified_ms=None,
        source=source,  # type: ignore[arg-type]
    )


class Probes:
    def __init__(self, mic: Answer = False, focus: Answer = False) -> None:
        self.mic_answer = mic
        self.focus_answer = focus
        self.hosts: list[str] = []
        self.focus_calls = 0

    def mic(self, host: str) -> Answer:
        self.hosts.append(host)
        return self.mic_answer

    def focus(self) -> Answer:
        self.focus_calls += 1
        return self.focus_answer


def quiet(
    probes: Probes,
    settings: AttentionSettings = DEFAULT,
    *,
    zone: str | None = NY,
    host: str | None = "chrome",
    meeting: Answer = None,
) -> Quiet:
    q = Quiet(
        settings=lambda: settings,
        zone=lambda: zone,
        host=lambda: host,
        mic=probes.mic,
        focus=probes.focus,
        probe_timeout_s=0.2,
    )
    q.in_meeting = lambda: meeting
    return q


NOON = ms("2026-10-29T12:00")


async def test_nothing_holds_her_voice_at_noon() -> None:
    assert await quiet(Probes()).reason(notice(), NOON) is None


@pytest.mark.parametrize(
    ("local", "held"),
    [
        ("2026-10-29T21:59", False),
        ("2026-10-29T22:00", True),
        ("2026-10-29T23:59", True),
        ("2026-10-30T00:30", True),
        ("2026-10-30T07:59", True),
        ("2026-10-30T08:00", False),
    ],
)
async def test_quiet_hours_cross_midnight_in_the_users_zone(local: str, held: bool) -> None:
    got = await quiet(Probes()).reason(notice(), ms(local, NY))
    assert got == (QUIET_HOURS if held else None)


@pytest.mark.parametrize(
    ("utc", "held"),
    [
        # 2026-11-01 in New York: 1:30 comes twice, once in EDT and once in EST.
        ("2026-11-01T05:30:00+00:00", True),
        ("2026-11-01T06:30:00+00:00", True),
        # 7:00 EST: with summer time's offset it would read 8:00, and be let through.
        ("2026-11-01T12:00:00+00:00", True),
        ("2026-11-01T12:59:00+00:00", True),
        ("2026-11-01T13:00:00+00:00", False),
    ],
)
async def test_quiet_hours_on_the_night_the_clocks_go_back(utc: str, held: bool) -> None:
    at = int(datetime.fromisoformat(utc).timestamp() * 1000)
    got = await quiet(Probes()).reason(notice(), at)
    assert got == (QUIET_HOURS if held else None)


async def test_quiet_hours_are_told_in_the_users_zone_not_the_computers() -> None:
    # 21:00 in New York is 01:00 in London: only the zone tells them apart.
    at = ms("2026-10-29T21:00", NY)
    assert await quiet(Probes(), zone=NY).reason(notice(), at) is None
    assert await quiet(Probes(), zone="Europe/London").reason(notice(), at) == QUIET_HOURS


async def test_quiet_hours_within_one_day() -> None:
    s = replace(DEFAULT, quiet_hours=QuietHours(on=True, start="12:00", end="13:00"))
    assert await quiet(Probes(), s).reason(notice(), NOON) == QUIET_HOURS
    assert await quiet(Probes(), s).reason(notice(), ms("2026-10-29T13:00")) is None


async def test_quiet_hours_off_or_no_zone_skip_that_reason() -> None:
    late = ms("2026-10-29T23:00")
    off = replace(DEFAULT, quiet_hours=QuietHours(on=False, start="22:00", end="08:00"))
    assert await quiet(Probes(), off).reason(notice(), late) is None
    assert await quiet(Probes(), zone=None).reason(notice(), late) is None
    assert await quiet(Probes(), zone="Not/AZone").reason(notice(), late) is None


async def test_a_reminder_is_not_held_by_quiet_hours_but_is_by_the_mic() -> None:
    late = ms("2026-10-29T23:00")
    assert await quiet(Probes()).reason(notice("schedule"), late) is None
    assert await quiet(Probes(mic=True)).reason(notice("schedule"), late) == MIC


async def test_in_a_meeting_holds_her_voice() -> None:
    assert await quiet(Probes(), meeting=True).reason(notice(), NOON) == MEETING
    assert await quiet(Probes(), meeting=None).reason(notice(), NOON) is None
    assert await quiet(Probes(), meeting=False).reason(notice(), NOON) is None


async def test_the_meeting_switch_turns_that_reason_off() -> None:
    off = replace(DEFAULT, meeting=False)
    assert await quiet(Probes(), off, meeting=True).reason(notice(), NOON) is None
    # The other reasons still hold.
    assert await quiet(Probes(mic=True), off, meeting=True).reason(notice(), NOON) == MIC


async def test_a_heads_up_for_the_next_meeting_is_still_spoken_in_one() -> None:
    q = quiet(Probes(), meeting=True)
    assert await q.reason(notice(key="meeting:e2:1800000000000"), NOON) is None


async def test_the_mic_probe_is_asked_with_the_live_pages_host() -> None:
    p = Probes(mic=True)
    assert await quiet(p, host="desktop").reason(notice(), NOON) == MIC
    assert p.hosts == ["desktop"]
    p = Probes(mic=True)
    await quiet(p, host=None).reason(notice(), NOON)
    assert p.hosts == ["other"]


async def test_focus_holds_her_voice() -> None:
    assert await quiet(Probes(focus=True)).reason(notice(), NOON) == FOCUS


async def test_cant_tell_never_holds_her_voice() -> None:
    assert await quiet(Probes(mic=None, focus=None)).reason(notice(), NOON) is None


async def test_the_mic_and_focus_switches_turn_their_checks_off() -> None:
    p = Probes(mic=True, focus=True)
    off = replace(DEFAULT, mic=False, focus=False)
    assert await quiet(p, off).reason(notice(), NOON) is None
    assert p.hosts == [] and p.focus_calls == 0


async def test_reasons_come_in_order() -> None:
    late = ms("2026-10-29T23:00")
    p = Probes(mic=True, focus=True)
    assert await quiet(p, meeting=True).reason(notice(), late) == QUIET_HOURS
    assert await quiet(p, meeting=True).reason(notice(), NOON) == MEETING
    assert await quiet(p).reason(notice(), NOON) == MIC


async def test_a_probe_that_hangs_is_cant_tell() -> None:
    def slow(host: str) -> Answer:
        time.sleep(1.0)
        return True

    q = Quiet(
        settings=lambda: DEFAULT,
        zone=lambda: NY,
        host=lambda: "chrome",
        mic=slow,
        focus=lambda: None,
        probe_timeout_s=0.05,
    )
    started = time.monotonic()
    assert await q.reason(notice(), NOON) is None
    assert time.monotonic() - started < 0.8


async def test_a_probe_that_raises_is_cant_tell() -> None:
    def broken(host: str) -> Answer:
        raise OSError("no")

    q = Quiet(
        settings=lambda: DEFAULT,
        zone=lambda: NY,
        host=lambda: "chrome",
        mic=broken,
        focus=lambda: None,
    )
    assert await q.reason(notice(), NOON) is None


def test_the_reasons_are_short_words() -> None:
    assert (QUIET_HOURS, MEETING, MIC, FOCUS) == (
        "quiet hours",
        "in a meeting",
        "another app is using the microphone",
        "Focus is on",
    )


def test_a_default_quiet_asks_this_computers_real_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    # The suite's autouse fixture puts "can't tell" in their place; undone, the wiring
    # is the real entry points.
    monkeypatch.undo()
    assert quiet_module.SYSTEM_MIC is mic_probe.mic_in_use
    assert quiet_module.SYSTEM_FOCUS is focus_probe.focus_on
    q = Quiet(settings=lambda: DEFAULT, zone=lambda: NY, host=lambda: "chrome")
    assert q.mic is mic_probe.mic_in_use and q.focus is focus_probe.focus_on


def test_probe_threads_are_daemons_and_at_most_two() -> None:
    gate = threading.Event()
    seen: list[threading.Thread] = []

    def hang() -> Answer:
        seen.append(threading.current_thread())
        gate.wait(5)
        return True

    threads = Threads(2)
    try:
        first, second = threads.submit(hang), threads.submit(hang)
        assert first is not None and second is not None
        # Both busy: nothing more is started.
        assert threads.submit(hang) is None
    finally:
        gate.set()
    assert first.result(2) is True and second.result(2) is True
    assert all(t.daemon for t in seen)
    # Free again once they finish.
    again = threads.submit(lambda: False)
    assert again is not None and again.result(2) is False


async def test_a_probe_that_times_out_or_finds_every_thread_busy_did_not_answer() -> None:
    gate = threading.Event()

    def hang() -> Answer:
        gate.wait(5)
        return True

    threads = Threads(1)
    try:
        assert await probe_answer("mic", hang, timeout_s=0.05, threads=threads) == (None, False)
        # Still hung: the one thread is busy.
        assert await probe_answer("mic", lambda: True, threads=threads) == (None, False)
    finally:
        gate.set()
    assert await probe_answer("focus", lambda: None, threads=Threads(1)) == (None, True)
    assert await probe_answer("focus", lambda: True, threads=Threads(1)) == (True, True)
