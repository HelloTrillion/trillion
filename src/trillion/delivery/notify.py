"""System notifications, best effort (SUR-9). Only the door calls this.

Each platform's own tool, run as an argument list and never through a shell, with
the words passed as arguments or as environment values, never spliced into a
script. A notification that can't be shown is logged by its kind and returns
False: the notice it was for stays held either way.
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from typing import Any

logger = logging.getLogger(__name__)

TIMEOUT_S = 5.0
LIMIT = 200
# Windows: run without opening a console window (OPS-3). subprocess.CREATE_NO_WINDOW,
# spelled out so the constant exists on every platform.
CREATE_NO_WINDOW = 0x08000000
TITLE_ENV, BODY_ENV = "TRILLION_NOTIFY_TITLE", "TRILLION_NOTIFY_BODY"
# PowerShell's own app id: Windows shows a toast only for an app it knows.
_WINDOWS_APP = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"
# Fixed text: the title and body reach it through the environment, as text nodes.
_TOAST = "\n".join(
    (
        "$ErrorActionPreference = 'Stop'",
        "$null = [Windows.UI.Notifications.ToastNotificationManager,"
        " Windows.UI.Notifications, ContentType = WindowsRuntime]",
        "$kind = [Windows.UI.Notifications.ToastTemplateType]::ToastText02",
        "$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($kind)",
        "$lines = $xml.GetElementsByTagName('text')",
        f"$null = $lines.Item(0).AppendChild($xml.CreateTextNode($env:{TITLE_ENV}))",
        f"$null = $lines.Item(1).AppendChild($xml.CreateTextNode($env:{BODY_ENV}))",
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)",
        "[Windows.UI.Notifications.ToastNotificationManager]::"
        f"CreateToastNotifier('{_WINDOWS_APP}').Show($toast)",
    )
)
# osascript hands what follows the script to its run handler: no quoting at all.
_APPLESCRIPT = (
    "on run argv",
    "display notification (item 2 of argv) with title (item 1 of argv)",
    "end run",
)

Run = Callable[..., Any]
Which = Callable[[str], str | None]


def cut(text: str) -> str:
    """One line, at most LIMIT characters."""
    line = " ".join(text.split())
    return line if len(line) <= LIMIT else line[: LIMIT - 1] + "…"


def command(
    title: str, body: str, *, platform: str, which: Which
) -> tuple[list[str], dict[str, str]] | None:
    """The argument list, and environment values, that show one notification here.
    None where there is nothing to show it with."""
    t, b = cut(title), cut(body)
    if platform == "darwin":
        exe = which("osascript")
        if exe is None:
            return None
        script = [arg for line in _APPLESCRIPT for arg in ("-e", line)]
        return [exe, *script, t, b], {}
    if platform == "win32":
        exe = which("powershell.exe") or which("powershell")
        if exe is None:
            return None
        encoded = base64.b64encode(_TOAST.encode("utf-16-le")).decode("ascii")
        args = [exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
        return args, {TITLE_ENV: t, BODY_ENV: b}
    exe = which("notify-send")
    if exe is None:
        return None
    return [exe, "--app-name=Trillion", "--", t, b], {}


def notify(
    title: str,
    body: str,
    *,
    platform: str = sys.platform,
    run: Run = subprocess.run,
    which: Which = shutil.which,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Show one notification. True only when the platform's tool said it did."""
    found = command(title, body, platform=platform, which=which)
    if found is None:
        logger.info("no way to show a system notification on %s", platform)
        return False
    args, extra = found
    options: dict[str, Any] = {
        "check": False,
        "capture_output": True,
        "stdin": subprocess.DEVNULL,
        "timeout": TIMEOUT_S,
        "env": {**(os.environ if env is None else env), **extra},
    }
    if platform == "win32":
        options["creationflags"] = CREATE_NO_WINDOW
    try:
        done = run(args, **options)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("system notification failed: %s", type(e).__name__)
        return False
    if done.returncode != 0:
        logger.warning("system notification failed: exit status %s", done.returncode)
        return False
    return True
