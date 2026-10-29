"""Questions for the operating system: is another app on the mic, is Focus on (D11).

Each probe answers yes, no or can't tell (None), never raises and never waits more
than a couple of seconds. `mic.mic_in_use(host)` and `focus.focus_on()` are the entry
points; each system's logic takes its system calls as parameters so every system is
tested on every runner. Callers run them in a thread: they are synchronous.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence

logger = logging.getLogger(__name__)

Answer = bool | None
# Runs one command; its stdout, or None when it is missing, fails or times out.
Run = Callable[[Sequence[str]], str | None]

TIMEOUT_S = 2.0
TTL_S = 10.0
# Windows: no console window (OPS-3); spelled out so it exists on every platform.
CREATE_NO_WINDOW = 0x08000000


def run(args: Sequence[str]) -> str | None:
    """Run an argument list, never a shell, with the probe timeout."""
    exe = shutil.which(args[0])
    if exe is None:
        return None
    try:
        done = subprocess.run(  # noqa: S603 - fixed argument lists, no shell
            [exe, *args[1:]],
            check=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_S,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        logger.info("probe command %s failed: %s", args[0], type(e).__name__)
        return None
    if done.returncode != 0:
        logger.debug("probe command %s exited %s", args[0], done.returncode)
        return None
    return done.stdout


def guarded(name: str, probe: Callable[[], Answer]) -> Answer:
    """The probe's answer; None for anything that goes wrong, logged by class name."""
    try:
        answer = probe()
    except Exception as e:
        logger.info("%s probe can't tell: %s", name, type(e).__name__)
        return None
    return answer if isinstance(answer, bool) else None


class Cache:
    """Answers kept for TTL_S per key. Probes run from several threads, so it locks."""

    def __init__(self, ttl_s: float = TTL_S, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._kept: dict[tuple[str, str], tuple[float, Answer]] = {}

    def get(self, key: tuple[str, str], compute: Callable[[], Answer]) -> Answer:
        now = self._clock()
        with self._lock:
            hit = self._kept.get(key)
        if hit is not None and now - hit[0] < self._ttl:
            return hit[1]
        # Computed outside the lock: a slow probe must not hold up other keys.
        answer = compute()
        with self._lock:
            self._kept[key] = (self._clock(), answer)
        return answer

    def clear(self) -> None:
        with self._lock:
            self._kept.clear()


CACHE = Cache()
