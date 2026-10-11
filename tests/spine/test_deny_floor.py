"""SPN-4: the floor holds on the normal path AND on the confirmed path."""

from __future__ import annotations

from tests.links import symlink_or_skip
from tests.spine.conftest import Spine
from trillion import paths
from trillion.spine.confirm import Refusal


async def test_protected_paths_are_refused_before_any_proposal(spine: Spine) -> None:
    for p in [
        spine.granted / ".GIT" / "config",
        spine.granted / ".ssh" / "authorized_keys",
        spine.granted / ".env",
        paths.home() / "persona" / "SOUL.md",
        paths.secrets_path(),
        spine.granted.parent / "outside.txt",
    ]:
        r = await spine.call("file_create", {"path": str(p), "content": "x"})
        assert r.reason is Refusal.DENIED_BY_FLOOR, p
        assert r.pending_id is None
    assert await spine.store.list_audit()  # refusals are recorded too


async def test_reads_of_the_secret_store_are_refused(spine: Spine) -> None:
    r = await spine.call("file_read", {"path": str(paths.secrets_path())})
    assert r.reason is Refusal.DENIED_BY_FLOOR


async def test_the_floor_applies_again_on_the_confirmed_path(spine: Spine) -> None:
    target = spine.granted / "link" / "x.txt"
    (spine.granted / "real").mkdir()
    symlink_or_skip(spine.granted / "link", spine.granted / "real")
    r = await spine.call("file_create", {"path": str(target), "content": "x"})
    assert r.status == "confirmation_required"
    # Between proposal and yes, the link is repointed into a protected directory.
    (spine.granted / "link").unlink()
    (spine.granted / ".ssh").mkdir()
    symlink_or_skip(spine.granted / "link", spine.granted / ".ssh")
    res = await spine.call("confirm_pending_action", {"pending_id": r.pending_id}, turn=2)
    assert res.reason is Refusal.DENIED_BY_FLOOR
    assert not (spine.granted / ".ssh" / "x.txt").exists()


async def test_revoking_a_folder_blocks_an_already_proposed_write(spine: Spine) -> None:
    r = await spine.call("file_create", {"path": str(spine.granted / "x.txt"), "content": "x"})
    res = await spine.call(
        "confirm_pending_action", {"pending_id": r.pending_id}, turn=2, granted_roots=()
    )
    assert res.reason is Refusal.DENIED_BY_FLOOR
    assert not (spine.granted / "x.txt").exists()


async def test_a_unicode_spelling_of_a_real_ssh_folder_is_refused(spine: Spine) -> None:
    """On APFS `.\u017fsh` and `.\u00dfh` open `.ssh`. On a case-sensitive volume the path
    does not exist, and it must still be refused by the fold rule."""
    (spine.granted / ".ssh").mkdir()
    (spine.granted / ".ssh" / "id").write_text("PRIVATE")
    for alias in (".\u017fsh", ".\u00dfh", ".SSH"):
        r = await spine.call("file_read", {"path": str(spine.granted / alias / "id")})
        assert r.reason is Refusal.DENIED_BY_FLOOR, alias
        assert "PRIVATE" not in r.to_model()
        w = await spine.call(
            "file_create", {"path": str(spine.granted / alias / "new"), "content": "x"}
        )
        assert w.reason is Refusal.DENIED_BY_FLOOR and w.pending_id is None, alias
