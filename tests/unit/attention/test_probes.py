"""What every probe shares: commands, the guard, the cache, and the real thing here."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Sequence
from typing import Any

import pytest

from trillion.attention import probes
from trillion.attention.probes import focus, mic


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[None]:
    probes.CACHE.clear()
    yield
    probes.CACHE.clear()


# --- run --------------------------------------------------------------------


def fake_subprocess(
    monkeypatch: pytest.MonkeyPatch, result: Any = None, raises: Exception | None = None
) -> list[tuple[list[str], dict[str, Any]]]:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake(args: list[str], **kw: Any) -> Any:
        calls.append((args, kw))
        if raises is not None:
            raise raises
        return result

    monkeypatch.setattr(probes.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(probes.subprocess, "run", fake)
    return calls


def test_run_uses_an_argument_list_and_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    done = subprocess.CompletedProcess(["x"], 0, stdout="ok\n")
    calls = fake_subprocess(monkeypatch, result=done)
    assert probes.run(["pactl", "-f", "json"]) == "ok\n"
    args, kw = calls[0]
    assert args == ["/bin/pactl", "-f", "json"]
    assert kw["timeout"] == probes.TIMEOUT_S == 2.0
    assert kw["stdin"] is subprocess.DEVNULL
    assert "shell" not in kw


def test_run_missing_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probes.shutil, "which", lambda name: None)
    assert probes.run(["pw-dump"]) is None


def test_run_timeout(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    fake_subprocess(monkeypatch, raises=subprocess.TimeoutExpired(["pactl"], 2.0, output="secret"))
    assert probes.run(["pactl"]) is None
    assert "TimeoutExpired" in caplog.text
    assert "secret" not in caplog.text


def test_run_os_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_subprocess(monkeypatch, raises=PermissionError("denied"))
    assert probes.run(["gdbus"]) is None


def test_run_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_subprocess(monkeypatch, result=subprocess.CompletedProcess(["x"], 1, stdout="junk"))
    assert probes.run(["gsettings"]) is None


# --- guard ------------------------------------------------------------------


def test_guard_turns_any_error_into_cannot_tell(caplog: pytest.LogCaptureFixture) -> None:
    def broken() -> probes.Answer:
        raise RuntimeError("details that must not be logged")

    caplog.set_level(logging.INFO)
    assert probes.guarded("mic", broken) is None
    assert "RuntimeError" in caplog.text
    assert "details" not in caplog.text


def test_guard_keeps_only_bool_or_none() -> None:
    assert probes.guarded("x", lambda: True) is True
    assert probes.guarded("x", lambda: None) is None
    assert probes.guarded("x", lambda: 1) is None  # type: ignore[arg-type, return-value]


# --- cache ------------------------------------------------------------------


def test_cache_keeps_an_answer_ten_seconds_per_key() -> None:
    now = [100.0]
    cache = probes.Cache(clock=lambda: now[0])
    count = {"n": 0}

    def compute() -> probes.Answer:
        count["n"] += 1
        return count["n"] % 2 == 1

    assert cache.get(("mic", "chrome"), compute) is True
    now[0] += 9.9
    assert cache.get(("mic", "chrome"), compute) is True
    assert cache.get(("mic", "desktop"), compute) is False
    now[0] += 0.2
    assert cache.get(("mic", "chrome"), compute) is True
    assert count["n"] == 3
    cache.clear()
    assert cache.get(("mic", "chrome"), compute) is False


def test_cache_is_safe_across_threads() -> None:
    cache = probes.Cache()
    results: list[probes.Answer] = []

    def worker(i: int) -> None:
        for j in range(200):
            results.append(cache.get(("p", str((i + j) % 5)), lambda: True))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [True] * 1600


def test_a_probe_runs_once_at_a_time_per_key() -> None:
    """A second caller while one is running gets the last answer (or can't tell) and
    starts nothing: a hung system call ties up one thread, never a pile of them."""
    cache = probes.Cache(ttl_s=0.0)
    started, release = threading.Event(), threading.Event()
    count = {"n": 0}

    def slow() -> probes.Answer:
        count["n"] += 1
        started.set()
        release.wait(5)
        return True

    first = threading.Thread(target=lambda: cache.get(("mic", "x"), slow))
    first.start()
    assert started.wait(5)
    assert cache.get(("mic", "x"), slow) is None
    release.set()
    first.join()
    assert count["n"] == 1
    # Done: the next caller runs it again (the answer expired at once here), and one
    # who comes while that runs gets the last answer.
    started.clear()
    release.clear()
    again = threading.Thread(target=lambda: cache.get(("mic", "x"), slow))
    again.start()
    assert started.wait(5)
    assert cache.get(("mic", "x"), slow) is True
    release.set()
    again.join()


def test_a_chain_of_commands_shares_one_deadline() -> None:
    now = [0.0]
    asked: list[tuple[str, float]] = []

    def run_for(args: Sequence[str], timeout_s: float) -> str | None:
        asked.append((args[0], timeout_s))
        now[0] += 1.5  # each one takes most of the time it may
        return None

    bounded = probes.within(2.0, run_for, clock=lambda: now[0])
    assert bounded(["pactl"]) is None and bounded(["pw-dump"]) is None
    assert bounded(["more"]) is None
    assert asked == [("pactl", 2.0), ("pw-dump", 0.5)]


def test_entry_points_cache_and_never_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def broken(host: str) -> probes.Answer:
        calls.append(host)
        raise OSError("no audio")

    monkeypatch.setattr(mic, "_system", broken)
    assert mic.mic_in_use("Chrome") is None
    assert mic.mic_in_use("chrome") is None
    assert mic.mic_in_use("netscape") is None
    assert calls == ["chrome", "other"]

    monkeypatch.setattr(focus, "_system", lambda: True)
    assert focus.focus_on() is True


# --- the real system ---------------------------------------------------------


@pytest.mark.parametrize("host", ["desktop", "chrome", "other"])
def test_real_mic_probe_answers_in_time(host: str) -> None:
    start = time.monotonic()
    answer = mic.mic_in_use(host)
    assert answer is None or isinstance(answer, bool)
    assert time.monotonic() - start < 3.0


def test_real_focus_probe_answers_in_time() -> None:
    start = time.monotonic()
    answer = focus.focus_on()
    assert answer is None or isinstance(answer, bool)
    assert time.monotonic() - start < 3.0
    if sys.platform == "win32":
        assert answer is None
