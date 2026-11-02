"""She can ask again for an action that expired or a check proved never went out, by the
`represent_pending_action` meta tool. It only shows a new card: nothing runs (OPS-1)."""

from __future__ import annotations

from tests.spine.conftest import Spine
from trillion.spine.confirm import META_TOOLS, REPRESENT_TOOL, Status
from trillion.spine.contract import ToolResult
from trillion.spine.router import EXPIRY_MS, SETTLE_NONE_AFTER_MS
from trillion.spine.store import DecisionIn
from trillion.tools.record import DecisionRecord

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


async def never_sent(spine: Spine) -> str:
    """A send a check after a crash proved never went out."""
    spine.scripted.reconcile_result = ToolResult(ok=True, content="not in Sent", effect="none")
    spine.scripted.result = ToolResult(ok=True, content="sent", effect="confirmed")
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
    return pid


async def approve(spine: Spine, pid: str, turn: int, session_id: str = "s1") -> str:
    await spine.store.mark_delivered(pid, "local", "web", spine.clock())
    r = await spine.router.resolve(
        pid, "confirm", spine.ctx(turn=turn, via="tap", session_id=session_id)
    )
    return r.status


async def test_a_send_proved_never_sent_is_asked_for_again_from_a_new_session(
    spine: Spine,
) -> None:
    pid = await never_sent(spine)
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


async def test_its_own_session_cannot_ask_again_once_a_copy_was_sent(spine: Spine) -> None:
    # A resumed conversation keeps its session id: the same rule holds there.
    pid = await never_sent(spine)
    a = await spine.call(REPRESENT, {"pending_id": pid}, turn=5)
    assert a.pending_id is not None
    assert await approve(spine, a.pending_id, turn=6) == "succeeded"
    b = await spine.call(REPRESENT, {"pending_id": pid}, turn=7)
    assert b.status == "refused" and b.reason == "not_representable"
    assert a.pending_id in b.detail
    assert spine.scripted.executions == 1


async def test_while_a_copy_waits_no_second_copy_is_made_in_any_session(spine: Spine) -> None:
    pid = await never_sent(spine)
    a = await spine.call(REPRESENT, {"pending_id": pid}, session_id="s2", turn=1)
    assert a.status == "confirmation_required"
    for session_id, turn in (("s1", 9), ("s2", 2)):
        b = await spine.call(REPRESENT, {"pending_id": pid}, session_id=session_id, turn=turn)
        assert b.status == "refused" and b.reason == "not_representable", session_id
    copies = await spine.store.list_represented_from(pid)
    assert [c.id for c in copies] == [a.pending_id]


async def test_an_expired_card_asked_for_twice_leaves_one_card(spine: Spine) -> None:
    pid = await propose(spine, "late.txt")
    spine.clock.advance(EXPIRY_MS + 1)
    a = await spine.call(REPRESENT, {"pending_id": pid}, turn=2)
    assert a.status == "confirmation_required"
    b = await spine.call(REPRESENT, {"pending_id": pid}, turn=3)
    assert b.status == "refused" and b.reason == "not_representable"
    copies = await spine.store.list_represented_from(pid)
    assert [c.status for c in copies] == [Status.PROPOSED]


async def test_a_copy_that_expired_unanswered_lets_it_be_asked_again(spine: Spine) -> None:
    pid = await propose(spine, "late.txt")
    spine.clock.advance(EXPIRY_MS + 1)
    a = await spine.call(REPRESENT, {"pending_id": pid}, turn=2)
    spine.clock.advance(EXPIRY_MS + 1)
    b = await spine.call(REPRESENT, {"pending_id": pid}, turn=3)
    assert b.status == "confirmation_required" and b.pending_id != a.pending_id


async def test_the_record_names_the_copy_and_how_it_ended(spine: Spine) -> None:
    record = DecisionRecord()
    record.bind(spine.store, "local", spine.clock)
    spine.registry.register(record)
    pid = await never_sent(spine)
    a = await spine.call(REPRESENT, {"pending_id": pid}, turn=5)
    assert await approve(spine, a.pending_id or "", turn=6) == "succeeded"
    r = await spine.call("decision_record", {}, turn=9)
    said = r.content
    original = said[said.index(f"{pid}: ") :]
    assert f"asked for again as {a.pending_id}" in original
    assert "can be asked for again" not in original
