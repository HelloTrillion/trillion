"""SPN-17 and Phase 0 item 14: the decision record answers for itself.

Every line `decision_record` returns is read from the database. These tests drive
the router, so what is checked is what the model would actually be handed.
"""

from __future__ import annotations

import pytest

from tests.spine.conftest import Spine
from trillion.spine.router import EXPIRY_MS
from trillion.tools.record import DAY_MS, DecisionRecord


@pytest.fixture
def record(spine: Spine) -> DecisionRecord:
    tool = DecisionRecord()
    tool.bind(spine.store, "local", spine.clock)
    spine.registry.register(tool)
    return tool


async def propose(spine: Spine, name: str, turn: int = 1) -> str:
    r = await spine.call(
        "file_create", {"path": str(spine.granted / name), "content": "hi"}, turn=turn
    )
    assert r.pending_id is not None
    return r.pending_id


async def look(spine: Spine, **args: object) -> str:
    """What the record said, without the untrusted wrapper every tool result gets."""
    r = await spine.call("decision_record", dict(args), turn=9)
    assert r.status == "succeeded", r
    assert r.content.startswith('<untrusted_tool_result tool="decision_record">')
    return r.content.split("\n---\n", 1)[1].removesuffix("\n</untrusted_tool_result>")


async def test_an_approval_is_answered_from_the_record_alone(
    spine: Spine, record: DecisionRecord
) -> None:
    pid = await propose(spine, "note.txt")
    assert await spine.store.mark_delivered(pid, "local", "web", spine.clock())
    spine.clock.advance(60_000)
    ok = await spine.router.resolve(pid, "confirm", spine.ctx(turn=2, via="tap"))
    assert ok.status == "succeeded"
    # No conversation is involved from here on: the answer is in the database.
    said = await look(spine, about="note.txt")
    assert f"{pid}: Create " in said and "note.txt" in said
    assert "shown: on screen at" in said
    assert "answer: approved (a tap on the card)" in said
    assert "outcome: done" in said


async def test_a_card_on_screen_and_unanswered_is_waiting(
    spine: Spine, record: DecisionRecord
) -> None:
    pid = await propose(spine, "seen.txt")
    await spine.store.mark_delivered(pid, "local", "web", spine.clock())
    assert "outcome: waiting for an answer" in await look(spine)


async def test_a_card_that_was_never_shown_says_so(spine: Spine, record: DecisionRecord) -> None:
    await propose(spine, "unseen.txt")
    said = await look(spine)
    assert "shown: never; the card was not displayed on any screen" in said
    assert "answer: none" in said
    assert "outcome: not answered, and the user has not seen it: its card was never shown" in said


async def test_an_unanswered_card_past_its_time_is_reported_as_expired(
    spine: Spine, record: DecisionRecord
) -> None:
    await propose(spine, "late.txt")
    spine.clock.advance(EXPIRY_MS + 1)
    assert "outcome: expired without an answer, so nothing was done" in await look(spine)


async def test_a_spoken_no_is_recorded_as_spoken(spine: Spine, record: DecisionRecord) -> None:
    pid = await propose(spine, "no.txt")
    gone = await spine.call("cancel_pending_action", {"pending_id": pid}, turn=2, via="voice")
    assert gone.status == "cancelled"
    said = await look(spine)
    assert "answer: declined (spoken)" in said and "outcome: declined, so nothing was done" in said
    assert not (spine.granted / "no.txt").exists()


async def test_a_failure_carries_the_recorded_reason(spine: Spine, record: DecisionRecord) -> None:
    pid = await propose(spine, "gone.txt")
    (spine.granted / "gone.txt").write_text("someone else made it first")
    r = await spine.call("confirm_pending_action", {"pending_id": pid}, turn=2)
    assert r.status != "succeeded"
    said = await look(spine)
    assert "answer: approved (typed)" in said
    assert "outcome: " in said and "outcome: done" not in said


async def test_it_narrows_by_words_newest_first_and_within_a_window(
    spine: Spine, record: DecisionRecord
) -> None:
    old = await propose(spine, "old.txt")
    spine.clock.advance(40 * DAY_MS)
    first = await propose(spine, "first.txt", turn=2)
    spine.clock.advance(1_000)
    second = await propose(spine, "second.txt", turn=3)
    said = await look(spine)
    assert said.index(second) < said.index(first)  # newest first
    assert old not in said  # older than the default thirty days
    assert old in await look(spine, days=60)
    assert first in await look(spine, about="FIRST.TXT") and second not in await look(
        spine, about="first.txt"
    )
    assert await look(spine, limit=1) == (await look(spine)).split("\n\n")[0]


async def test_words_are_matched_as_plain_text(spine: Spine, record: DecisionRecord) -> None:
    await propose(spine, "axb.txt")
    assert "axb.txt" in await look(spine, about="axb")
    assert "axb.txt" in await look(spine, about="did the axb file ever get written")
    for wild in ("a_b", "axb_txt"):  # each would match "axb.txt" if "_" were a wildcard
        said = await look(spine, about=wild)
        assert said.startswith("no action needed an answer in the last 30 days matching"), wild


async def test_nothing_to_report_is_said_plainly(spine: Spine, record: DecisionRecord) -> None:
    assert await look(spine) == "no action needed an answer in the last 30 days"


async def test_another_owner_cannot_read_this_record(spine: Spine, record: DecisionRecord) -> None:
    await propose(spine, "mine.txt")
    # Called directly: an owner with no row of their own cannot reach the router yet (#4).
    r = await record.execute({}, spine.ctx(owner_id="someone-else"))
    assert not r.ok and "mine.txt" not in r.content
