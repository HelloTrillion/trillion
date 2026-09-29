"""Approved arguments are not kept forever (issue #20).

`pending_actions.args_json` held each proposal's full arguments — for a file write,
up to a megabyte of the file — and the freeze trigger forbids ever changing it, so the
database was becoming a second copy of everything the agent ever wrote.

The frozen row now keeps a slim copy: any long string is replaced by its SHA-256 and
length, and `args_hash` still covers the full arguments, so what was approved stays
provable. The full arguments live in `pending_action_args`, where the only change the
database allows is clearing them. `Router.forget_settled_arguments` clears them thirty
days after an action settles. Outcomes, decisions and the audit trail are untouched.
"""

from __future__ import annotations

import json

import aiosqlite
import pytest

from tests.spine.conftest import Spine
from trillion.spine.confirm import Refusal, Status
from trillion.spine.hashing import args_hash
from trillion.spine.retention import RETENTION_MS, SLIM_OVER

DAY = 24 * 60 * 60 * 1000
BIG = "x" * (SLIM_OVER * 4)


async def _raw(spine: Spine, sql: str, *params: object) -> list[aiosqlite.Row]:
    async with spine.store._conn.execute(sql, params) as cur:
        return list(await cur.fetchall())


async def _written(spine: Spine, name: str = "note.txt") -> str:
    """Propose and approve a file_create carrying a large body; return its id."""
    r = await spine.call("file_create", {"path": str(spine.granted / name), "content": BIG})
    ok = await spine.call("confirm_pending_action", {"pending_id": r.pending_id}, turn=2)
    assert ok.status == "succeeded", ok
    return r.pending_id or ""


# ── What the frozen row keeps ─────────────────────────────────────────


async def test_the_frozen_row_does_not_hold_the_content(spine: Spine) -> None:
    pid = await _written(spine)
    (row,) = await _raw(spine, "SELECT args_json, args_hash FROM pending_actions WHERE id = ?", pid)
    stored = json.loads(row["args_json"])
    assert BIG not in row["args_json"]
    assert stored["content"]["$omitted"]["chars"] == len(BIG)
    assert stored["path"] == str(spine.granted / "note.txt")  # small values stay readable


async def test_the_hash_still_proves_the_full_arguments(spine: Spine) -> None:
    pid = await _written(spine)
    (row,) = await _raw(spine, "SELECT args_hash FROM pending_actions WHERE id = ?", pid)
    full = {"content": BIG, "path": str(spine.granted / "note.txt")}
    a = await spine.store.get_pending(pid)
    assert a is not None and a.args == full
    assert row["args_hash"] == args_hash(full)


# ── The sweep ─────────────────────────────────────────────────────────


async def test_content_is_cleared_thirty_days_after_it_settles(spine: Spine) -> None:
    pid = await _written(spine)
    spine.clock.advance(RETENTION_MS + DAY)
    assert await spine.router.forget_settled_arguments() == 1
    a = await spine.store.get_pending(pid)
    assert a is not None and a.args_cleared
    assert BIG not in json.dumps(a.args)
    (side,) = await _raw(spine, "SELECT args_json, cleared_at FROM pending_action_args")
    assert side["args_json"] is None and side["cleared_at"] == spine.clock.now


async def test_content_younger_than_thirty_days_is_kept(spine: Spine) -> None:
    pid = await _written(spine)
    spine.clock.advance(RETENTION_MS - DAY)
    assert await spine.router.forget_settled_arguments() == 0
    a = await spine.store.get_pending(pid)
    assert a is not None and not a.args_cleared and a.args["content"] == BIG


async def test_an_open_proposal_is_never_cleared_however_old(spine: Spine) -> None:
    """Only settled actions are forgotten. Something still waiting for a yes keeps
    everything it would need to run."""
    r = await spine.call("file_create", {"path": str(spine.granted / "n.txt"), "content": BIG})
    spine.clock.advance(RETENTION_MS * 3)
    await spine.router.forget_settled_arguments()
    a = await spine.store.get_pending(r.pending_id or "")
    assert a is not None and a.status is Status.PROPOSED  # expiry is applied lazily
    assert not a.args_cleared
    (side,) = await _raw(spine, "SELECT args_json FROM pending_action_args")
    assert side["args_json"] is not None


async def test_the_outcome_survives_clearing(spine: Spine) -> None:
    pid = await _written(spine)
    before = await spine.store.list_decisions(pid)
    spine.clock.advance(RETENTION_MS + DAY)
    await spine.router.forget_settled_arguments()
    a = await spine.store.get_pending(pid)
    assert a is not None and a.status is Status.SUCCEEDED and a.effect_status == "confirmed"
    assert await spine.store.list_decisions(pid) == before
    again = await spine.call("confirm_pending_action", {"pending_id": pid}, turn=9)
    assert again.status == "succeeded"  # the record still answers for itself


async def test_an_old_expired_proposal_cannot_be_asked_again(spine: Spine) -> None:
    r = await spine.call("file_create", {"path": str(spine.granted / "n.txt"), "content": BIG})
    pid = r.pending_id or ""
    spine.clock.advance(RETENTION_MS + DAY)
    stale = await spine.call("confirm_pending_action", {"pending_id": pid}, turn=2)
    assert stale.reason is Refusal.EXPIRED
    spine.clock.advance(RETENTION_MS + DAY)
    assert await spine.router.forget_settled_arguments() == 1
    again = await spine.router.represent(pid, spine.ctx(turn=3))
    assert again.reason is Refusal.NOT_REPRESENTABLE
    assert "30 days" in (again.detail or "")


# ── The side table can only be cleared ────────────────────────────────


async def test_full_arguments_can_be_cleared_but_never_edited(spine: Spine) -> None:
    pid = await _written(spine)
    conn = spine.store._conn
    with pytest.raises(aiosqlite.Error, match="only be cleared"):
        await conn.execute(
            "UPDATE pending_action_args SET args_json = '{}' WHERE pending_id = ?", (pid,)
        )
    with pytest.raises(aiosqlite.Error, match="only be cleared"):
        await conn.execute(
            "UPDATE pending_action_args SET owner_id = 'x' WHERE pending_id = ?", (pid,)
        )


async def test_cleared_arguments_can_never_come_back(spine: Spine) -> None:
    pid = await _written(spine)
    spine.clock.advance(RETENTION_MS + DAY)
    await spine.router.forget_settled_arguments()
    with pytest.raises(aiosqlite.Error, match="only be cleared"):
        await spine.store._conn.execute(
            "UPDATE pending_action_args SET args_json = '{}', cleared_at = NULL"
            " WHERE pending_id = ?",
            (pid,),
        )


async def test_the_side_table_refuses_deletes(spine: Spine) -> None:
    await _written(spine)
    with pytest.raises(aiosqlite.Error, match="append-only"):
        await spine.store._conn.execute("DELETE FROM pending_action_args")


async def test_a_large_edit_is_slimmed_too(spine: Spine) -> None:
    """file_edit arrived after the retention design: its old/new text can be as large
    as a whole file, and it is slimmed and cleared the same way."""
    doc = spine.granted / "big.md"
    doc.write_bytes(b"head\n" + BIG.encode() + b"\ntail\n")
    r = await spine.call("file_edit", {"path": str(doc), "old": BIG, "new": BIG + "!"})
    assert r.status == "confirmation_required", r
    (row,) = await _raw(spine, "SELECT args_json FROM pending_actions WHERE id = ?", r.pending_id)
    assert BIG not in row["args_json"]
    a = await spine.store.get_pending(r.pending_id or "")
    assert a is not None and a.args["old"] == BIG  # full arguments still there to run
