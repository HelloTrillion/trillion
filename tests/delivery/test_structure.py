"""SUR-9 by construction: the scheduler has no route to a surface, and only the door
can reach the system's notifications."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "trillion"
NOTIFIER = "trillion.delivery.notify"
SURFACES = (
    "trillion.api",
    "trillion.delivery",
    "trillion.channels",
    "subprocess",
    "asyncio.subprocess",
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
    "os.system",
    "os.popen",
    "os.exec",
    "os.spawn",
    "os.startfile",
    "webbrowser",
)
# Importing by name at run time would get round every check here.
DYNAMIC = ("importlib", "__import__", "import_module")


def module_of(py: Path, src: Path = SRC) -> str:
    rel = py.relative_to(src.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imports(source: str, module: str, *, package: bool = False) -> set[str]:
    """Every module (and module.name) the source imports, and every dotted name it
    uses, so `os.system(...)` counts as reaching `os.system`."""
    out: set[str] = set()
    here = module.split(".") if package else module.split(".")[:-1]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = here[: len(here) - (node.level - 1)] if node.level > 1 else here
                parent = ".".join(base)
                mod = f"{parent}.{node.module}" if node.module else parent
            else:
                mod = node.module or ""
            out.add(mod)
            out.update(f"{mod}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Attribute):
            dotted = _dotted(node)
            if dotted is not None:
                out.add(dotted)
        elif isinstance(node, ast.Name) and node.id in DYNAMIC:
            out.add(node.id)
    return out


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr}" if inner is not None else None
    return None


def reached(names: set[str]) -> list[str]:
    bad = {m for m in names if m.startswith(SURFACES)}
    bad |= {m for m in names if m.split(".")[0] in DYNAMIC or m.split(".")[-1] in DYNAMIC}
    return sorted(bad)


def file_imports(py: Path) -> set[str]:
    return imports(py.read_text(encoding="utf-8"), module_of(py), package=py.name == "__init__.py")


def test_the_scheduler_has_no_route_to_a_surface() -> None:
    found = sorted((SRC / "scheduler").rglob("*.py"))
    assert len(found) >= 3  # the package is where this test looks
    for py in found:
        bad = reached(file_imports(py))
        assert bad == [], f"{py.relative_to(SRC)} reaches {bad}"


def test_only_the_door_imports_the_notifier() -> None:
    users = sorted(
        py.relative_to(SRC).as_posix()
        for py in SRC.rglob("*.py")
        if any(m == NOTIFIER or m.startswith(NOTIFIER + ".") for m in file_imports(py))
    )
    assert users == ["delivery/door.py"]


def test_the_check_sees_round_the_back_ways() -> None:
    mod = "trillion.scheduler.runner"
    samples = {
        "from ..delivery import notify": "trillion.delivery.notify",
        "from ..api.session import Session": "trillion.api.session",
        "from . import store\nfrom .. import delivery": "trillion.delivery",
        "import importlib\nimportlib.import_module('trillion.api')": "importlib",
        "from importlib import import_module": "importlib.import_module",
        "__import__('subprocess')": "__import__",
        "import os\nos.system('x')": "os.system",
        "import asyncio\nasyncio.create_subprocess_exec('x')": "asyncio.create_subprocess_exec",
        "from asyncio import subprocess": "asyncio.subprocess",
        "import webbrowser": "webbrowser",
    }
    for source, expected in samples.items():
        assert expected in reached(imports(source, mod)), source
    # The package's own relative imports resolve inside it, and are allowed.
    assert reached(imports("from .store import Notice\nfrom . import when", mod)) == []
    pkg = imports("from .delivery import notify", "trillion", package=True)
    assert "trillion.delivery.notify" in pkg
