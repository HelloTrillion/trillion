"""Unseen decisions under Needs you (SPN-17, SPN-2b): a card that expired unanswered in
the last 7 days is listed until the user asks for it again or dismisses it. The list is
the record's; asking again is the ordinary re-present, a fresh card with fresh checks."""

from __future__ import annotations

from tests.spine.conftest import Spine
from trillion.spine.confirm import Status
from trillion.spine.router import EXPIRY_MS, MISSED_MS
from trillion.spine.store import DISMISSED, DecisionIn

OWNER = "local"


async def propose(spine: Spine, name: str, **ctx_kw: object) -> str:
    args = {"path": str(spine.granted / name), "content": "hi"}
    r = await spine.call("file_create", args, **ctx_kw)
    assert r.status == "confirmation_required" and r.pending_id is not None
    return r.pending_id


async def missed_ids(spine: Spine) -> list[str]:
    return [a.id for a in await spine.router.missed(OWNER)]


async def test_a_card_that_expired_unanswered_is_listed_and_one_waiting_is_not(
    spine: Spine,
) -> None:
    late = await propose(spine, "late.txt")
    spine.clock.advance(EXPIRY_MS)
    waiting = await propose(spine, "waiting.txt")
    # Expiry is written lazily: the late card's row still says proposed, and counts.
    row = await spine.store.get_pending(late)
    assert row is not None and row.status is Status.PROPOSED
    assert await missed_ids(spine) == [late]
    assert waiting not in await missed_ids(spine)


async def test_newest_first_and_marked_expired_counts_too(spine: Spine) -> None:
    first = await propose(spine, "one.txt")
    spine.clock.advance(1000)
    second = await propose(spine, "two.txt")
    spine.clock.advance(EXPIRY_MS + 1)
    # A tap after expiry writes the expiry; the card is still unanswered.
    late = await spine.router.resolve(first, "confirm", spine.ctx(turn=2, via="tap"))
    assert late.status == "refused" and late.reason == "expired"
    row = await spine.store.get_pending(first)
    assert row is not None and row.status is Status.EXPIRED
    assert await missed_ids(spine) == [second, first]


async def test_the_window_is_seven_days_from_expiry(spine: Spine) -> None:
    pid = await propose(spine, "week.txt")
    spine.clock.advance(EXPIRY_MS + MISSED_MS)
    assert await missed_ids(spine) == [pid]
    spine.clock.advance(1)
    assert await missed_ids(spine) == []


async def test_an_answered_card_is_never_listed(spine: Spine) -> None:
    approved = await propose(spine, "yes.txt")
    declined = await propose(spine, "no.txt")
    for pid in (approved, declined):
        await spine.store.mark_delivered(pid, OWNER, "web", spine.clock())
    ok = await spine.router.resolve(approved, "confirm", spine.ctx(turn=2, via="tap"))
    no = await spine.router.resolve(declined, "cancel", spine.ctx(turn=2, via="tap"))
    assert ok.status == "succeeded" and no.status == "cancelled"
    spine.clock.advance(EXPIRY_MS + 1)
    assert await missed_ids(spine) == []


async def test_a_superseded_card_is_not_listed(spine: Spine) -> None:
    old = await propose(spine, "same.txt")
    new = await propose(spine, "same.txt", turn=2)
    row = await spine.store.get_pending(old)
    assert row is not None and row.status is Status.SUPERSEDED
    spine.clock.advance(EXPIRY_MS + 1)
    assert await missed_ids(spine) == [new]


async def test_another_owners_cards_are_not_listed(spine: Spine) -> None:
    await propose(spine, "mine.txt")
    spine.clock.advance(EXPIRY_MS + 1)
    assert (
        await spine.store.missed("someone-else", since_ms=0, now_ms=spine.clock(), limit=10) == []
    )


async def test_asking_again_takes_it_off_and_its_copy_is_listed_if_it_expires(
    spine: Spine,
) -> None:
    pid = await propose(spine, "again.txt")
    spine.clock.advance(EXPIRY_MS + 1)
    r = await spine.router.represent(pid, spine.ctx(turn=2, via="tap"))
    assert r.status == "confirmation_required" and r.pending_id not in (None, pid)
    assert await missed_ids(spine) == []
    spine.clock.advance(EXPIRY_MS + 1)
    assert await missed_ids(spine) == [r.pending_id]


async def test_asking_again_is_a_fresh_card_with_fresh_checks(spine: Spine) -> None:
    pid = await propose(spine, "fresh.txt")
    old = await spine.store.get_pending(pid)
    assert old is not None
    spine.clock.advance(EXPIRY_MS + 1)
    # A share taken away since: the floor is checked again, and refuses.
    gone = await spine.router.represent(pid, spine.ctx(turn=2, via="tap", granted_roots=()))
    assert gone.status == "refused" and gone.reason == "denied_by_floor"
    # The file made since: the tool's own precheck runs again, and refuses.
    (spine.granted / "fresh.txt").write_bytes(b"made meanwhile")
    taken = await spine.router.represent(pid, spine.ctx(turn=2, via="tap"))
    assert taken.status == "refused" and taken.reason == "precondition_failed"
    # A refusal leaves it on the list: it was not asked for again.
    assert await missed_ids(spine) == [pid]
    (spine.granted / "fresh.txt").unlink()
    r = await spine.router.represent(pid, spine.ctx(turn=2, via="tap"))
    assert r.status == "confirmation_required" and r.pending_id is not None
    new = await spine.store.get_pending(r.pending_id)
    assert new is not None and new.status is Status.PROPOSED
    assert new.represented_from == pid and new.created_at == spine.clock()
    assert new.expires_at == spine.clock() + EXPIRY_MS > old.expires_at
    # Nothing ran: the new card waits for its own yes.
    assert not (spine.granted / "fresh.txt").exists()
    decisions = [d.event for d in await spine.store.list_decisions(pid)]
    assert decisions == ["proposed", "expired"]


async def test_a_dismissal_is_final_and_recorded(spine: Spine) -> None:
    pid = await propose(spine, "dismiss.txt")
    spine.clock.advance(EXPIRY_MS + 1)
    ctx = spine.ctx(turn=2, via="tap")
    assert await spine.router.dismiss(pid, ctx) is True
    assert await missed_ids(spine) == []
    # Written as expired first, then dismissed: one more immutable decision each.
    events = [d.event for d in await spine.store.list_decisions(pid)]
    assert events == ["proposed", "expired", DISMISSED]
    row = await spine.store.get_pending(pid)
    assert row is not None and row.status is Status.EXPIRED
    # Once only.
    assert await spine.router.dismiss(pid, ctx) is False
    events = [d.event for d in await spine.store.list_decisions(pid)]
    assert events.count(DISMISSED) == 1


async def test_only_an_expired_unanswered_card_can_be_dismissed(spine: Spine) -> None:
    waiting = await propose(spine, "waiting.txt")
    ctx = spine.ctx(turn=2, via="tap")
    assert await spine.router.dismiss(waiting, ctx) is False
    row = await spine.store.get_pending(waiting)
    assert row is not None and row.status is Status.PROPOSED
    await spine.store.mark_delivered(waiting, OWNER, "web", spine.clock())
    assert (await spine.router.resolve(waiting, "confirm", ctx)).status == "succeeded"
    assert await spine.router.dismiss(waiting, ctx) is False
    assert await spine.router.dismiss("no-such-card", ctx) is False
    asked = await propose(spine, "asked.txt", turn=2)
    spine.clock.advance(EXPIRY_MS + 1)
    again = await spine.router.represent(asked, spine.ctx(turn=3, via="tap"))
    assert again.status == "confirmation_required"
    # Asked for again: its copy is the live one, so the original can't be dismissed.
    assert (
        await spine.store.dismiss(
            asked, OWNER, DecisionIn(event=DISMISSED, decided_by="s1", via="tap"), spine.clock()
        )
        is False
    )


async def test_nobody_else_and_no_unattended_run_can_dismiss(spine: Spine) -> None:
    pid = await propose(spine, "theirs.txt")
    spine.clock.advance(EXPIRY_MS + 1)
    for via in ("routine", "schedule", "refresh"):
        assert await spine.router.dismiss(pid, spine.ctx(turn=2, via=via)) is False
    assert await spine.router.dismiss(pid, spine.ctx(turn=2, owner_id="nobody")) is False
    await spine.store._conn.execute("INSERT INTO owners (id, created_at) VALUES ('other', 0)")
    await spine.store._conn.commit()
    assert await spine.router.dismiss(pid, spine.ctx(turn=2, owner_id="other")) is False
    assert await missed_ids(spine) == [pid]
