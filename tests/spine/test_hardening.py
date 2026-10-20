"""#4: four smaller guarantees from the adversarial review of the spine.

1. Two proposals for one target can never both stay open, and the target is
   compared as the file system compares it.
2. Audit hashes are keyed, so a short value such as a path cannot be guessed back.
3. An owner that does not exist is refused cleanly, never turned into an error.
4. A card that was never shown cannot be approved.
"""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import pytest

from tests.spine.conftest import Spine
from trillion.spine.audit import KEY_BYTES, audit_key
from trillion.spine.confirm import Refusal, Status
from trillion.spine.deny_floor import DenyPolicy
from trillion.spine.hashing import args_hash
from trillion.spine.router import Router


async def open_proposals(spine: Spine) -> list[str]:
    return [a.id for a in await spine.store.list_by_status(Status.PROPOSED)]


# ── 1. One open proposal per target ──────────────────────────────────────


async def test_a_new_proposal_retires_the_one_before_it(spine: Spine) -> None:
    path = str(spine.granted / "notes.txt")
    first = await spine.call("file_create", {"path": path, "content": "one"})
    second = await spine.call("file_create", {"path": path, "content": "two"})
    assert await open_proposals(spine) == [second.pending_id]
    old = await spine.store.get_pending(first.pending_id or "")
    assert old is not None and old.status is Status.SUPERSEDED


async def test_two_spellings_of_one_file_are_one_target(spine: Spine) -> None:
    await spine.call("file_create", {"path": str(spine.granted / "Notes.txt"), "content": "a"})
    second = await spine.call(
        "file_create", {"path": str(spine.granted / "notes.TXT"), "content": "b"}
    )
    assert await open_proposals(spine) == [second.pending_id]


async def test_proposals_racing_for_one_target_leave_exactly_one_open(spine: Spine) -> None:
    path = str(spine.granted / "race.txt")
    results = await asyncio.gather(
        *(spine.call("file_create", {"path": path, "content": str(i)}) for i in range(10))
    )
    assert all(r.status == "confirmation_required" for r in results)
    assert len(await open_proposals(spine)) == 1


async def test_different_targets_do_not_retire_each_other(spine: Spine) -> None:
    for name in ("a.txt", "b.txt"):
        await spine.call("file_create", {"path": str(spine.granted / name), "content": "x"})
    assert len(await open_proposals(spine)) == 2


# ── 2. Keyed audit hashes ────────────────────────────────────────────────


async def test_an_audit_row_cannot_be_matched_to_a_guessed_path(spine: Spine) -> None:
    guess = {"path": str(spine.granted / "salary-2027.txt")}
    await spine.call("file_read", guess)
    (row,) = await spine.store.list_audit()
    assert row.args_hash.startswith("hmac:")
    assert args_hash(guess) not in row.args_hash  # the bare hash an attacker would compute


async def test_the_same_call_gets_the_same_audit_hash_under_one_key(spine: Spine) -> None:
    for _ in range(2):
        await spine.call("file_read", {"path": str(spine.granted / "same.txt")})
    first, second = await spine.store.list_audit()
    assert first.args_hash == second.args_hash  # rows still match each other


def test_the_audit_key_is_made_once_and_kept_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "home" / "audit.key"
    key = audit_key(path)
    assert len(key) == KEY_BYTES and audit_key(path) == key  # the same across restarts
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.write_bytes(b"short")  # damaged: replaced rather than trusted
    assert len(audit_key(path)) == KEY_BYTES


# ── 3. An owner that does not exist ──────────────────────────────────────


async def test_an_unknown_owner_is_refused_and_nothing_is_recorded(spine: Spine) -> None:
    before = len(await spine.store.list_audit())
    for r in (
        await spine.call("file_read", {"path": str(spine.granted / "x")}, owner_id="ghost"),
        await spine.router.resolve("p", "confirm", spine.ctx(owner_id="ghost", via="tap")),
        await spine.router.represent("p", spine.ctx(owner_id="ghost")),
    ):
        assert (r.status, r.reason) == ("refused", Refusal.UNKNOWN_OWNER)
    assert len(await spine.store.list_audit()) == before  # no row it could not name


# ── 4. Shown before it can be approved ───────────────────────────────────


@pytest.fixture
def strict(spine: Spine) -> Router:
    """The router as the running app builds it: a card must be shown to be approved."""
    router = Router(
        spine.registry,
        spine.store,
        policy=DenyPolicy.default(),
        clock=spine.clock,
        require_delivery=True,
    )
    spine.router = router
    return router


async def test_a_card_that_was_never_shown_cannot_be_approved(spine: Spine, strict: Router) -> None:
    r = await spine.call("file_create", {"path": str(spine.granted / "n.txt"), "content": "x"})
    pid = r.pending_id or ""
    for refused in (
        await strict.resolve(pid, "confirm", spine.ctx(turn=2, via="tap")),
        await spine.call("confirm_pending_action", {"pending_id": pid}, turn=2),
    ):
        assert (refused.status, refused.reason) == ("refused", Refusal.NOT_SHOWN)
    assert not (spine.granted / "n.txt").exists()
    a = await spine.store.get_pending(pid)
    assert a is not None and a.status is Status.PROPOSED  # still waiting, not lost
    assert await strict.mark_delivered(pid, "local", "web")
    ok = await strict.resolve(pid, "confirm", spine.ctx(turn=2, via="tap"))
    assert ok.status == "succeeded" and (spine.granted / "n.txt").read_text() == "x"


async def test_a_card_that_was_never_shown_can_still_be_declined(
    spine: Spine, strict: Router
) -> None:
    r = await spine.call("file_create", {"path": str(spine.granted / "n.txt"), "content": "x"})
    no = await strict.resolve(r.pending_id or "", "cancel", spine.ctx(turn=2, via="tap"))
    assert no.status == "cancelled"


def test_the_running_app_requires_cards_to_be_shown() -> None:
    import inspect

    from trillion.api import runtime

    assert "require_delivery=True" in inspect.getsource(runtime.open_runtime)
