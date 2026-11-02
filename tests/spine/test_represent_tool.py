"""She can ask again for an action that expired or a check proved never went out, by the
`represent_pending_action` meta tool. It only shows a new card: nothing runs (OPS-1)."""

from __future__ import annotations

from tests.spine.conftest import Spine
from trillion.spine.confirm import META_TOOLS, REPRESENT_TOOL, Status
from trillion.spine.contract import ToolResult
from trillion.spine.router import EXPIRY_MS, SETTLE_NONE_AFTER_MS
from trillion.spine.store import DecisionIn

REPRESENT = "represent_pending_action"


async def propose(spine: Spine, name: str) -> str:
    r = await spine.call("file_create", {"path": str(spine.granted / name), "content": "hi"})
    assert r.status == "confirmation_required" and r.pending_id is not None
    return r.pending_id


async def test_it_is_a_meta_tool_offered_beside_confirm_and_cancel(spine: Spine) -> None:
    assert META_TOOLS[REPRESENT] is REPRESENT_TOOL
    assert REPRESENT in {d.name for d in spine.registry.definitions()}
    # A run nobody watches is offered no meta tool.
    assert REPRESENT not in {d.name for d in spine.registry.unattended().definitions()}


async def test_an_expired_action_is_asked_for_again_from_its_session(spine: Spine) -> None:
    pid = await propose(spine, "late.txt")
    spine.clock.advance(EXPIRY_MS + 1)
    r = await spine.call(REPRESENT, {"pending_id": pid}, turn=2)
    assert r.status == "confirmation_required"
    assert r.pending_id is not None and r.pending_id != pid
    # A new card, waiting for its own yes: nothing was approved or run.
    new = await spine.store.get_pending(r.pending_id)
    assert new is not None and new.status is Status.PROPOSED and new.represented_from == pid
    assert not (spine.granted / "late.txt").exists()


async def test_a_send_proved_never_sent_is_asked_for_again_from_a_new_session(
    spine: Spine,
) -> None:
    spine.scripted.reconcile_result = ToolResult(ok=True, content="not in Sent", effect="none")
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
    # "Never went" counts only once the action has been quiet for a while.
    spine.clock.advance(SETTLE_NONE_AFTER_MS)
    await spine.router.recover((spine.granted,))
    again = await spine.call(REPRESENT, {"pending_id": pid}, session_id="s2", turn=1)
    assert again.status == "confirmation_required" and again.pending_id not in (None, pid)
    assert spine.scripted.executions == 0


async def test_a_succeeded_action_is_never_asked_for_again(spine: Spine) -> None:
    pid = await propose(spine, "done.txt")
    await spine.store.mark_delivered(pid, "local", "web", spine.clock())
    ok = await spine.router.resolve(pid, "confirm", spine.ctx(turn=2, via="tap"))
    assert ok.status == "succeeded"
    r = await spine.call(REPRESENT, {"pending_id": pid}, turn=3)
    assert r.status == "refused" and r.reason == "not_representable"


async def test_a_run_nobody_watches_cannot_ask_again(spine: Spine) -> None:
    pid = await propose(spine, "late.txt")
    spine.clock.advance(EXPIRY_MS + 1)
    for via in ("routine", "schedule", "refresh"):
        r = await spine.call(REPRESENT, {"pending_id": pid}, turn=2, via=via)
        assert r.status == "refused" and r.reason == "unattended", via


async def test_only_a_pending_id_is_taken(spine: Spine) -> None:
    pid = await propose(spine, "late.txt")
    r = await spine.call(REPRESENT, {"pending_id": pid, "path": "x"}, turn=2)
    assert r.status == "refused" and r.reason == "invalid_args"
