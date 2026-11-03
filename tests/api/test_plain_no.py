"""SPN-2f through a real turn: a plain no ends the card, and she is told the host did it.

PRD 9 item 7: "Write a note to my Desktop saying hi." A card appears and she asks;
"no" cancels; asked again, a yes writes the file exactly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.api.test_drafts import finish, judging, until
from tests.api.test_listening import hearing, speak
from tests.api.test_session import Page, create, idle
from tests.brain.fakes import ScriptedProvider, say, use
from tests.voice.audio import room, tone
from trillion.api.runtime import Runtime, open_runtime
from trillion.api.session import Session
from trillion.brain.types import ContextBlock, Msg, TextBlock
from trillion.secrets.store import MemoryStore
from trillion.spine.confirm import Status


@pytest.fixture
async def rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    granted = tmp_path / "granted"
    granted.mkdir()
    runtime = await open_runtime(
        share_rules=lambda _p: None,
        granted_roots=(granted,),
        persona=tmp_path,
        secrets=MemoryStore(),
        env={},
    )
    yield runtime
    await runtime.close()


def last_user(provider: ScriptedProvider) -> Msg:
    _, messages = provider.calls[-1]
    return [m for m in messages if m.role == "user"][-1]


def notes(msg: Msg) -> list[str]:
    return [b.text for b in msg.blocks if isinstance(b, ContextBlock)]


async def status_of(rt: Runtime, pid: str) -> Status:
    a = await rt.spine_store.get_pending(pid)
    assert a is not None
    return a.status


async def test_a_typed_no_cancels_and_she_is_told(rt: Runtime) -> None:
    provider = ScriptedProvider(
        create(rt, "note.txt", "c1"), say("Shall I?"), say("Okay, left it.")
    )
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "write a note saying x"})
    await idle(session)
    (card,) = page.all("confirm.request")
    pid = card["pending_id"]

    await session.handle({"type": "turn.text", "text": "No."})
    await idle(session)
    assert await status_of(rt, pid) is Status.REJECTED
    resolved = page.all("confirm.resolved")
    assert [(r["pending_id"], r["status"]) for r in resolved] == [(pid, "cancelled")]
    told = last_user(provider)
    assert told.blocks[-1] == TextBlock(text="No.")
    (note,) = [n for n in notes(told) if n.startswith("[host]")]
    assert pid in note and '"status": "cancelled"' in note
    assert "do not call cancel_pending_action" in note
    # The card is no longer listed as waiting in what she is told.
    assert f"waiting for an answer: {pid}" not in notes(told)[0]
    decisions = await rt.spine_store.list_decisions(pid)
    assert (decisions[-1].event, decisions[-1].via) == ("rejected", "text")
    assert not (rt.granted_roots[0] / "note.txt").exists()
    await session.close()


async def test_a_no_with_more_to_it_is_left_to_her(rt: Runtime) -> None:
    provider = ScriptedProvider(create(rt, "note.txt", "c1"), say("Shall I?"), say("Sure."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "write a note saying x"})
    await idle(session)
    pid = page.all("confirm.request")[0]["pending_id"]
    await session.handle({"type": "turn.text", "text": "no, wait, make it say hello"})
    await idle(session)
    assert await status_of(rt, pid) is Status.PROPOSED
    assert page.all("confirm.resolved") == []
    assert not [n for n in notes(last_user(provider)) if n.startswith("[host]")]
    await session.close()


async def test_a_spoken_no_cancels_and_she_is_told(rt: Runtime) -> None:
    stt = hearing(rt)
    path = str(rt.granted_roots[0] / "note.txt")
    provider = ScriptedProvider(
        use("file_create", {"path": path, "content": "hi"}, "c1"),
        say("Shall I?"),
        say("Okay, I won't."),
        use("file_create", {"path": path, "content": "hi"}, "c2"),
        say("Shall I?"),
        use("confirm_pending_action", {"pending_id": "?"}, "c3"),
        say("Done."),
    )
    rt.provider = provider
    rt.think_early = False
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "Write a note to my Desktop saying hi."})
    await idle(session)
    first = page.all("confirm.request")[0]["pending_id"]

    await session.handle({"type": "listen.start"})
    await speak(session, room(0.2) + tone(0.6))
    stt.stream.say("No.", final=True)
    await speak(session, room(0.6))
    await idle(session)
    assert await status_of(rt, first) is Status.REJECTED
    # Their words show before the card goes.
    kinds = [m["type"] for m in page.got]
    assert kinds.index("transcript.final") < kinds.index("confirm.resolved")
    decisions = await rt.spine_store.list_decisions(first)
    assert (decisions[-1].event, decisions[-1].via) == ("rejected", "voice")
    assert any(n.startswith("[host]") and first in n for n in notes(last_user(provider)))
    assert not (rt.granted_roots[0] / "note.txt").exists()

    # Asked again, a yes writes the file exactly.
    await session.handle({"type": "turn.text", "text": "Write a note to my Desktop saying hi."})
    await idle(session)
    second = page.all("confirm.request")[-1]["pending_id"]
    assert second != first
    provider._replies[0] = use("confirm_pending_action", {"pending_id": second}, "c3")
    await session.handle({"type": "turn.text", "text": "yes"})
    await idle(session)
    assert await status_of(rt, second) is Status.SUCCEEDED
    assert (rt.granted_roots[0] / "note.txt").read_text() == "hi"
    await session.close()


async def test_a_draft_made_while_the_card_waited_is_not_used(rt: Runtime) -> None:
    """VOI-12: a draft sent before the no still listed the card as waiting, and had no
    host note. The turn makes its own call instead."""
    stt, provider = judging(rt, create(rt, "note.txt", "c1"), say("Shall I?"))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "write a note saying x"})
    await idle(session)
    pid = page.all("confirm.request")[0]["pending_id"]
    provider._replies += [say("Drafted, never shown."), say("Okay, I won't.")]
    await session.handle({"type": "listen.start"})
    await speak(session, room(0.2) + tone(0.6))
    stt.stream.say("no", final=False, turn="maybe")
    await until(lambda: len(provider.calls) == 3)
    await finish(session, stt, "no")
    assert await status_of(rt, pid) is Status.REJECTED
    assert len(provider.calls) == 4
    assert [m["text"] for m in page.all("reply.delta")][-1] == "Okay, I won't."
    assert any(n.startswith("[host]") and pid in n for n in notes(last_user(provider)))
    await session.close()


async def no_open_note(provider: ScriptedProvider) -> bool:
    return not [n for n in notes(last_user(provider)) if n.startswith("[host]")]


async def test_a_no_to_her_later_question_leaves_the_card(rt: Runtime) -> None:
    """The card was asked about, then the talk moved on: she ends on another question,
    and the no answers that one."""
    provider = ScriptedProvider(
        create(rt, "note.txt", "c1"),
        say("Shall I?"),
        say("Sunny. Want me to set a reminder too?"),
        say("Okay."),
    )
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "write a note saying x"})
    await idle(session)
    pid = page.all("confirm.request")[0]["pending_id"]
    await session.handle({"type": "turn.text", "text": "what's the weather"})
    await idle(session)
    await session.handle({"type": "turn.text", "text": "no"})
    await idle(session)
    assert await status_of(rt, pid) is Status.PROPOSED
    assert page.all("confirm.resolved") == []
    assert await no_open_note(provider)
    await session.close()


async def test_a_no_said_over_her_only_stops_her(rt: Runtime) -> None:
    """VOI-15c: talking over her to quiet her is not cancelling anything."""
    stt = hearing(rt)
    provider = ScriptedProvider(create(rt, "note.txt", "c1"), say("Shall I?"), say("Sorry."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "write a note saying x"})
    await idle(session)
    pid = page.all("confirm.request")[0]["pending_id"]
    await session.handle({"type": "barge"})
    await speak(session, room(0.2) + tone(0.6))
    stt.stream.say("No, no.", final=True)
    await speak(session, room(0.6))
    await idle(session)
    assert page.all("transcript.final")[-1]["text"] == "No, no."
    assert await status_of(rt, pid) is Status.PROPOSED
    assert page.all("confirm.resolved") == []
    assert await no_open_note(provider)
    await session.close()


async def test_a_failure_in_the_rule_never_loses_the_turn(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    provider = ScriptedProvider(create(rt, "note.txt", "c1"), say("Shall I?"), say("Okay."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "write a note saying x"})
    await idle(session)

    async def broken(*_a: object, **_k: object) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(rt.router, "cancel_on_no", broken)
    await session.handle({"type": "turn.text", "text": "No thanks"})
    await idle(session)
    told = last_user(provider)
    assert told.blocks[-1] == TextBlock(text="No thanks")
    assert await no_open_note(provider)
    assert page.all("reply.delta")[-1]["text"] == "Okay."
    assert "plain no not applied" in caplog.text and "No thanks" not in caplog.text
    await session.close()
