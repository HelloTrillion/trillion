"""The style guide's samples: every one is a widget the kit takes, every type and block
is shown, and apps/web/src/kit/samples.json is what the generator writes."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from trillion.workspace.blocks import BLOCK_TYPES, validate_blocks
from trillion.workspace.samples import BLOCK_SAMPLES, SAMPLES
from trillion.workspace.widgets import TYPES, validate_widget

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gen_kit_samples.py"


@pytest.fixture(scope="module")
def gen() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_kit_samples", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_samples_file_is_up_to_date(gen: ModuleType) -> None:
    assert gen.main(["--check"]) == 0, "run: uv run python scripts/gen_kit_samples.py"


@pytest.mark.parametrize("raw", SAMPLES + BLOCK_SAMPLES, ids=lambda r: str(r.get("title")))
def test_every_sample_validates(raw: dict) -> None:
    validate_widget(raw, 1)


def test_every_type_has_a_sample() -> None:
    shown = {raw["type"] for raw in SAMPLES}
    assert set(TYPES) <= shown
    assert sum(raw["type"] == "custom" for raw in SAMPLES) >= 2


def test_every_block_has_a_card_titled_with_its_name() -> None:
    titled = {raw["title"]: raw["blocks"][0]["type"] for raw in BLOCK_SAMPLES}
    assert set(BLOCK_TYPES) <= set(titled)
    assert all(name == kind for name, kind in titled.items())


def test_every_expanded_sample_has_blocks_the_kit_takes_unchanged(gen: ModuleType) -> None:
    doc = json.loads(gen.render())
    for widget in doc["widgets"] + doc["blocks"]:
        assert widget["blocks"], widget["id"]
        assert validate_blocks(widget["blocks"], widget["id"]) == widget["blocks"]
        if "source" in widget:
            assert widget["source"]["at"] == "2026-10-25T09:00:00Z"
    ids = [w["id"] for w in doc["widgets"] + doc["blocks"]]
    assert ids == [f"k{n}" for n in range(1, len(ids) + 1)]
