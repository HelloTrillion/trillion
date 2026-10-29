"""Is Focus or Do Not Disturb on? One function per system, best effort.

Windows has no documented signal a user process can read: `SHQueryUserNotificationState`
is documented but describes presentation mode, full-screen apps and first-login quiet
time, not Do Not Disturb; the CloudStore blob and the WNF state that do hold it are
undocumented. So Windows says can't tell.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Callable
from pathlib import Path

from trillion.attention.probes import CACHE, Answer, Run, guarded, run

logger = logging.getLogger(__name__)

Read = Callable[[Path], str]

ASSERTIONS = Path("Library/DoNotDisturb/DB/Assertions.json")
GSETTINGS = ["gsettings", "get", "org.gnome.desktop.notifications", "show-banners"]
GDBUS = [
    "gdbus", "call", "--session",
    "--dest", "org.freedesktop.Notifications",
    "--object-path", "/org/freedesktop/Notifications",
    "--method", "org.freedesktop.DBus.Properties.Get",
    "org.freedesktop.Notifications", "Inhibited",
]  # fmt: skip
_GDBUS_ANSWER = re.compile(r"<\s*(true|false)\s*>")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _macos(read: Read, home: Path) -> Answer:
    # A Focus turned on by hand is an assertion in this file. Recent macOS guards
    # the folder (Full Disk Access), so most processes get PermissionError here.
    try:
        text = read(home / ASSERTIONS)
    except OSError as e:
        logger.debug("Focus state unreadable: %s", type(e).__name__)
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    entries = data.get("data") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, dict) and entry.get("storeAssertionRecords"):
            return True
    # A scheduled or automatic Focus leaves no assertion; this misses it.
    return False


def _windows() -> Answer:
    return None


def _gdbus(run: Run) -> Answer:
    text = run(GDBUS)
    match = _GDBUS_ANSWER.search(text) if text is not None else None
    return None if match is None else match.group(1) == "true"


def _linux(run: Run) -> Answer:
    banners = run(GSETTINGS)
    value = banners.strip() if banners is not None else None
    if value == "false":
        return True
    # gsettings answers on any desktop with GLib, so "banners on" may be GNOME's
    # default on a desktop that keeps Do Not Disturb elsewhere: ask the daemon too.
    inhibited = _gdbus(run)
    if inhibited is not None:
        return inhibited
    return False if value == "true" else None


def _system() -> Answer:
    if sys.platform == "darwin":
        return _macos(_read, Path.home())
    if sys.platform == "win32":
        return _windows()
    if sys.platform.startswith("linux"):
        return _linux(run)
    return None


def focus_on() -> Answer:
    """True while the system's Focus or Do Not Disturb is on; None when it can't tell."""
    return CACHE.get(("focus", ""), lambda: guarded("focus", _system))
