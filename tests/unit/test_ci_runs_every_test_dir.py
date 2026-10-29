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
