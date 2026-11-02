"""TESTING.md §1.2: execution outcomes (SPN-17, SPN-17a, SPN-17b)."""

from __future__ import annotations

import asyncio

from tests.spine.conftest import Spine
from trillion.spine.confirm import Status
from trillion.spine.contract import ToolResult
from trillion.spine.store import DecisionIn


async def test_1_2_1_crash_mid_execution_is_unknown_and_never_retried(spine: Spine) -> None:
    r = await spine.call("slow_irreversible", {})
    pid = r.pending_id or ""
    # Simulate a crash after the claim: drive the row to `executing` by hand.
    assert await spine.store.transition(
        pid,
        Status.PROPOSED,
        Status.APPROVED,
        now_ms=spine.clock(),
        decision=DecisionIn(event="approved", decided_by="s1", via="tap"),
    )
    assert await spine.store.transition(
        pid,
        Status.APPROVED,
        Status.EXECUTING,
        now_ms=spine.clock(),
        decision=DecisionIn(event="executing", decided_by="s1", via="tap"),
    )
    touched = await spine.router.recover((spine.granted,))
    assert touched == [pid]
    after = await spine.store.get_pending(pid)
    assert after is not None and after.status is Status.UNKNOWN
    assert spine.slow.executions == 0
    # A second startup does not touch it again, and confirming it runs nothing.
    assert await spine.router.recover((spine.granted,)) == []
    again = await spine.router.resolve(pid, "confirm", spine.ctx(turn=2, via="tap"))
    assert again.status == "unknown"
    assert spine.slow.executions == 0


async def test_1_2_2_twenty_simultaneous_confirmations_execute_once(spine: Spine) -> None:
    r = await spine.call("slow_irreversible", {})
    results = await asyncio.gather(
        *(
            spine.call("confirm_pending_action", {"pending_id": r.pending_id}, turn=2)
            for _ in range(20)
        )
    )
    assert spine.slow.executions == 1
    assert [x.status for x in results].count("succeeded") >= 1
    assert all(x.status in {"succeeded", "in_progress"} for x in results)
    again = await spine.call("confirm_pending_action", {"pending_id": r.pending_id}, turn=3)
    assert again.status == "succeeded" and spine.slow.executions == 1
    events = [d.event for d in await spine.store.list_decisions(r.pending_id or "")]
    assert events.count("approved") == 1 and events.count("executing") == 1


async def test_1_2_3_ok_without_effect_is_not_success(spine: Spine) -> None:
    spine.scripted.result = ToolResult(ok=True, content="", effect="none")
    r = await spine.call("scripted", {})
    res = await spine.call("confirm_pending_action", {"pending_id": r.pending_id}, turn=2)
    assert res.status == "failed"
    a = await spine.store.get_pending(r.pending_id or "")
    assert a is not None and a.status is Status.FAILED and a.effect_status == "none"


async def test_recover_invalidates_approved_but_unstarted(spine: Spine) -> None:
    r = await spine.call("slow_irreversible", {})
    pid = r.pending_id or ""
    await spine.store.transition(
        pid,
        Status.PROPOSED,
        Status.APPROVED,
        now_ms=spine.clock(),
        decision=DecisionIn(event="approved", decided_by="s1", via="tap"),
    )
    assert await spine.router.recover((spine.granted,)) == [pid]
    a = await spine.store.get_pending(pid)
    assert a is not None and a.status is Status.INVALIDATED
    assert spine.slow.executions == 0
    again = await spine.router.represent(pid, spine.ctx(turn=3))
    assert again.status == "confirmation_required"


async def _to_executing(spine: Spine, pid: str) -> None:
    for old, new in ((Status.PROPOSED, Status.APPROVED), (Status.APPROVED, Status.EXECUTING)):
        await spine.store.transition(
            pid,
            old,
            new,
            now_ms=spine.clock(),
            decision=DecisionIn(event=new.value, decided_by="s1", via="tap"),
        )


async def test_recover_uses_the_tools_own_evidence(spine: Spine) -> None:
    """reconcile() can prove an interrupted action landed; its answer is recorded, not rerun."""
    spine.scripted.reconcile_result = ToolResult(ok=True, content="it landed", effect="confirmed")
    r = await spine.call("scripted", {})
    pid = r.pending_id or ""
    await _to_executing(spine, pid)
    await spine.router.recover((spine.granted,))
    a = await spine.store.get_pending(pid)
    assert a is not None and a.status is Status.SUCCEEDED and a.outcome_detail == "it landed"
    assert spine.scripted.executions == 0
    events = [d.event for d in await spine.store.list_decisions(pid)]
    assert events[-1] == "recovered:succeeded"


async def test_recover_when_reconcile_raises_is_unknown(spine: Spine) -> None:
    spine.scripted.reconcile_error = RuntimeError("boom")
    r = await spine.call("scripted", {})
    pid = r.pending_id or ""
    await _to_executing(spine, pid)
    await spine.router.recover((spine.granted,))
    a = await spine.store.get_pending(pid)
    assert a is not None and a.status is Status.UNKNOWN and a.effect_status == "unknown"
    assert spine.scripted.executions == 0


async def test_1_2_4_delivered_at_is_written_once(spine: Spine) -> None:
    r = await spine.call("slow_irreversible", {})
    pid = r.pending_id or ""
    assert await spine.store.get_delivery(pid) is None  # fetching is not rendering
    assert await spine.router.mark_delivered(pid, "local", "web")
    first = await spine.store.get_delivery(pid)
    assert first is not None and first.surface == "web"
    spine.clock.advance(5000)
    assert not await spine.router.mark_delivered(pid, "local", "desktop")
    assert await spine.store.get_delivery(pid) == first
    assert not await spine.router.mark_delivered(pid, "someone-else", "web")
    assert not await spine.router.mark_delivered("no-such-id", "local", "web")


async def test_the_tool_is_given_the_action_id_when_it_runs(spine: Spine) -> None:
    spine.scripted.result = ToolResult(ok=True, content="done", effect="confirmed")
    r = await spine.call("scripted", {})
    await spine.call("confirm_pending_action", {"pending_id": r.pending_id}, turn=2)
    assert spine.scripted.seen[-1].action_id == r.pending_id


async def test_the_check_is_given_the_action_id(spine: Spine) -> None:
    spine.scripted.reconcile_result = ToolResult(ok=True, content="landed", effect="confirmed")
    r = await spine.call("scripted", {})
    pid = r.pending_id or ""
    await _to_executing(spine, pid)
    await spine.router.recover((spine.granted,))
    assert spine.scripted.seen[-1].action_id == pid


async def test_a_direct_call_has_no_action_id(spine: Spine) -> None:
    ctx = spine.ctx()
    assert ctx.action_id is None
