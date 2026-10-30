"""One request, one card: the same gated call proposed again while its card waits
does not open a second pending action. A small model can repeat a call in one turn."""

from __future__ import annotations

import json

from tests.spine.conftest import Spine
from trillion.spine.confirm import EXPIRY_MS, Status


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


async def test_the_card_waiting_still_cannot_run_on_the_turn_it_was_proposed(
    spine: Spine,
) -> None:
    first = await spine.call("file_create", note(spine))
    await spine.call("file_create", note(spine), turn=2)
    same = await spine.call("confirm_pending_action", {"pending_id": first.pending_id}, turn=1)
    assert same.status == "refused"
    ok = await spine.call("confirm_pending_action", {"pending_id": first.pending_id}, turn=2)
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
    b = await spine.call("file_create", note(spine), turn=2)
    assert b.status == "confirmation_required"
    assert b.pending_id != a.pending_id and not b.already_asked


async def test_after_the_card_is_settled_the_same_call_asks_afresh(spine: Spine) -> None:
    a = await spine.call("file_create", note(spine))
    await spine.call("cancel_pending_action", {"pending_id": a.pending_id}, turn=2)
    b = await spine.call("file_create", note(spine), turn=2)
    assert b.pending_id != a.pending_id and not b.already_asked
