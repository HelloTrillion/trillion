"""One request, one card: the same gated call proposed again while its card waits
does not open a second pending action. A small model can repeat a call in one turn."""

from __future__ import annotations

import json

from tests.spine.conftest import Spine
from trillion.spine.confirm import EXPIRY_MS, Refusal, Status


def note(spine: Spine, content: str = "hi") -> dict[str, str]:
    return {"path": str(spine.granted / "note.txt"), "content": content}


async def test_the_same_call_again_returns_the_card_already_waiting(spine: Spine) -> None:
    first = await spine.call("file_create", note(spine))
    again = await spine.call("file_create", note(spine), call_id="c2")
    assert first.status == again.status == "confirmation_required"
    assert again.pending_id == first.pending_id
    assert again.summary == first.summary
    assert again.already_asked and not first.already_asked
    # The model is told the card is already up, so it asks once.
    told = json.loads(again.to_model())
    assert told["pending_id"] == first.pending_id and "already" in told["detail"]
    # Nothing here knows the card was shown, so it does not say so (SPN-17).
    assert "screen" not in told["detail"]
    assert "already_asked" not in told

    waiting = await spine.store.list_by_status(Status.PROPOSED)
    assert [a.id for a in waiting] == [first.pending_id]
    events = [d.event for d in await spine.store.list_decisions(first.pending_id or "")]
    assert events == ["proposed"]
    # Both calls are in the audit, the second as a duplicate of the first.
    audit = [(r.tool, r.decision, r.outcome) for r in await spine.store.list_audit()]
    assert audit == [
        ("file_create", "proposed", "confirmation_required"),
        ("file_create", "duplicate", "confirmation_required"),
    ]


async def test_a_repeat_in_the_same_turn_still_cannot_run_on_that_turn(spine: Spine) -> None:
    first = await spine.call("file_create", note(spine))
    again = await spine.call("file_create", note(spine), call_id="c2")
    assert again.pending_id == first.pending_id
    same = await spine.call("confirm_pending_action", {"pending_id": first.pending_id}, turn=1)
    assert same.reason is Refusal.SAME_TURN
    assert not (spine.granted / "note.txt").exists()


async def test_a_repeat_on_a_later_turn_is_a_fresh_card_that_waits_a_turn(spine: Spine) -> None:
    """Returning the old card on a later turn would let the model propose and confirm in
    one turn (SPN-2): a later repeat supersedes it with a card of its own."""
    first = await spine.call("file_create", note(spine))
    later = await spine.call("file_create", note(spine), turn=2)
    assert later.status == "confirmation_required"
    assert later.pending_id != first.pending_id and not later.already_asked
    same = await spine.call("confirm_pending_action", {"pending_id": later.pending_id}, turn=2)
    assert same.reason is Refusal.SAME_TURN
    old = await spine.store.get_pending(first.pending_id or "")
    assert old is not None and old.status is Status.SUPERSEDED
    ok = await spine.call("confirm_pending_action", {"pending_id": later.pending_id}, turn=3)
    assert ok.status == "succeeded"
    assert (spine.granted / "note.txt").read_text() == "hi"


async def test_different_arguments_still_get_a_card_of_their_own(spine: Spine) -> None:
    a = await spine.call("file_create", note(spine, "one"))
    b = await spine.call("file_create", {"path": str(spine.granted / "b.txt"), "content": "x"})
    assert a.pending_id != b.pending_id and not b.already_asked
    assert len(await spine.store.list_by_status(Status.PROPOSED)) == 2


async def test_another_session_gets_its_own_card(spine: Spine) -> None:
    a = await spine.call("file_create", note(spine))
    b = await spine.call("file_create", note(spine), session_id="s2")
    assert a.pending_id != b.pending_id and not b.already_asked


async def test_after_the_card_expires_the_same_call_asks_afresh(spine: Spine) -> None:
    a = await spine.call("file_create", note(spine))
    spine.clock.advance(EXPIRY_MS + 1)
    b = await spine.call("file_create", note(spine))
    assert b.status == "confirmation_required"
    assert b.pending_id != a.pending_id and not b.already_asked


async def test_after_the_card_is_settled_the_same_call_asks_afresh(spine: Spine) -> None:
    a = await spine.call("file_create", note(spine))
    await spine.call("cancel_pending_action", {"pending_id": a.pending_id})
    b = await spine.call("file_create", note(spine))
    assert b.pending_id != a.pending_id and not b.already_asked
