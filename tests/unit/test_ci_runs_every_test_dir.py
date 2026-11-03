"""CI runs every test directory: one left out of the workflow would never run there."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_every_test_directory_is_run_by_a_ci_job() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    ran = set(re.findall(r"\btests/(\w+)", workflow))
    dirs = {p.name for p in (ROOT / "tests").iterdir() if (p / "__init__.py").is_file()}
    assert dirs - ran == set()


def test_the_spine_suite_runs_on_every_os() -> None:
    """macOS runs it inside the Python job (one scarce Mac runner, not two); the
    others in the spine job. Losing either would drop a platform silently."""
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    spine = workflow.split("\n  spine:\n", 1)[1].split("\n  structure:\n", 1)[0]
    python = workflow.split("\n  python:\n", 1)[1].split("\n  spine:\n", 1)[0]
    assert "os: [ubuntu-latest, windows-latest]" in spine
    assert "macos-latest" in python
    assert "if: runner.os == 'macOS'" in python
    assert "--cov=trillion.spine" in python and "--cov-fail-under=95" in python
