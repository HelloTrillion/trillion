"""SUR-9 by construction: the scheduler has no route to a surface, and only the door
can reach the system's notifications."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "trillion"
NOTIFIER = "trillion.delivery.notify"
SURFACES = ("trillion.api", "trillion.delivery", "trillion.channels", "subprocess")


def imports(py: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
    return out


def test_the_scheduler_has_no_route_to_a_surface() -> None:
    found = sorted((SRC / "scheduler").rglob("*.py"))
    assert len(found) >= 3  # the package is where this test looks
    for py in found:
        reached = sorted(m for m in imports(py) if m.startswith(SURFACES))
        assert reached == [], f"{py.relative_to(SRC)} imports {reached}"


def test_only_the_door_imports_the_notifier() -> None:
    users = sorted(
        py.relative_to(SRC).as_posix()
        for py in SRC.rglob("*.py")
        if any(m == NOTIFIER or m.startswith(NOTIFIER + ".") for m in imports(py))
    )
    assert users == ["delivery/door.py"]
