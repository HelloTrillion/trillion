"""The file tools through the router: what the user approved is what lands, and nothing else."""

from __future__ import annotations

from tests.spine.conftest import Spine
from trillion.spine.confirm import Status


async def test_an_approved_append_lands_and_is_verified(spine: Spine) -> None:
    log = spine.granted / "log.txt"
    log.write_text("a\n")
    r = await spine.call("file_append", {"path": str(log), "content": "b\n"})
    assert r.status == "confirmation_required"
    assert log.read_text() == "a\n"
    ok = await spine.call("confirm_pending_action", {"pending_id": r.pending_id}, turn=2)
    assert ok.status == "succeeded"
    assert log.read_text() == "a\nb\n"


async def test_append_refuses_a_symlink_swapped_in_after_approval(spine: Spine) -> None:
    """The floor passes (the link points inside the granted folder), so O_NOFOLLOW must hold."""
    log = spine.granted / "log.txt"
    other = spine.granted / "other.txt"
    log.write_text("a\n")
    other.write_text("keep\n")
    r = await spine.call("file_append", {"path": str(log), "content": "b\n"})
    assert r.status == "confirmation_required"
    # Between proposal and yes, the approved file is swapped for a link to a different file.
    log.unlink()
    log.symlink_to(other)
    res = await spine.call("confirm_pending_action", {"pending_id": r.pending_id}, turn=2)
    assert res.status == "failed", res  # nothing was opened, so nothing may have landed
    assert other.read_text() == "keep\n"
    assert log.is_symlink()
    a = await spine.store.get_pending(r.pending_id or "")
    assert a is not None and a.status is Status.FAILED and a.effect_status == "none"
