"""An approved action's result reaches her whole, as an ordinary read's does.

The record keeps only the start of a result (its outcome detail). Handing her that
copy cut a list of a hundred records to the first one, mid-record, and she took it
for all there was."""

from __future__ import annotations

from tests.spine.conftest import Spine
from trillion.spine.contract import ToolResult
from trillion.spine.router import _DETAIL_LIMIT

LONG = "".join(f"record {i:04d};" for i in range(600))  # about 7,000 characters


async def approve(spine: Spine, via: str) -> str:
    spine.scripted.result = ToolResult(ok=True, content=LONG, effect="confirmed")
    proposed = await spine.call("scripted", {})
    assert proposed.status == "confirmation_required"
    pending = proposed.pending_id or ""
    if via == "tap":
        done = await spine.router.resolve(pending, "confirm", spine.ctx(turn=2, via="tap"))
    else:
        done = await spine.call("confirm_pending_action", {"pending_id": pending}, turn=2)
    assert done.status == "succeeded"
    return done.content


async def test_a_tapped_approval_hands_back_the_whole_result(spine: Spine) -> None:
    assert len(LONG) > _DETAIL_LIMIT
    content = await approve(spine, "tap")
    assert "record 0599;" in content


async def test_a_spoken_yes_hands_back_the_whole_result(spine: Spine) -> None:
    content = await approve(spine, "text")
    assert "record 0599;" in content


async def test_the_record_still_keeps_only_its_start(spine: Spine) -> None:
    spine.scripted.result = ToolResult(ok=True, content=LONG, effect="confirmed")
    proposed = await spine.call("scripted", {})
    pending = proposed.pending_id or ""
    await spine.router.resolve(pending, "confirm", spine.ctx(turn=2, via="tap"))
    record = await spine.store.get_pending(pending)
    assert record is not None and len(record.outcome_detail or "") == _DETAIL_LIMIT
    # A second yes answers from the record and runs nothing again.
    again = await spine.router.resolve(pending, "confirm", spine.ctx(turn=3, via="tap"))
    assert spine.scripted.executions == 1 and "record 0599;" not in again.content
