"""OPS-3: scheduled and background work never flashes a console window on Windows.

Trillion schedules nothing with the operating system: routines, reminders and
watchers run inside the one `trillion run` process (the scheduler and attention
loops), so there is no scheduled launch command to generate. What it does start
while working in the background is checked here: every process it starts is
windowless, and no console interpreter is ever named."""

from __future__ import annotations

import ast
import functools
import inspect
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "trillion"
DESKTOP = ROOT / "apps" / "desktop" / "src-tauri"

# Ways to ask the operating system to run something later or at login.
OS_SCHEDULERS = re.compile(
    r"schtasks|ScheduledTask|crontab|launchctl|systemd-run|systemctl|StartCalendarInterval"
    r"|CurrentVersion\\Run",
    re.IGNORECASE,
)
# Starting a process. Each call must say how it hides its window.
_STARTS = {
    ("subprocess", "run"),
    ("subprocess", "Popen"),
    ("subprocess", "call"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
    ("asyncio", "create_subprocess_exec"),
    ("asyncio", "create_subprocess_shell"),
    ("os", "system"),
    ("os", "startfile"),
    ("os", "popen"),
}
_STARTS_PREFIXED = ("spawn", "exec")  # os.spawnv, os.execv and the rest
# The few that may start a process without hiding a window, and why.
ALLOWED = {
    # `trillion doctor` is a command the user types in a terminal: there is already
    # a console, and it is theirs. Never run by a routine or in the background.
    "cli/doctor.py",
}


def _sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


@functools.cache
def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _starts(tree: ast.AST) -> list[ast.Call]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if not isinstance(owner, ast.Name):
            continue
        name = (owner.id, node.func.attr)
        if name in _STARTS or (owner.id == "os" and node.func.attr.startswith(_STARTS_PREFIXED)):
            found.append(node)
    return found


def test_no_os_scheduler_is_used() -> None:
    """Scheduled work runs in-process, so there is no OS task whose launch could flash."""
    places = [
        *_sources(),
        *sorted((DESKTOP / "src").glob("*.rs")),
        ROOT / "install.ps1",
        ROOT / "install.sh",
    ]
    hits = [
        f"{p.relative_to(ROOT)}: {m.group(0)}"
        for p in places
        for m in [OS_SCHEDULERS.search(p.read_text(encoding="utf-8"))]
        if m
    ]
    assert hits == [], "an OS scheduler must launch through a windowless launcher (OPS-3)"


def test_every_process_the_core_starts_hides_its_window() -> None:
    """A process started from the core says `creationflags` (CREATE_NO_WINDOW on Windows)."""
    bare = []
    for path in _sources():
        rel = path.relative_to(SRC).as_posix()
        if rel in ALLOWED:
            continue
        for call in _starts(_tree(path)):
            if not any(k.arg == "creationflags" for k in call.keywords):
                bare.append(f"{rel}:{call.lineno}")
    assert bare == [], "start it with creationflags=CREATE_NO_WINDOW on Windows (OPS-3)"


def test_the_allowed_list_is_still_needed() -> None:
    """An entry that no longer starts anything is dropped, so the list stays honest."""
    for rel in ALLOWED:
        assert _starts(_tree(SRC / rel)), rel


def test_no_console_interpreter_is_named() -> None:
    """Never python.exe or node.exe: each opens a console window when started."""
    named = []
    for path in _sources():
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if re.search(r"\b(python|node)\.exe\b", node.value, re.IGNORECASE):
                    named.append(f"{path.relative_to(SRC).as_posix()}:{node.lineno}")
    assert named == [], "use pythonw.exe or a windowless shim (OPS-3)"


def test_mcp_servers_start_windowless_on_windows() -> None:
    """A connected MCP server (often `node` or `python`) is started by the MCP SDK,
    which hides its window on Windows. If an upgrade drops that, this fails."""
    from mcp.client import stdio
    from mcp.os.win32 import utilities

    assert "create_windows_process" in inspect.getsource(stdio)
    assert "CREATE_NO_WINDOW" in inspect.getsource(utilities.create_windows_process)


def test_the_desktop_app_starts_its_core_windowless_wherever_it_ships() -> None:
    """The desktop app ships for macOS only today. Once it bundles for Windows, its
    core must start with CREATE_NO_WINDOW like everything else."""
    conf = json.loads((DESKTOP / "tauri.conf.json").read_text(encoding="utf-8"))
    targets = conf.get("bundle", {}).get("targets", [])
    targets = [targets] if isinstance(targets, str) else targets
    windows = {"all", "msi", "nsis"} & set(targets)
    server = (DESKTOP / "src" / "server.rs").read_text(encoding="utf-8")
    if windows:
        assert "CREATE_NO_WINDOW" in server
    else:
        pytest.skip("the desktop app does not ship for Windows")


def test_the_check_sees_a_bare_start() -> None:
    """The scan itself: a start without creationflags is found, one with it is not."""
    code = (
        "import os, subprocess, asyncio\n"
        "subprocess.Popen(['x'])\n"
        "subprocess.run(['x'], creationflags=0)\n"
        "asyncio.create_subprocess_exec('x')\n"
        "os.spawnv(0, 'x', [])\n"
    )
    calls = _starts(ast.parse(code))
    assert [c.lineno for c in calls] == [2, 3, 4, 5]
    assert [c.lineno for c in calls if not any(k.arg == "creationflags" for k in c.keywords)] == [
        2,
        4,
        5,
    ]
    assert OS_SCHEDULERS.search("schtasks /create /tn x")
    assert OS_SCHEDULERS.search(r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Run")
