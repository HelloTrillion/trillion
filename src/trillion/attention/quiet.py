"""Whether to hold her voice for a notice right now (D7, D8): the door's real `Quiet`.

The reasons, in order, the first that applies:

1. Quiet hours, in the user's zone as reminders tell it. Watcher notices only: a
   reminder or timed ask was set for its time by the user. An unknown zone skips it.
2. In a meeting, from the meetings watcher's last read (`in_meeting`, can't tell
   until it is filled). A heads-up for the next meeting is still spoken.
3. Another app is using the microphone (the mic probe, told the live page's host).
4. Focus is on (the Focus probe).

Can't tell never holds her voice (D11). A probe runs on a small executor of its own,
never the default one the door's notifications use, with a hard limit, and the two
are asked together, so a probe that hangs can't stop the door; the probes themselves
run one at a time per question.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from trillion.attention.probes import Answer
from trillion.attention.probes.focus import focus_on
from trillion.attention.probes.mic import mic_in_use
from trillion.attention.settings import AttentionSettings, QuietHours
from trillion.scheduler import when
from trillion.scheduler.store import Notice

logger = logging.getLogger(__name__)

QUIET_HOURS = "quiet hours"
MEETING = "in a meeting"
MIC = "another app is using the microphone"
FOCUS = "Focus is on"
# A meeting watcher's find keys start so: its heads-up is about the next meeting.
MEETING_KEY = "meeting:"
# Both probes together, inside the door's own 2.5 s limit on the whole check.
PROBE_TIMEOUT_S = 2.0

# This computer's probes. Module names, so the test suite can put "can't tell" in
# their place everywhere: no test may depend on what this computer is doing.
SYSTEM_MIC: Callable[[str], Answer] = mic_in_use
SYSTEM_FOCUS: Callable[[], Answer] = focus_on

# Two: one probe hung on a system call still leaves room for the other.
_PROBES = ThreadPoolExecutor(max_workers=2, thread_name_prefix="attention-probe")


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_quiet_hours(hours: QuietHours, zone: str | None, now_ms: int) -> bool:
    if not hours.on or not zone:
        return False
    try:
        tz = when.zone_of(zone)
    except when.WhenError:
        return False
    local = datetime.fromtimestamp(now_ms / 1000, UTC).astimezone(tz)
    now, start, end = local.hour * 60 + local.minute, _minutes(hours.start), _minutes(hours.end)
    if start == end:
        return False
    # From after to: the hours cross midnight (22:00-08:00).
    return start <= now < end if start < end else now >= start or now < end


async def ask_probe(
    name: str, probe: Callable[..., Answer], *args: str, timeout_s: float = PROBE_TIMEOUT_S
) -> Answer:
    """A probe's answer from its own executor, or None for anything else."""
    loop = asyncio.get_running_loop()
    try:
        answer = await asyncio.wait_for(loop.run_in_executor(_PROBES, probe, *args), timeout_s)
    except Exception as e:
        logger.info("%s probe can't tell: %s", name, type(e).__name__)
        return None
    return answer if isinstance(answer, bool) else None


async def _off() -> Answer:
    """A check switched off in Settings: never a reason."""
    return None


class Quiet:
    def __init__(
        self,
        *,
        settings: Callable[[], AttentionSettings],
        zone: Callable[[], str | None],
        host: Callable[[], str | None],
        mic: Callable[[str], Answer] | None = None,
        focus: Callable[[], Answer] | None = None,
        probe_timeout_s: float = PROBE_TIMEOUT_S,
    ) -> None:
        self._settings = settings
        self._zone = zone
        # Where the live page runs (`desktop`, `chrome`...), for the mic probe.
        self.host = host
        # Public so a test, and Settings' probe status, use the same ones.
        self.mic = mic or SYSTEM_MIC
        self.focus = focus or SYSTEM_FOCUS
        self._timeout = probe_timeout_s
        # The meetings watcher puts its last read here: True while one is under way.
        self.in_meeting: Callable[[], Answer] = lambda: None

    async def reason(self, notice: Notice, now_ms: int) -> str | None:
        s = self._settings()
        watcher = notice.source == "attention"
        if watcher and in_quiet_hours(s.quiet_hours, self._zone(), now_ms):
            return QUIET_HOURS
        next_meeting = watcher and notice.source_id.startswith(MEETING_KEY)
        if not next_meeting and self._meeting() is True:
            return MEETING
        # Asked together, so both fit in one limit; the first reason still wins.
        mic, focus = await asyncio.gather(
            ask_probe("mic", self.mic, self.page_host(), timeout_s=self._timeout)
            if s.mic
            else _off(),
            ask_probe("focus", self.focus, timeout_s=self._timeout) if s.focus else _off(),
        )
        if mic is True:
            return MIC
        if focus is True:
            return FOCUS
        return None

    def page_host(self) -> str:
        try:
            return self.host() or "other"
        except Exception:
            return "other"

    def _meeting(self) -> Answer:
        try:
            return self.in_meeting()
        except Exception as e:
            logger.info("meeting check can't tell: %s", type(e).__name__)
            return None
