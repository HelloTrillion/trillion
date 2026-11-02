"""At startup she finds every send a check left in doubt lately, so a notice lost to a
quit before it was written is still given (OPS-1)."""

from __future__ import annotations

from tests.spine.conftest import Spine
from trillion.spine.confirm import Status
from trillion.spine.contract import ToolResult
from trillion.spine.router import CHECK_FOR_MS, SETTLE_NONE_AFTER_MS, Settled
from trillion.spine.store import DecisionIn

DAY_MS = 86_400_000


async def executing(spine: Spine) -> str:
    r = await spine.call("scripted", {})
    pid = r.pending_id or ""
    for old, new in ((Status.PROPOSED, Status.APPROVED), (Status.APPROVED, Status.EXECUTING)):
        await spine.store.transition(
            pid,
            old,
            new,
            now_ms=spine.clock(),
            decision=DecisionIn(event=new.value, decided_by="s1", via="tap"),
        )
    return pid


async def test_never_sent_and_unknown_are_found_and_nothing_else(spine: Spine) -> None:
    spine.scripted.reconcile_result = ToolResult(ok=True, content="not in Sent", effect="none")
    gone = await executing(spine)
    spine.clock.advance(SETTLE_NONE_AFTER_MS)
    await spine.router.recover((spine.granted,))
    spine.scripted.reconcile_result = ToolResult(ok=True, content="in Sent", effect="confirmed")
    await executing(spine)
    await spine.router.recover((spine.granted,))
    spine.scripted.reconcile_result = None  # no check at all: unknown
    lost = await executing(spine)
    await spine.router.recover((spine.granted,))
    since = spine.clock() - 7 * DAY_MS
    found = await spine.router.in_doubt(since)
    assert sorted(found, key=lambda s: s.id) == sorted(
        [Settled(id=gone, outcome="not_sent"), Settled(id=lost, outcome="unknown")],
        key=lambda s: s.id,
    )


async def test_an_old_one_or_one_asked_for_again_is_not_found(spine: Spine) -> None:
    spine.scripted.reconcile_result = ToolResult(ok=True, content="not in Sent", effect="none")
    spine.scripted.result = ToolResult(ok=True, content="sent", effect="confirmed")
    pid = await executing(spine)
    spine.clock.advance(SETTLE_NONE_AFTER_MS)
    await spine.router.recover((spine.granted,))
    await spine.call("represent_pending_action", {"pending_id": pid}, turn=5)
    assert await spine.router.in_doubt(spine.clock() - 7 * DAY_MS) == []
    old = await executing(spine)
    spine.scripted.reconcile_result = None
    await spine.router.recover((spine.granted,))
    spine.clock.advance(7 * DAY_MS + CHECK_FOR_MS)
    assert old not in {s.id for s in await spine.router.in_doubt(spine.clock() - 7 * DAY_MS)}
