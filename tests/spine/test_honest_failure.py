"""SPN-19: the reply reports exactly what the record says happened."""

from __future__ import annotations

import dataclasses

import pytest

from tests.spine.conftest import Spine
from trillion.spine.contract import ToolResult


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (ToolResult(ok=True, content="", effect="none"), "failed"),
        (
            ToolResult(ok=False, content="quota exceeded", effect="none", error_kind="quota"),
            "failed",
        ),
        (ToolResult(ok=True, content="request received", effect="unknown"), "unknown"),
        (ToolResult(ok=False, content="lost the connection", effect="unknown"), "unknown"),
        (ToolResult(ok=True, content="sent", effect="confirmed"), "succeeded"),
    ],
)
async def test_the_status_is_exactly_what_came_back(
    spine: Spine, result: ToolResult, expected: str
) -> None:
    spine.scripted.result = result
    r = await spine.call("scripted", {})
    res = await spine.call("confirm_pending_action", {"pending_id": r.pending_id}, turn=2)
    assert res.status == expected
    if result.content:
        assert result.content in res.content
    body = res.to_model()
    if expected != "succeeded":
        assert '"succeeded"' not in body


async def test_a_timeout_is_unknown_not_failed(spine: Spine) -> None:
    spine.scripted.definition = dataclasses.replace(spine.scripted.definition, timeout_s=0.05)
    spine.scripted.hang = True
    r = await spine.call("scripted", {})
    res = await spine.call("confirm_pending_action", {"pending_id": r.pending_id}, turn=2)
    assert res.status == "unknown"
    a = await spine.store.get_pending(r.pending_id or "")
    assert a is not None and a.effect_status == "unknown"


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (ToolResult(ok=True, content="", effect="none"), "failed"),
        (ToolResult(ok=True, content="queued", effect="unknown"), "unknown"),
        (ToolResult(ok=True, content="saved", effect="confirmed"), "succeeded"),
    ],
)
async def test_an_ungated_write_needs_evidence_too(
    spine: Spine, result: ToolResult, expected: str
) -> None:
    """Only a read may call `ok` a success on its own; a write must show its effect."""
    spine.writer.result = result
    r = await spine.call("scripted_write", {})
    assert r.status == expected
    (rec,) = await spine.store.list_audit()
    assert rec.outcome == expected
