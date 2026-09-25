"""The file tools through the router: what the user approved is what lands, and nothing else."""

from __future__ import annotations

from tests.spine.conftest import Spine
from trillion.spine.confirm import Refusal, Status
from trillion.tools.files import FileOverwrite


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
    """The floor passes (the link points inside the granted folder) and the fingerprint
    matches (the other file has the same bytes), so O_NOFOLLOW must hold."""
    log = spine.granted / "log.txt"
    other = spine.granted / "other.txt"
    log.write_text("a\n")
    other.write_text("a\n")
    r = await spine.call("file_append", {"path": str(log), "content": "b\n"})
    assert r.status == "confirmation_required"
    # Between proposal and yes, the approved file is swapped for a link to a different file.
    log.unlink()
    log.symlink_to(other)
    res = await spine.call("confirm_pending_action", {"pending_id": r.pending_id}, turn=2)
    assert res.status == "failed", res  # nothing was opened, so nothing may have landed
    assert other.read_text() == "a\n"
    assert log.is_symlink()
    a = await spine.store.get_pending(r.pending_id or "")
    assert a is not None and a.status is Status.FAILED and a.effect_status == "none"


async def test_an_overwrite_notices_the_file_changed_since_the_proposal(spine: Spine) -> None:
    """The user edits the file after the card was shown: the tap must not clobber the edit."""
    doc = spine.granted / "doc.md"
    doc.write_text("v1")
    r = await spine.call("file_overwrite", {"path": str(doc), "content": "model"})
    assert r.status == "confirmation_required"
    doc.write_text("v1 plus the user's edit")
    res = await spine.router.resolve(r.pending_id or "", "confirm", spine.ctx(turn=2, via="tap"))
    assert res.reason is Refusal.PRECONDITION_CHANGED, res
    assert res.detail == "the target changed since you were asked"
    assert doc.read_text() == "v1 plus the user's edit"
    a = await spine.store.get_pending(r.pending_id or "")
    assert a is not None and a.status is Status.INVALIDATED
    # Asking again shows a fresh card, fingerprinted against the file as it is now.
    again = await spine.router.represent(r.pending_id or "", spine.ctx(turn=3))
    assert again.status == "confirmation_required" and again.pending_id != r.pending_id
    ok = await spine.router.resolve(again.pending_id or "", "confirm", spine.ctx(turn=4, via="tap"))
    assert ok.status == "succeeded" and doc.read_text() == "model"


async def test_an_append_notices_the_file_changed_since_the_proposal(spine: Spine) -> None:
    log = spine.granted / "log.txt"
    log.write_text("a\n")
    r = await spine.call("file_append", {"path": str(log), "content": "b\n"})
    log.write_text("rewritten\n")
    res = await spine.call("confirm_pending_action", {"pending_id": r.pending_id}, turn=2)
    assert res.reason is Refusal.PRECONDITION_CHANGED
    assert log.read_text() == "rewritten\n"


async def test_the_fingerprint_is_frozen_with_the_proposal(spine: Spine) -> None:
    doc = spine.granted / "doc.md"
    doc.write_text("v1")
    r = await spine.call("file_overwrite", {"path": str(doc), "content": "x"})
    a = await spine.store.get_pending(r.pending_id or "")
    assert a is not None and a.precondition_token == FileOverwrite().fingerprint(a.args)
    n = await spine.call("file_create", {"path": str(spine.granted / "n.txt"), "content": "x"})
    b = await spine.store.get_pending(n.pending_id or "")
    assert b is not None and b.precondition_token == "absent"  # noqa: S105 - not a secret
    s = await spine.call("spend_money", {"amount_cents": 1})
    c = await spine.store.get_pending(s.pending_id or "")
    assert c is not None and c.precondition_token is None  # no hook, nothing to compare


async def test_a_file_that_grows_after_the_size_check_is_not_read_whole(spine: Spine) -> None:
    from trillion.spine.contract import JSON, Precondition, ToolContext
    from trillion.tools.files import MAX_READ_BYTES

    big = spine.granted / "grows.log"
    big.write_text("small")
    reader = spine.registry.get("file_read")
    assert reader is not None
    checked = reader.precheck

    async def check_then_grow(args: JSON, ctx: ToolContext) -> Precondition:
        verdict = await checked(args, ctx)
        big.write_bytes(b"x" * (MAX_READ_BYTES * 3))
        return verdict

    reader.precheck = check_then_grow  # type: ignore[method-assign]
    r = await spine.call("file_read", {"path": str(big)})
    assert r.status == "failed", r.status
    assert len(r.content) < 10_000
