from __future__ import annotations

import json
from pathlib import Path

from trillion import __version__
from trillion.spine.contract import ToolContext
from trillion.spine.registry import ToolRegistry
from trillion.tools.describe import DescribeSelf


def ctx(root: Path) -> ToolContext:
    return ToolContext(
        owner_id="local", session_id="s", turn=1, call_id="c", via="text", granted_roots=(root,)
    )


async def test_the_report_is_generated_from_the_live_registry(tmp_path: Path) -> None:
    registry = ToolRegistry.discover()
    tool = registry.get("describe_self")
    assert isinstance(tool, DescribeSelf)
    tool.bind(registry.definitions, lambda: {"model": {"provider": "p", "model": "m"}})
    # Reached directly here only to read the report; in the app it runs through the router.
    result = await DescribeSelf.execute(tool, {}, ctx(tmp_path))
    report = json.loads(result.content)

    assert report["version"] == __version__
    assert report["model"] == {"provider": "p", "model": "m"}
    assert report["shared_folders"] == [str(tmp_path)]
    listed = {t["name"]: t for t in report["tools"]}
    # Every registered tool, and nothing else: nothing invented, nothing forgotten.
    assert set(listed) == {d.name for d in registry.definitions()}
    assert listed["file_read"]["confirmation"] == "runs at once"
    assert listed["file_create"]["confirmation"].startswith(
        "calling it shows an approval card; a yes or a tap"
    )
    assert "only a tap" in listed["file_overwrite"]["confirmation"]
    assert "cannot do" in report["note"]


async def test_unbound_it_claims_nothing(tmp_path: Path) -> None:
    result = await DescribeSelf.execute(DescribeSelf(), {}, ctx(tmp_path))
    assert json.loads(result.content)["tools"] == []


def test_it_is_a_read_whose_output_is_treated_as_data() -> None:
    d = DescribeSelf.definition
    # Tool descriptions can come from plugins, so the report is wrapped like any result.
    assert d.risk.value == "read" and not d.trusted_output
