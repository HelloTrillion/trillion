"""Early thinking (VOI-12): a draft started on "probably done", used or dropped."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Callable
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_listening import speak
from tests.api.test_session import Gated, Page, idle
from tests.brain.fakes import say, use
from tests.voice.audio import room, tone
from tests.voice.fakes import FakeSTT
from trillion import paths
from trillion.api.runtime import ListenSettings, Runtime, open_runtime
from trillion.api.session import Session
from trillion.brain.types import ContextBlock, ProviderError, SystemBlock
from trillion.secrets.store import MemoryStore


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
        env={"TZ": "UTC"},
    )
    yield runtime
    await runtime.close()


def judging(rt: Runtime, *replies: Any) -> tuple[FakeSTT, Gated]:
    stt = FakeSTT(turns=True, word_times=True)
    rt.stt = stt
    rt.listening = ListenSettings(kind="deepgram", choice="en")
    provider = Gated(*replies)
    rt.provider = provider
    return stt, provider


async def start(rt: Runtime) -> tuple[Session, Page]:
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "listen.start"})
    await speak(session, room(0.2) + tone(0.6))
    return session, page


async def until(ok: Callable[[], bool]) -> None:
    for _ in range(400):
        if ok():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("never happened")


async def finish(session: Session, stt: FakeSTT, text: str) -> None:
    """The recognizer says the turn is over, and the next audio ends listening."""
    stt.stream.say(text, final=True, turn="done")
    await asyncio.sleep(0.01)  # read before the audio that ends listening
    await speak(session, room(0.1))
    await idle(session)


def drafts() -> list[str]:
    with closing(sqlite3.connect(paths.db_path())) as db:
        return [d for (d,) in db.execute("SELECT draft FROM turn_timings ORDER BY id")]


async def test_a_draft_that_matches_is_used_and_the_model_is_called_once(rt: Runtime) -> None:
    stt, provider = judging(rt, say("Sure, the dentist."))
    session, page = await start(rt)
    stt.stream.say("call the dentist", final=False, turn="maybe")
    await until(lambda: len(provider.calls) == 1)
    assert session._draft is not None
    assert not page.all("reply.delta") and session._history == []  # held, unsaved
    await finish(session, stt, "Call the dentist.")
    assert len(provider.calls) == 1  # the draft was the turn's first call
    assert page.all("reply.delta")[0]["text"] == "Sure, the dentist."
    kinds = [k for k, _ in await rt.conversations.history(session.id, "local")]
    assert kinds == ["user", "assistant"]
    assert session._turn == 1
    assert drafts() == ["used"]
    await session.close()


async def test_the_draft_sends_what_the_turn_would_have_sent(rt: Runtime) -> None:
    """The drafted call is marked as spoken, like any spoken message (#40)."""
    stt, provider = judging(rt, say("Sure."))
    session, _ = await start(rt)
    stt.stream.say("call the dentist", final=False, turn="maybe")
    await until(lambda: len(provider.calls) == 1)
    assert session._via == "text"  # nothing changed until the turn is real
    await finish(session, stt, "call the dentist")
    _, sent = provider.calls[0]
    saved = [m for _, m in await rt.conversations.history(session.id, "local")]
    assert sent == saved[:1]
    assert "THIS MESSAGE WAS SPOKEN" in str(sent[0].blocks[0].text)  # type: ignore[union-attr]
    await session.close()


async def test_carrying_on_drops_the_draft_and_nothing_is_saved_or_sent(rt: Runtime) -> None:
    stt, provider = judging(rt, say("Dropped."), say("The real answer."))
    session, page = await start(rt)
    stt.stream.say("I need to remember to", final=False, turn="maybe")
    await until(lambda: len(provider.calls) == 1)
    first = session._draft
    stt.stream.say("I need to remember to", final=False, turn="resumed")
    await until(lambda: session._draft is None)
    assert first is not None and first.finished
    assert not page.all("reply.delta") and session._history == []
    await finish(session, stt, "I need to remember to call the dentist")
    assert len(provider.calls) == 2
    assert [m["text"] for m in page.all("reply.delta")] == ["The real answer."]
    assert drafts() == ["dropped"]
    await session.close()


async def test_a_done_that_leads_on_drafts_and_carrying_on_drops_it(rt: Runtime) -> None:
    """The recognizer ended the turn on "and": she keeps listening, a draft starts as
    on a maybe, and the words that follow drop it and join the message."""
    stt, provider = judging(rt, say("Dropped."), say("Milk, got it."))
    session, page = await start(rt)
    stt.stream.say("I went to the shop and", final=True, turn="done")
    await until(lambda: len(provider.calls) == 1)
    assert session._listener is not None and session._draft is not None
    stt.stream.say("bought milk", final=False)
    await until(lambda: session._draft is None)
    await finish(session, stt, "bought milk")
    finals = [m["text"] for m in page.all("transcript.final")]
    assert finals == ["I went to the shop and bought milk"]
    assert [m["text"] for m in page.all("reply.delta")] == ["Milk, got it."]
    assert drafts() == ["dropped"]
    await session.close()


async def test_different_final_words_drop_the_draft(rt: Runtime) -> None:
    stt, provider = judging(rt, say("Dropped."), say("Fresh."))
    session, page = await start(rt)
    stt.stream.say("call the", final=False, turn="maybe")
    await until(lambda: len(provider.calls) == 1)
    await finish(session, stt, "call the dentist")
    assert [m["text"] for m in page.all("reply.delta")] == ["Fresh."]
    _, sent = provider.calls[1]
    assert sent[-1].blocks[-1].text == "call the dentist"  # type: ignore[union-attr]
    assert drafts() == ["dropped"]
    await session.close()


async def test_a_drafted_tool_call_runs_only_in_the_confirmed_turn(rt: Runtime) -> None:
    stt, provider = judging(rt, use("schedule_list", {}), say("Nothing set."))
    session, page = await start(rt)
    stt.stream.say("what reminders do I have", final=False, turn="maybe")
    await until(lambda: len(provider.calls) == 1)
    await asyncio.sleep(0.01)
    assert not page.all("tool.start")  # held, not run
    await finish(session, stt, "What reminders do I have?")
    starts = page.all("tool.start")
    assert [s["name"] for s in starts] == ["schedule_list"]
    assert session._turn == 1
    await session.close()


async def test_with_the_switch_off_no_draft_starts(rt: Runtime) -> None:
    stt, provider = judging(rt, say("Answer."))
    rt.think_early = False
    session, page = await start(rt)
    stt.stream.say("hello", final=False, turn="maybe")
    await asyncio.sleep(0.01)
    assert session._draft is None and provider.calls == []
    await finish(session, stt, "hello")
    assert [m["text"] for m in page.all("reply.delta")] == ["Answer."]
    assert drafts() == ["none"]
    await session.close()


async def test_a_failed_draft_is_dropped_quietly(rt: Runtime) -> None:
    stt, _provider = judging(rt, ProviderError("unreachable"), say("Recovered."))
    session, page = await start(rt)
    stt.stream.say("hello", final=False, turn="maybe")
    await until(lambda: session._draft is not None and session._draft.finished)
    await finish(session, stt, "hello")
    assert not page.all("error")
    assert [m["text"] for m in page.all("reply.delta")] == ["Recovered."]
    await session.close()


async def test_a_dropped_drafts_usage_is_recorded(rt: Runtime) -> None:
    stt, _provider = judging(rt, say("Dropped."), say("Kept."))
    session, _ = await start(rt)
    stt.stream.say("one", final=False, turn="maybe")
    await until(lambda: session._draft is not None and session._draft.finished)
    stt.stream.say("one", final=False, turn="resumed")
    await until(lambda: session._draft is None)
    await finish(session, stt, "one two")
    rows = await rt.conversations.usage_since("local", 0)
    assert [r["unpriced_turns"] for r in rows] == [2]  # the dropped draft, then the turn
    await session.close()


async def test_a_used_drafts_usage_is_recorded_once(rt: Runtime) -> None:
    stt, _provider = judging(rt, say("Sure."))
    session, _ = await start(rt)
    stt.stream.say("one", final=False, turn="maybe")
    await until(lambda: session._draft is not None and session._draft.finished)
    await finish(session, stt, "one")
    rows = await rt.conversations.usage_since("local", 0)
    assert [r["unpriced_turns"] for r in rows] == [1]
    await session.close()


async def test_stopping_while_she_listens_cancels_the_draft(rt: Runtime) -> None:
    stt, provider = judging(rt, say("Never heard."))
    provider.hold(0)
    session, page = await start(rt)
    stt.stream.say("call the dentist", final=False, turn="maybe")
    await until(lambda: provider.running == 1)
    draft = session._draft
    assert draft is not None
    await session.handle({"type": "interrupt"})
    assert session._draft is None and draft.cancelled and provider.running == 0
    assert not page.all("reply.delta") and session._history == []
    await session.close()


async def test_closing_cancels_the_draft(rt: Runtime) -> None:
    stt, provider = judging(rt, say("Never heard."))
    provider.hold(0)
    session, _ = await start(rt)
    stt.stream.say("call the dentist", final=False, turn="maybe")
    await until(lambda: provider.running == 1)
    draft = session._draft
    assert draft is not None
    await session.close()
    assert session._draft is None and draft.cancelled and provider.running == 0


async def test_a_new_maybe_replaces_the_draft(rt: Runtime) -> None:
    stt, provider = judging(rt, say("Second."))
    gate = provider.hold(0)
    session, page = await start(rt)
    stt.stream.say("call the", final=False, turn="maybe")
    await until(lambda: provider.running == 1)
    first = session._draft
    stt.stream.say("call the dentist", final=False, turn="maybe")
    await until(lambda: session._draft is not first and session._draft is not None)
    assert first is not None and first.cancelled
    gate.set()  # the second gets going; the first was stopped while held
    await finish(session, stt, "call the dentist")
    assert [m["text"] for m in page.all("reply.delta")] == ["Second."]
    assert len(provider.calls) == 1  # the held first call never got going
    await session.close()


async def test_a_draft_held_past_the_end_is_replayed_as_it_arrives(rt: Runtime) -> None:
    stt, provider = judging(rt, say("Late but used."))
    gate = provider.hold(0)
    session, page = await start(rt)
    stt.stream.say("call the dentist", final=False, turn="maybe")
    await until(lambda: provider.running == 1)
    stt.stream.say("call the dentist", final=True, turn="done")
    await speak(session, room(0.1))
    await page.until("transcript.final")
    await asyncio.sleep(0.01)
    gate.set()
    await idle(session)
    assert len(provider.calls) == 1
    assert [m["text"] for m in page.all("reply.delta")] == ["Late but used."]
    await session.close()


async def test_a_typed_turn_never_starts_a_draft(rt: Runtime) -> None:
    _stt, provider = judging(rt, say("Typed answer."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "hello"})
    await idle(session)
    assert session._draft is None and len(provider.calls) == 1
    assert drafts() == ["none"]
    await session.close()


async def test_a_model_changed_before_the_end_drops_the_draft(rt: Runtime) -> None:
    stt, old = judging(rt, say("From the old one."))
    session, page = await start(rt)
    stt.stream.say("call the dentist", final=False, turn="maybe")
    await until(lambda: session._draft is not None and session._draft.finished)
    new = Gated(say("From the new one."))
    new.name = "other"
    rt.provider = new
    await finish(session, stt, "call the dentist")
    assert len(old.calls) == 1 and len(new.calls) == 1
    assert [m["text"] for m in page.all("reply.delta")] == ["From the new one."]
    rows = await rt.conversations.usage_since("local", 0)
    # The dropped draft is the old model's spending, not the new one's.
    assert {r["provider"]: r["unpriced_turns"] for r in rows} == {"fake": 1, "other": 1}
    assert drafts() == ["dropped"]
    await session.close()


async def test_a_used_draft_shows_the_words_it_saved(rt: Runtime) -> None:
    """The screen shows what is saved, so a reload shows the same words."""
    stt, _ = judging(rt, say("Sure."))
    session, page = await start(rt)
    stt.stream.say("call the dentist", final=False, turn="maybe")
    await until(lambda: session._draft is not None)
    await finish(session, stt, "Call the dentist.")
    assert [m["text"] for m in page.all("transcript.final")] == ["call the dentist"]
    saved = [m for k, m in await rt.conversations.history(session.id, "local") if k == "user"]
    assert saved[0].blocks[-1].text == "call the dentist"  # type: ignore[union-attr]
    await session.close()


async def test_a_tap_note_before_the_turn_starts_drops_the_used_draft(rt: Runtime) -> None:
    """A tap's note joins the history after the draft was taken, so it is not replayed.

    The tap is made to land exactly then: while the words are being shown, which is
    after the draft was judged usable and before the turn starts."""
    stt, provider = judging(rt, say("Drafted."), say("Answer."))
    session, page = await start(rt)
    shown = page.send

    async def send(body: dict[str, Any]) -> None:
        await shown(body)
        if body["type"] == "transcript.final":
            session._queued.append("[host] a tap's note")

    session._raw_send = send
    stt.stream.say("call the dentist", final=False, turn="maybe")
    await until(lambda: session._draft is not None and session._draft.finished)
    draft = session._draft
    await finish(session, stt, "call the dentist")
    assert draft is not None and draft.finished
    assert len(provider.calls) == 2
    assert [m["text"] for m in page.all("reply.delta")] == ["Answer."]
    kinds = [k for k, _ in await rt.conversations.history(session.id, "local")]
    assert kinds == ["user", "tap", "assistant"]
    rows = await rt.conversations.usage_since("local", 0)
    assert [r["unpriced_turns"] for r in rows] == [2]
    assert drafts() == ["dropped"]
    await session.close()


async def test_a_system_prompt_changed_before_the_end_drops_the_draft(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rule or skill changed while the draft ran: it sent an old prompt."""
    stt, provider = judging(rt, say("Old rules."), say("New rules."))
    session, page = await start(rt)
    stt.stream.say("call the dentist", final=False, turn="maybe")
    await until(lambda: session._draft is not None and session._draft.finished)
    changed = [*rt.system(), SystemBlock(text="a new rule")]
    monkeypatch.setattr(rt, "system", lambda: changed)
    await finish(session, stt, "call the dentist")
    assert len(provider.calls) == 2
    assert [m["text"] for m in page.all("reply.delta")] == ["New rules."]
    assert drafts() == ["dropped"]
    rows = await rt.conversations.usage_since("local", 0)
    assert [r["unpriced_turns"] for r in rows] == [2]  # the dropped draft's, and the turn's
    await session.close()


async def test_a_board_change_from_the_page_drops_the_draft(rt: Runtime) -> None:
    """The draft's context told her the board as it was; the turn's tells the new one."""
    stt, provider = judging(rt, say("Drafted."), say("Answer."))
    session, page = await start(rt)
    assert rt.boards is not None
    await rt.boards.show(session.id, "October", [{"type": "stat", "label": "Total", "value": "4"}])
    await rt.boards.show(session.id, None, [{"type": "note", "text": "Milk"}], "add")
    stt.stream.say("call the dentist", final=False, turn="maybe")
    await until(lambda: session._draft is not None and session._draft.finished)
    await session.handle({"type": "board.remove", "id": "w2"})
    assert session._draft is None
    await finish(session, stt, "call the dentist")
    assert len(provider.calls) == 2
    assert [m["text"] for m in page.all("reply.delta")] == ["Answer."]
    assert drafts() == ["dropped"]
    _, sent = provider.calls[1]
    context = str(sent[-1].blocks[0].text)  # type: ignore[union-attr]
    assert 'Board (open): "October": w1 stat "Total".' in context
    assert "w2 note" not in context and "Milk" not in context
    await session.close()


async def test_a_fifth_message_sent_as_a_used_draft_carries_the_checkpoint(rt: Runtime) -> None:
    """The draft is built before the message is counted, so it asks for the next turn."""
    stt, provider = judging(rt, *(say(f"R{i}.") for i in range(5)))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    for i in range(4):
        await session.handle({"type": "turn.text", "text": f"m{i}"})
        await idle(session)
    await session.handle({"type": "listen.start"})
    await speak(session, room(0.2) + tone(0.6))
    stt.stream.say("call the dentist", final=False, turn="maybe")
    await until(lambda: len(provider.calls) == 5)
    await finish(session, stt, "call the dentist")
    assert len(provider.calls) == 5 and drafts()[-1] == "used"
    assert session._turn == 5
    sent = provider.calls[4][1][-1].blocks[0]
    assert isinstance(sent, ContextBlock) and "Snap back now" in sent.text
    fourth = provider.calls[3][1][-1].blocks[0]
    assert isinstance(fourth, ContextBlock) and "Snap back now" not in fourth.text
    await session.close()


async def test_a_host_note_never_carries_the_checkpoint(rt: Runtime) -> None:
    """The checkpoint asks about "your last line": it rides on the user's own words only."""
    _stt, _provider = judging(rt, *(say(f"R{i}.") for i in range(5)))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    for i in range(5):
        await session.handle({"type": "turn.text", "text": f"m{i}"})
        await idle(session)
    assert session._turn == 5
    note = (await session._build_input("tap", "[host] a tap's note")).msg.blocks[0]
    assert isinstance(note, ContextBlock) and "Snap back now" not in note.text
    own = (await session._build_input("user", "and another thing")).msg.blocks[0]
    assert isinstance(own, ContextBlock) and "Snap back now" in own.text
    await session.close()
