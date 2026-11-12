"""§5.3a: the gate is speaker-scoped (SPN-14a, SPN-15a, SPN-18) and a voice that isn't
the owner's gets nothing personal (PER-13). The session judges the voice; these tests
give the spine each verdict and check what it does with it."""

from __future__ import annotations

import pytest

from tests.spine.conftest import Spine
from tests.spine.fakes import MARKER
from trillion.spine.confirm import (
    NOT_OWNER_DETAIL,
    REPLAY_DETAIL,
    UNCHECKED_DETAIL,
    UNSURE_AGAIN,
    UNSURE_TAP,
    Refusal,
    Status,
)
from trillion.spine.contract import Speaker
from trillion.spine.router import GUEST_TOOLS, RouterResponse


async def propose(spine: Spine, name: str = "note.txt") -> str:
    r = await spine.call(
        "file_create", {"path": str(spine.granted / name), "content": f"hi {MARKER}"}
    )
    assert r.status == "confirmation_required" and r.pending_id
    return r.pending_id


async def say_yes(spine: Spine, pid: str, who: Speaker | None, turn: int = 2) -> RouterResponse:
    return await spine.call(
        "confirm_pending_action", {"pending_id": pid}, turn=turn, via="voice", speaker=who
    )


async def test_the_owners_spoken_yes_approves(spine: Spine) -> None:
    pid = await propose(spine)
    r = await say_yes(spine, pid, "owner")
    assert r.status == "succeeded"
    assert (spine.granted / "note.txt").exists()


async def test_with_nobody_enrolled_a_spoken_yes_is_as_before(spine: Spine) -> None:
    # The Phase 0 anchor: the raising session (SPN-14). No voice is judged.
    pid = await propose(spine)
    assert (await say_yes(spine, pid, None)).status == "succeeded"


async def test_another_voice_cannot_approve(spine: Spine) -> None:
    # SPN-18: a second voice mid-session may talk to her, never approve.
    pid = await propose(spine)
    r = await say_yes(spine, pid, "other")
    assert r.status == "refused" and r.reason is Refusal.NOT_OWNER
    assert r.detail == NOT_OWNER_DETAIL
    pending = await spine.store.get_pending(pid)
    assert pending is not None and pending.status is Status.PROPOSED
    assert not (spine.granted / "note.txt").exists()
    # The owner can still approve it afterwards.
    assert (await say_yes(spine, pid, "owner", turn=3)).status == "succeeded"


async def test_a_replayed_yes_cannot_approve(spine: Spine) -> None:
    # SPN-14a's first fixture: the owner's own earlier yes, played back. The session's
    # replay check calls it "replay"; the spine refuses it and points at the tap.
    pid = await propose(spine)
    r = await say_yes(spine, pid, "replay")
    assert r.reason is Refusal.NOT_OWNER and r.detail == REPLAY_DETAIL
    assert not (spine.granted / "note.txt").exists()


async def test_an_unsure_yes_asks_again_then_for_the_tap(spine: Spine) -> None:
    # SPN-15a: never a dead end. A fresh sample first, then the card's tap.
    pid = await propose(spine)
    first = await say_yes(spine, pid, "unsure")
    assert first.reason is Refusal.VOICE_UNSURE and first.detail == UNSURE_AGAIN
    second = await say_yes(spine, pid, "unsure", turn=3)
    assert second.reason is Refusal.VOICE_UNSURE and second.detail == UNSURE_TAP
    # The fresh sample that matches is enough...
    assert (await say_yes(spine, pid, "owner", turn=4)).status == "succeeded"


async def test_after_an_unsure_yes_the_tap_still_approves(spine: Spine) -> None:
    pid = await propose(spine)
    await say_yes(spine, pid, "unsure")
    await say_yes(spine, pid, "unsure", turn=3)
    # The tap path is unchanged: a physical act on the card, whoever is speaking.
    tap = await spine.router.resolve(pid, "confirm", spine.ctx(turn=4, via="tap"))
    assert tap.status == "succeeded"


async def test_with_the_voiceprint_unreadable_no_spoken_yes_approves(spine: Spine) -> None:
    # 2026-11-12 spec: an owner enrolled but the file can't be read, so no voice can be
    # checked. Fails closed: never back to Phase 0's "any voice". Asking for the yes
    # again can't help, so it is the tap every time (SPN-15a), and the card stays.
    pid = await propose(spine)
    for turn in (2, 3, 4):
        r = await say_yes(spine, pid, "unchecked", turn=turn)
        assert r.status == "refused" and r.reason is Refusal.VOICE_UNCHECKED
        assert r.detail == UNCHECKED_DETAIL and "tap" in r.detail
    pending = await spine.store.get_pending(pid)
    assert pending is not None and pending.status is Status.PROPOSED
    assert not (spine.granted / "note.txt").exists()
    # The tap and a typed yes are the session's, as always.
    tap = await spine.router.resolve(pid, "confirm", spine.ctx(turn=5, via="tap"))
    assert tap.status == "succeeded"


async def test_with_the_voiceprint_unreadable_a_typed_yes_is_the_sessions(spine: Spine) -> None:
    pid = await propose(spine)
    r = await spine.call("confirm_pending_action", {"pending_id": pid}, turn=2, via="text")
    assert r.status == "succeeded"


async def test_with_the_voiceprint_unreadable_spend_still_needs_the_tap(spine: Spine) -> None:
    r = await spine.call("spend_money", {"amount_cents": 500})
    said = await say_yes(spine, r.pending_id or "", "unchecked")
    assert said.reason is Refusal.TAP_REQUIRED


async def test_a_tap_is_never_judged_by_voice(spine: Spine) -> None:
    pid = await propose(spine)
    tap = await spine.router.resolve(pid, "confirm", spine.ctx(turn=2, via="tap", speaker="other"))
    assert tap.status == "succeeded"


async def test_a_typed_yes_carries_no_voice(spine: Spine) -> None:
    pid = await propose(spine)
    r = await spine.call("confirm_pending_action", {"pending_id": pid}, turn=2, via="text")
    assert r.status == "succeeded"


async def test_unsure_counts_are_per_card(spine: Spine) -> None:
    a = await propose(spine, "a.txt")
    b = await propose(spine, "b.txt")
    assert (await say_yes(spine, a, "unsure")).detail == UNSURE_AGAIN
    assert (await say_yes(spine, b, "unsure")).detail == UNSURE_AGAIN


async def test_any_voice_may_decline(spine: Spine) -> None:
    # Declining is the safe direction: it runs nothing, and the card can be asked again.
    pid = await propose(spine)
    r = await spine.call(
        "cancel_pending_action", {"pending_id": pid}, turn=2, via="voice", speaker="other"
    )
    assert r.status == "cancelled"


async def test_spend_and_unrecoverable_still_need_the_tap_from_the_owner(spine: Spine) -> None:
    # A matched voice is not a stronger yes than SPN-14 asks for: spend still taps.
    r = await spine.call("spend_money", {"amount_cents": 500})
    pid = r.pending_id or ""
    said = await say_yes(spine, pid, "owner")
    assert said.reason is Refusal.TAP_REQUIRED


@pytest.mark.parametrize("who", ["other", "unsure", "replay", "unchecked"])
async def test_nothing_personal_runs_for_another_voice(spine: Spine, who: Speaker) -> None:
    # PER-13, enforced at the router: files are refused before any of their code runs.
    (spine.granted / "diary.txt").write_text(MARKER)
    r = await spine.call(
        "file_read", {"path": str(spine.granted / "diary.txt")}, via="voice", speaker=who
    )
    assert r.status == "refused" and r.reason is Refusal.PRIVATE
    assert MARKER not in r.to_model()
    listed = await spine.call("file_list", {"path": str(spine.granted)}, via="voice", speaker=who)
    assert listed.reason is Refusal.PRIVATE
    # Nor can it raise a card.
    made = await spine.call(
        "file_create",
        {"path": str(spine.granted / "x.txt"), "content": "x"},
        via="voice",
        speaker=who,
    )
    assert made.reason is Refusal.PRIVATE
    assert await spine.store.list_by_status(Status.PROPOSED) == []


async def test_the_owner_reads_as_before(spine: Spine) -> None:
    (spine.granted / "diary.txt").write_text("mine")
    r = await spine.call(
        "file_read", {"path": str(spine.granted / "diary.txt")}, via="voice", speaker="owner"
    )
    assert r.status == "succeeded"


def test_guest_tools_are_only_what_anyone_may_hear() -> None:
    # Adding a tool here is a decision about what a stranger in the room may do.
    assert frozenset({"web_fetch", "stay_quiet"}) == GUEST_TOOLS
