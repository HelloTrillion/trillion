#!/usr/bin/env python3
"""Write the style guide's samples: every sample in `trillion.workspace.samples`,
checked by the kit and expanded into blocks exactly as the page receives a board.

    uv run python scripts/gen_kit_samples.py [--check]

`--check` writes nothing and exits 1 when apps/web/src/kit/samples.json is out of date.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from trillion.workspace.recipes import expand_board
from trillion.workspace.samples import BLOCK_SAMPLES, SAMPLES
from trillion.workspace.widgets import validate_widget

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "apps" / "web" / "src" / "kit" / "samples.json"
# The server stamps when a source was read; the samples use one fixed moment.
AT = "2026-10-25T09:00:00Z"


def _expanded(samples: list[dict[str, Any]], first: int) -> list[dict[str, Any]]:
    widgets = []
    for n, raw in enumerate(samples, first):
        widget = validate_widget(raw, n - first + 1)
        if "source" in widget:
            widget["source"]["at"] = AT
        widgets.append({"id": f"k{n}", **widget})
    return list(expand_board({"widgets": widgets})["widgets"])


def render() -> str:
    widgets = _expanded(SAMPLES, 1)
    blocks = _expanded(BLOCK_SAMPLES, len(widgets) + 1)
    doc = {"widgets": widgets, "blocks": blocks}
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else None
        if current != text:
            print(f"{OUT.relative_to(ROOT)} is out of date; run scripts/gen_kit_samples.py")
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
