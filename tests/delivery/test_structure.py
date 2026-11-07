"""SUR-9 by construction: the scheduler has no route to a surface, and only the door
can reach the system's notifications."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "trillion"
NOTIFIER = "trillion.delivery.notify"
_EXEC = ("execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe")
_SPAWN = ("spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp")
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
    "os.startfile",
    "os.posix_spawn",
    "os.posix_spawnp",
    *(f"os.{name}" for name in (*_EXEC, *_SPAWN, "spawnvpe")),
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
    uses, read through what it was imported as: after `from trillion import delivery`,
    `delivery.notify` counts as reaching `trillion.delivery.notify`."""
    out: set[str] = set()
    here = module.split(".") if package else module.split(".")[:-1]
    bound: dict[str, str] = {}
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name)
                if a.asname:
                    bound[a.asname] = a.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parent = ".".join(here[: len(here) - (node.level - 1)])
                mod = f"{parent}.{node.module}" if node.module else parent
            else:
                mod = node.module or ""
            out.add(mod)
            for a in node.names:
                out.add(f"{mod}.{a.name}")
                bound[a.asname or a.name] = f"{mod}.{a.name}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            dotted = _dotted(node)
            if dotted is not None:
                head, _, rest = dotted.partition(".")
                out.add(dotted)
                if head in bound:
                    out.add(f"{bound[head]}.{rest}")
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


def _under(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def reached(names: set[str]) -> list[str]:
    bad = {m for m in names if any(_under(m, p) for p in SURFACES)}
    bad |= {m for m in names if any(part in DYNAMIC for part in m.split("."))}
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
        if any(_under(m, NOTIFIER) for m in file_imports(py))
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
        "import os\nos.execv('x', [])": "os.execv",
        "from trillion import delivery\ndelivery.notify.notify('a', 'b')": (
            "trillion.delivery.notify.notify"
        ),
        "import trillion.api as surface\nsurface.session": "trillion.api.session",
    }
    for source, expected in samples.items():
        assert expected in reached(imports(source, mod)), source
    # The package's own relative imports resolve inside it, and are allowed.
    assert reached(imports("from .store import Notice\nfrom . import when", mod)) == []
    # A name that only starts like a surface is not one.
    assert reached({"subprocessing", "trillion.apiary", "os.systems"}) == []
    pkg = imports("from .delivery import notify", "trillion", package=True)
    assert "trillion.delivery.notify" in pkg


def test_security_alerts_have_no_route_to_a_surface() -> None:
    """SEC-11 through SUR-9: security alerts write notices and tell the door, nothing else."""
    found = sorted((SRC / "security").rglob("*.py"))
    assert len(found) >= 3
    for py in found:
        bad = reached(file_imports(py))
        assert bad == [], f"{py.relative_to(SRC)} reaches {bad}"
