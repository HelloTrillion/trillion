"""What the code itself must never do, checked by reading it (the her browser spec)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from trillion.browser import tools
from trillion.browser.tools import NAMES, TOOLS
from trillion.spine.confirm import META_TOOLS
from trillion.spine.contract import Risk
from trillion.spine.registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "trillion"
BROWSER = SRC / "browser"


def _sources() -> list[tuple[Path, str]]:
    return [(p, p.read_text(encoding="utf-8")) for p in SRC.rglob("*.py")]


def test_the_fixture_switch_is_never_made_or_turned_on_in_src():
    """Private addresses are reached only by a test's fixture: nothing in src makes a
    `FixtureForTests` or passes one."""
    for path, text in _sources():
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = (
                    f.id
                    if isinstance(f, ast.Name)
                    else f.attr
                    if isinstance(f, ast.Attribute)
                    else ""
                )
                assert name != "FixtureForTests", f"{path} makes a FixtureForTests"
                for kw in node.keywords:
                    if kw.arg == "fixture_for_tests":
                        # Only handing on the parameter it was given (None in the app).
                        assert isinstance(kw.value, ast.Attribute | ast.Name), path
                        assert path.parent == BROWSER, f"{path} passes fixture_for_tests"


def test_the_app_never_passes_the_fixture_switch_to_her_browser():
    for path, text in _sources():
        if path.parent == BROWSER:
            continue
        assert "fixture_for_tests" not in text, path
        assert "FixtureForTests" not in text, path


def test_never_the_users_browser():
    """No `channel` (an installed Chrome or Edge), no other executable, no sandbox off, no
    certificate errors ignored, no persistent profile."""
    for path in BROWSER.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\bchannel\s*=", text), path
        assert "--no-sandbox" not in text, path
        assert "ignore_https_errors=True" not in text, path
        assert "launch_persistent_context" not in text, path
        assert "connect_over_cdp" not in text, path
        for word in (".app/Contents", "Program Files", "chrome.exe", "msedge", "/usr/bin/"):
            assert word not in text, (path, word)
    runtime = (BROWSER / "runtime.py").read_text(encoding="utf-8")
    # The one launch, and it names the executable found in Trillion's own folder.
    assert runtime.count(".launch(") == 1
    assert "executable_path=str(exe)" in runtime
    assert "exe = (await asyncio.to_thread(self.install.verify)).executable" in runtime


def test_a_browsing_task_has_only_the_browser_tools():
    class Stub:
        id = "0" * 16

    full = tools.task_registry(Stub())  # type: ignore[arg-type]
    # The spine adds its own meta tools to every registry; the model is offered only
    # the browser tools (`offered`), and the meta tools can't approve a press anyway.
    assert {d.name for d in full.definitions()} == set(NAMES) | set(META_TOOLS)
    registry = tools.offered(full)
    assert {d.name for d in registry.definitions()} == set(NAMES)
    assert all(d.category == "browser" for d in registry.definitions())
    assert not any(d.trusted_output for d in registry.definitions())
    risks = {d.name: d.risk for d in registry.definitions()}
    assert risks["browser_press"] is Risk.IRREVERSIBLE
    assert not registry.definition("browser_press").recoverable  # type: ignore[union-attr]
    assert {n for n, r in risks.items() if r is Risk.READ} == {
        "browser_open",
        "browser_search",
        "browser_read",
        "browser_find",
        "browser_follow",
        "browser_back",
    }
    # And `task_registry` is that list, nothing added.
    source = (BROWSER / "tools.py").read_text(encoding="utf-8")
    body = source[source.index("def task_registry") :]
    assert "for cls in TOOLS:" in body and body.count("register(") == 1
    assert len(TOOLS) == len(NAMES)


def test_her_own_turn_never_has_the_browser_tools():
    found = {d.name for d in ToolRegistry.discover().definitions()}
    assert not found & set(NAMES)


def test_the_browser_tests_run_in_ci():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "tests/browser" in ci
