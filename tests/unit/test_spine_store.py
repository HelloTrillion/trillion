"""Proves the store half of "one approval, one execution": transition() is compare-and-set."""

from __future__ import annotations

from trillion.spine.confirm import PendingAction, Status
from trillion.spine.contract import Risk
from trillion.spine.store import DecisionIn
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.spine_store import SqliteSpineStore

NOW = 1_000_000


def _pending() -> PendingAction:
    return PendingAction(
        id="p1",
        owner_id="local",
        tool="file_create",
        args={"path": "/x"},
        args_hash="h",
        summary="Create x",
        target="/x",
        risk=Risk.IRREVERSIBLE,
        recoverable=True,
        session_id="s1",
        created_turn=1,
        status=Status.PROPOSED,
        created_at=NOW,
        expires_at=NOW + 1_000,
        represented_from=None,
    )


async def test_transition_is_compare_and_set(tmp_path):
    conn = await connect(tmp_path / "t.db")
    await migrate(conn)
    store = SqliteSpineStore(conn)
    await store.insert_pending(
        _pending(), DecisionIn(event="proposed", decided_by="s1", via="text")
    )

    by = DecisionIn(event="approved", decided_by="s1", via="text")
    first = await store.transition(
        "p1", Status.PROPOSED, Status.APPROVED, now_ms=NOW + 1, decision=by
    )
    second = await store.transition(
        "p1", Status.PROPOSED, Status.APPROVED, now_ms=NOW + 2, decision=by
    )

    assert first is True
    assert second is False

    decisions = await store.list_decisions("p1")
    assert [d.event for d in decisions] == ["proposed", "approved"]

    await conn.close()
