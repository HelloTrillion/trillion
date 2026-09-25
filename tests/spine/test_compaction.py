"""SPN-3 and TESTING.md §1.2.5: the gate and the record live outside the conversation."""

from __future__ import annotations

from tests.spine.conftest import Spine
from trillion.spine.confirm import Refusal, Status


async def test_the_gate_survives_losing_the_whole_conversation(spine: Spine) -> None:
    """The router holds no conversation. Discarding every message changes nothing."""
    conversation = [{"role": "user", "content": "write a note"}]
    r = await spine.call("file_create", {"path": str(spine.granted / "n.txt"), "content": "x"})
    conversation.append({"role": "assistant", "content": r.to_model()})
    conversation.clear()  # compaction dropped everything, including the instruction to confirm

    same_turn = await spine.call("confirm_pending_action", {"pending_id": r.pending_id}, turn=1)
    assert same_turn.reason is Refusal.SAME_TURN
    assert not (spine.granted / "n.txt").exists()
    ok = await spine.call("confirm_pending_action", {"pending_id": r.pending_id}, turn=2)
    assert ok.status == "succeeded"


async def test_1_2_5_the_decision_record_answers_without_history(spine: Spine) -> None:
    r = await spine.call("file_create", {"path": str(spine.granted / "n.txt"), "content": "x"})
    await spine.call("confirm_pending_action", {"pending_id": r.pending_id}, turn=2)
    # The conversation is gone; "did that ever reach me?" is answered from storage alone.
    a = await spine.store.get_pending(r.pending_id or "")
    assert a is not None and a.status is Status.SUCCEEDED and a.effect_status == "confirmed"
    events = [(d.event, d.decided_by) for d in await spine.store.list_decisions(a.id)]
    assert events == [
        ("proposed", "s1"),
        ("approved", "s1"),
        ("executing", "s1"),
        ("succeeded", "s1"),
    ]
