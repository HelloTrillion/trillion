"""SPN-2f: an explicit no ends a pending action, and the host honours it.

A plain refusal, said or typed as the user's whole turn, cancels the one card waiting
in that session through the same path `cancel_pending_action` takes. Anything more
than a plain no is left to the model, and a yes never approves anything here.
"""

from __future__ import annotations

import pytest

from tests.spine.conftest import Spine
from trillion.spine.confirm import Status
from trillion.spine.refusal import plain_no
from trillion.tools.record import DecisionRecord


@pytest.mark.parametrize(
    "said",
    [
        "no",
        "No.",
        "NO!",
        "nope",
        "nah",
        "no thanks",
        "No, thank you.",
        "don't",
        "Don\N{RIGHT SINGLE QUOTATION MARK}t do that.",
        "do not",
        "cancel",
        "Cancel that.",
        "never mind",
        "nevermind",
        "stop",
        "forget it",
        "um, no",
        "Uh... no, cancel that.",
        "no no no",
        "Actually, no.",
        "no, never mind",
        "No, please don't.",
    ],
)
def test_plain_refusals(said: str) -> None:
    assert plain_no(said)


@pytest.mark.parametrize(
    "said",
    [
        "",
        "um",
        "yes",
        "no, wait, make it say hello",
        "no wait",
        "no, call it notes.txt",
        "don't call it that",
        "stop calling it hi",
        "cancel the other one",
        "no problem",
        "not that one",
        "know",
        "nobody",
        "why not",
        "yes no",
        "no?",
    ],
)
def test_anything_more_than_a_plain_no_is_not_one(said: str) -> None:
    assert not plain_no(said)


async def propose(spine: Spine, name: str, session: str = "s1") -> str:
    r = await spine.call(
        "file_create",
        {"path": str(spine.granted / name), "content": "hi"},
        session_id=session,
    )
    assert r.status == "confirmation_required" and r.pending_id
    return r.pending_id


async def status_of(spine: Spine, pid: str) -> Status:
    a = await spine.store.get_pending(pid)
    assert a is not None
    return a.status


async def test_a_plain_no_cancels_the_one_open_action(spine: Spine) -> None:
    pid = await propose(spine, "note.txt")
    r = await spine.router.cancel_on_no("No.", spine.ctx(turn=2, via="voice"))
    assert r is not None and r.status == "cancelled" and r.pending_id == pid
    assert await status_of(spine, pid) is Status.REJECTED
    assert not (spine.granted / "note.txt").exists()
    # Asked again, a yes on the new card writes the file exactly.
    again = await propose(spine, "note.txt")
    ok = await spine.call("confirm_pending_action", {"pending_id": again}, turn=3)
    assert ok.status == "succeeded"
    assert (spine.granted / "note.txt").read_text() == "hi"


async def test_an_ambiguous_refusal_is_left_to_the_model(spine: Spine) -> None:
    pid = await propose(spine, "note.txt")
    said = "no, wait, make it say hello"
    assert await spine.router.cancel_on_no(said, spine.ctx(turn=2)) is None
    assert await status_of(spine, pid) is Status.PROPOSED


async def test_no_open_action_means_nothing_to_cancel(spine: Spine) -> None:
    assert await spine.router.cancel_on_no("no", spine.ctx(turn=2)) is None
    assert await spine.store.list_audit() == []


async def test_two_open_actions_are_left_to_the_model(spine: Spine) -> None:
    a = await propose(spine, "a.txt")
    b = await propose(spine, "b.txt")
    assert await spine.router.cancel_on_no("no", spine.ctx(turn=2)) is None
    assert await status_of(spine, a) is Status.PROPOSED
    assert await status_of(spine, b) is Status.PROPOSED


async def test_a_no_in_another_session_cancels_nothing(spine: Spine) -> None:
    """SPN-14: only the session that raised a card answers it."""
    pid = await propose(spine, "note.txt", session="s1")
    assert await spine.router.cancel_on_no("no", spine.ctx(turn=2, session_id="s2")) is None
    assert await status_of(spine, pid) is Status.PROPOSED


async def test_an_expired_action_is_not_open(spine: Spine) -> None:
    pid = await propose(spine, "note.txt")
    spine.clock.advance(11 * 60 * 1000)
    assert await spine.router.cancel_on_no("no", spine.ctx(turn=2)) is None
    assert await status_of(spine, pid) is Status.PROPOSED


@pytest.mark.parametrize("via", ["tap", "ask", "channel", "schedule", "routine"])
async def test_only_spoken_or_typed_words_count(spine: Spine, via: str) -> None:
    pid = await propose(spine, "note.txt")
    assert await spine.router.cancel_on_no("no", spine.ctx(turn=2, via=via)) is None
    assert await status_of(spine, pid) is Status.PROPOSED


async def test_a_yes_never_approves(spine: Spine) -> None:
    pid = await propose(spine, "note.txt")
    assert await spine.router.cancel_on_no("yes", spine.ctx(turn=2)) is None
    assert await status_of(spine, pid) is Status.PROPOSED
    assert not (spine.granted / "note.txt").exists()


@pytest.mark.parametrize(("via", "how"), [("voice", "spoken"), ("text", "typed")])
async def test_the_record_shows_it_declined_by_the_users_words(
    spine: Spine, via: str, how: str
) -> None:
    tool = DecisionRecord()
    tool.bind(spine.store, "local", spine.clock)
    spine.registry.register(tool)
    pid = await propose(spine, "note.txt")
    await spine.router.cancel_on_no("never mind", spine.ctx(turn=2, via=via))
    decisions = await spine.store.list_decisions(pid)
    assert [(d.event, d.via) for d in decisions][-1] == ("rejected", via)
    r = await spine.call("decision_record", {}, turn=9)
    assert f"answer: declined ({how})" in r.content
    assert "outcome: declined, so nothing was done" in r.content
