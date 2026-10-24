"""An interrupted spoken reply is kept up to what was heard (VOI-9, D8)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_drafts import judging
from tests.api.test_listening import hearing, speak
from tests.api.test_session import Page, idle
from tests.api.test_session_notices import voiced
from tests.brain.fakes import ScriptedProvider, is_naming, say, use
from tests.voice.audio import room, tone
from tests.voice.test_speaker import FakeVoice
from trillion.api.runtime import Runtime, SpeechSettings, open_runtime
from trillion.api.session import Session
from trillion.brain.loop import INTERRUPTED
from trillion.brain.types import ModelEvent, Msg, TextBlock, TextDelta
from trillion.secrets.store import MemoryStore

REPLY = "First thing. Second thing. And a third"


@pytest.fixture
async def rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    granted = tmp_path / "granted"
    granted.mkdir()
    runtime = await open_runtime(
        granted_roots=(granted,), persona=tmp_path, secrets=MemoryStore(), env={"TZ": "UTC"}
    )
    yield runtime
    await runtime.close()


class Halting(ScriptedProvider):
    """Plays its scripted rounds, then writes `text` and never finishes."""

    def __init__(self, *rounds: list[ModelEvent], text: str = REPLY) -> None:
        super().__init__(*rounds)
        self._text = text

    async def stream(self, **kw: Any) -> AsyncIterator[ModelEvent]:
        if is_naming(kw["system"]):  # naming is not a turn
            async for ev in super().stream(**kw):
                yield ev
            return
        if self._replies:
            async for ev in super().stream(**kw):
                yield ev
            return
        yield TextDelta(text=self._text)
        await asyncio.sleep(30)


async def asked_by_voice(rt: Runtime, page: Page, session: Session) -> None:
    await session.handle({"type": "listen.start"})
    await speak(session, room(0.2) + tone(0.6))
    assert rt.stt is not None
    rt.stt.stream.say("when should we leave", final=True)  # type: ignore[attr-defined]
    await speak(session, room(0.6))


async def last_said(rt: Runtime, session: Session) -> list[Msg]:
    history = await rt.conversations.history(session.id, "local")
    return [m for kind, m in history if kind == "assistant"]


async def test_a_spoken_reply_cut_off_is_kept_up_to_what_was_heard(rt: Runtime) -> None:
    hearing(rt)
    voiced(rt)
    rt.provider = Halting()
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await asked_by_voice(rt, page, session)
    await page.until("reply.sentence", 2)
    turn = page.all("reply.sentence")[0]["turn"]

    await session.handle({"type": "playback.sentence", "turn_id": turn, "seq": 1})
    # A report for another turn's sound, or a malformed one, says nothing about this one.
    await session.handle({"type": "playback.sentence", "turn_id": turn + 99, "seq": 2})
    await session.handle({"type": "playback.sentence", "turn_id": turn, "seq": True})
    await session.handle({"type": "barge"})

    (stopped,) = page.all("interrupted")
    assert stopped["kept"] == "First thing.…"
    said = await last_said(rt, session)
    assert said[-1].blocks == (TextBlock(text="First thing.…" + INTERRUPTED),)
    assert not page.all("error")
    await session.close()


async def test_a_spoken_reply_cut_off_before_a_sentence_was_heard_keeps_only_the_mark(
    rt: Runtime,
) -> None:
    hearing(rt)
    voiced(rt)
    rt.provider = Halting()
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await asked_by_voice(rt, page, session)
    await page.until("reply.sentence", 2)
    await session.handle({"type": "barge"})

    assert page.all("interrupted")[-1]["kept"] == ""
    said = await last_said(rt, session)
    assert said[-1].blocks == (TextBlock(text=INTERRUPTED.strip()),)
    await session.close()


async def test_a_typed_reply_cut_off_keeps_everything_she_wrote(rt: Runtime) -> None:
    hearing(rt)
    voiced(rt)
    rt.provider = Halting()
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "when should we leave"})
    await page.until("reply.sentence", 2)
    turn = page.all("reply.sentence")[0]["turn"]
    await session.handle({"type": "playback.sentence", "turn_id": turn, "seq": 1})
    await session.handle({"type": "interrupt"})
    await idle(session)

    assert "kept" not in page.all("interrupted")[-1]  # read, not heard: the bubble stays
    said = await last_said(rt, session)
    assert said[-1].blocks == (TextBlock(text=REPLY + INTERRUPTED),)
    await session.close()


async def test_a_spoken_reply_with_her_voice_muted_keeps_everything_she_wrote(
    rt: Runtime,
) -> None:
    hearing(rt)
    voiced(rt)
    rt.provider = Halting()
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "speech.mute", "muted": True})
    await asked_by_voice(rt, page, session)
    await page.until("reply.delta")
    await session.handle({"type": "barge"})

    assert "kept" not in page.all("interrupted")[-1]
    said = await last_said(rt, session)
    assert said[-1].blocks == (TextBlock(text=REPLY + INTERRUPTED),)
    await session.close()


async def test_only_the_round_being_heard_is_cut(rt: Runtime) -> None:
    hearing(rt)
    voiced(rt)
    look = use("file_list", {"path": str(rt.granted_roots[0])}, text="Let me look. ")
    rt.provider = Halting(look, text="I found the train times. It leaves at four. And")
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await asked_by_voice(rt, page, session)
    await page.until("reply.sentence", 3)
    turn = page.all("reply.sentence")[0]["turn"]
    for seq in (1, 2):
        await session.handle({"type": "playback.sentence", "turn_id": turn, "seq": seq})
    await session.handle({"type": "barge"})

    assert page.all("interrupted")[-1]["kept"] == "I found the train times.…"
    said = await last_said(rt, session)
    # The round before the tool is saved as it was; only the one being heard is cut.
    assert said[-2].blocks[0] == TextBlock(text="Let me look. ")
    assert said[-1].blocks == (TextBlock(text="I found the train times.…" + INTERRUPTED),)
    await session.close()


async def test_cut_off_after_a_tool_before_she_writes_again_nothing_is_cut(
    rt: Runtime,
) -> None:
    """The round she was heard in was saved whole before the tool, and the page's bubble
    for it stays as it is: no new text was written, so nothing is kept or trimmed."""
    hearing(rt)
    voiced(rt)
    gate = asyncio.Event()
    look = use("file_list", {"path": str(rt.granted_roots[0])}, text="Let me look. ")

    class Slow(Halting):
        async def stream(self, **kw: Any) -> AsyncIterator[ModelEvent]:
            if not self._replies:
                await gate.wait()
            async for ev in super().stream(**kw):
                yield ev

    rt.provider = Slow(look)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await asked_by_voice(rt, page, session)
    await page.until("tool.end")
    turn = page.all("reply.sentence")[0]["turn"]
    await session.handle({"type": "playback.sentence", "turn_id": turn, "seq": 1})
    await session.handle({"type": "barge"})

    assert "kept" not in page.all("interrupted")[-1]
    said = await last_said(rt, session)
    assert said[-1].blocks[0] == TextBlock(text="Let me look. ")
    gate.set()
    await session.close()


async def test_a_reply_whose_voice_was_muted_midway_keeps_everything_she_wrote(
    rt: Runtime,
) -> None:
    hearing(rt)
    voiced(rt)
    rt.provider = Halting()
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await asked_by_voice(rt, page, session)
    await page.until("reply.sentence", 2)
    turn = page.all("reply.sentence")[0]["turn"]
    await session.handle({"type": "playback.sentence", "turn_id": turn, "seq": 1})
    await session.handle({"type": "speech.mute", "muted": True})  # from here she is read
    await session.handle({"type": "barge"})

    assert "kept" not in page.all("interrupted")[-1]
    said = await last_said(rt, session)
    assert said[-1].blocks == (TextBlock(text=REPLY + INTERRUPTED),)
    await session.close()


async def test_a_reply_whose_voice_failed_keeps_everything_she_wrote(rt: Runtime) -> None:
    hearing(rt)
    rt.tts, rt.speech = FakeVoice(fail_on="First"), SpeechSettings(kind="elevenlabs", voice="v1")
    rt.provider = Halting()
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await asked_by_voice(rt, page, session)
    await page.until("error")  # her voice is down: the rest is read on screen
    turn = page.all("reply.sentence")[0]["turn"]
    await session.handle({"type": "playback.sentence", "turn_id": turn, "seq": 1})
    await session.handle({"type": "barge"})

    assert "kept" not in page.all("interrupted")[-1]
    said = await last_said(rt, session)
    assert said[-1].blocks == (TextBlock(text=REPLY + INTERRUPTED),)
    await session.close()


async def test_a_barge_while_her_last_words_are_still_being_voiced_keeps_the_usage(
    rt: Runtime,
) -> None:
    hearing(rt)
    rt.tts, rt.speech = FakeVoice(slow=True), SpeechSettings(kind="elevenlabs", voice="v1")
    rt.provider = ScriptedProvider(say("First thing. Second thing."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await asked_by_voice(rt, page, session)
    await page.until("reply.sentence", 2)
    await asyncio.sleep(0.05)  # written and saved; the voice is still being made
    assert session._task is not None
    await session.handle({"type": "barge"})

    (row,) = await rt.conversations.usage_since("local", 0)
    assert (row["input_tokens"], row["output_tokens"]) == (10, 5)
    await session.close()


# ── A barge after she has written it all (D8) ────────────────────────────

ANSWER = "First thing to know. Second thing to know. Third thing to know."


async def answered(rt: Runtime, page: Page, *, slow: bool = False) -> Session:
    """A spoken question, answered in full; with `slow`, her voice is still being made."""
    hearing(rt)
    rt.tts, rt.speech = FakeVoice(slow=slow), SpeechSettings(kind="elevenlabs", voice="v1")
    rt.provider = ScriptedProvider(say(ANSWER))
    session = await Session.start(rt, page.send, None)
    await asked_by_voice(rt, page, session)
    await page.until("reply.sentence", 3)
    if slow:
        await asyncio.sleep(0.05)  # written and saved; the voice is still being made
        assert session._task is not None
    else:
        await page.until("turn.complete")
        await idle(session)
    return session


async def played(session: Session, page: Page, *seqs: int) -> None:
    turn = page.all("reply.sentence")[0]["turn"]
    for seq in seqs:
        await session.handle({"type": "playback.sentence", "turn_id": turn, "seq": seq})


async def test_a_barge_while_her_last_words_are_voiced_keeps_what_was_heard(
    rt: Runtime,
) -> None:
    page = Page(rt)
    session = await answered(rt, page, slow=True)
    await played(session, page, 1)
    await session.handle({"type": "barge"})

    (stopped,) = page.all("interrupted")
    assert stopped["kept"] == "First thing to know.…"
    said = await last_said(rt, session)
    assert said[-1].blocks == (TextBlock(text="First thing to know.…" + INTERRUPTED),)
    assert session._history[-1].blocks == said[-1].blocks
    await session.close()


async def test_a_barge_after_the_turn_while_she_still_plays_keeps_what_was_heard(
    rt: Runtime,
) -> None:
    page = Page(rt)
    session = await answered(rt, page)
    await played(session, page, 1, 2)
    await session.handle({"type": "barge"})

    assert page.all("interrupted")[-1]["kept"] == "First thing to know. Second thing to know.…"
    expected = "First thing to know. Second thing to know.…" + INTERRUPTED
    said = await last_said(rt, session)
    assert said[-1].blocks == (TextBlock(text=expected),)
    assert said[-1].raw is None  # no longer what the provider returned
    assert session._history[-1].blocks == said[-1].blocks
    assert page.all("mic.start")  # and she listens
    await session.close()

    # A reload shows what was kept.
    again = Page(rt)
    resumed = await Session.start(rt, again.send, session.id)
    assert again.all("history")[0]["messages"][-1]["text"] == expected
    await resumed.close()


async def test_a_stop_after_the_turn_while_she_still_plays_keeps_what_was_heard(
    rt: Runtime,
) -> None:
    page = Page(rt)
    session = await answered(rt, page)
    await played(session, page, 1)
    await session.handle({"type": "interrupt"})

    assert page.all("interrupted")[-1]["kept"] == "First thing to know.…"
    said = await last_said(rt, session)
    assert said[-1].blocks == (TextBlock(text="First thing to know.…" + INTERRUPTED),)
    await session.close()


async def test_a_late_barge_before_anything_was_heard_keeps_only_the_mark(
    rt: Runtime,
) -> None:
    page = Page(rt)
    session = await answered(rt, page)
    await session.handle({"type": "barge"})

    assert page.all("interrupted")[-1]["kept"] == ""
    said = await last_said(rt, session)
    assert said[-1].blocks == (TextBlock(text=INTERRUPTED.strip()),)
    await session.close()


async def test_a_barge_during_her_last_sentence_still_marks_it(rt: Runtime) -> None:
    """Every sentence has started, but the last is still playing: she was cut off."""
    page = Page(rt)
    session = await answered(rt, page)
    await played(session, page, 1, 2, 3)
    await session.handle({"type": "barge"})

    assert page.all("interrupted")[-1]["kept"] == ANSWER + "…"
    said = await last_said(rt, session)
    assert said[-1].blocks == (TextBlock(text=ANSWER + "…" + INTERRUPTED),)
    await session.close()


async def test_once_she_has_been_heard_to_the_end_nothing_is_cut(rt: Runtime) -> None:
    page = Page(rt)
    session = await answered(rt, page)
    await played(session, page, 1)
    turn = page.all("reply.sentence")[0]["turn"]
    await session.handle({"type": "playback.ended", "turn_id": turn})
    await session.handle({"type": "barge"})

    assert not page.all("interrupted")
    said = await last_said(rt, session)
    assert said[-1].blocks == (TextBlock(text=ANSWER),)
    await session.close()


async def test_a_late_barge_changes_nothing_once_something_was_saved_after_her_reply(
    rt: Runtime,
) -> None:
    page = Page(rt)
    session = await answered(rt, page)
    await played(session, page, 1)
    # A notice arrives after it: the reply is no longer the conversation's last word.
    await session._save_all("assistant", [Msg(role="assistant", blocks=(TextBlock(text="Hi."),))])
    await session.handle({"type": "barge"})

    assert not page.all("interrupted")
    said = await last_said(rt, session)
    assert said[-2].blocks == (TextBlock(text=ANSWER),)
    await session.close()


async def test_the_store_only_amends_a_conversations_last_message(rt: Runtime) -> None:
    store = rt.conversations
    await store.create("c1", "local", 1)
    await store.create("c2", "local", 1)
    first = await store.append("c1", "local", "assistant", Msg(role="assistant", blocks=()), 1)
    last = await store.append("c1", "local", "assistant", Msg(role="assistant", blocks=()), 2)
    await store.append("c2", "local", "assistant", Msg(role="assistant", blocks=()), 3)
    short = Msg(role="assistant", blocks=(TextBlock(text="cut"),))

    assert not await store.amend_tail("c1", "local", first, short)
    assert not await store.amend_tail("c2", "local", last, short)  # not that conversation's
    assert await store.amend_tail("c1", "local", last, short)
    assert [m.blocks for _, m in await store.history("c1", "local")] == [(), short.blocks]


async def test_a_barge_while_the_turn_is_still_being_counted_keeps_what_was_heard(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Talked over between saving her reply and recording what it cost."""
    counting, counted = asyncio.Event(), asyncio.Event()
    record = rt.conversations.record_usage

    async def slow_record(**kw: Any) -> None:
        counting.set()
        await counted.wait()
        await record(**kw)

    monkeypatch.setattr(rt.conversations, "record_usage", slow_record)
    hearing(rt)
    voiced(rt)
    rt.provider = ScriptedProvider(say(ANSWER))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await asked_by_voice(rt, page, session)
    await asyncio.wait_for(counting.wait(), 5)
    await played(session, page, 1)
    await session.handle({"type": "barge"})

    assert page.all("interrupted")[-1]["kept"] == "First thing to know.…"
    said = await last_said(rt, session)
    assert said[-1].blocks == (TextBlock(text="First thing to know.…" + INTERRUPTED),)
    counted.set()
    await asyncio.sleep(0.05)
    assert await rt.conversations.usage_since("local", 0)  # still recorded
    await session.close()


@pytest.mark.parametrize("over", ["barge", "interrupt"])
async def test_a_reply_a_draft_has_read_is_never_trimmed(rt: Runtime, over: str) -> None:
    """A draft sends the model her reply: from then on it stays as it was."""
    stt, provider = judging(rt, say(ANSWER), say("Of course."))
    voiced(rt)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "listen.start"})
    await speak(session, room(0.2) + tone(0.6))
    stt.stream.say("when should we leave", final=True, turn="done")
    await speak(session, room(0.1))
    await page.until("turn.complete")
    await idle(session)
    await played(session, page, 1)

    # The user starts again while she still plays, and sounds done: a draft starts.
    await session.handle({"type": "listen.start"})
    await speak(session, room(0.2) + tone(0.6))
    stt.stream.say("and after that", final=False, turn="maybe")
    for _ in range(400):
        if len(provider.calls) == 2:
            break
        await asyncio.sleep(0.005)
    assert len(provider.calls) == 2 and ANSWER in str(provider.calls[1][1])
    await session.handle({"type": over})

    assert all("kept" not in m for m in page.all("interrupted"))
    said = await last_said(rt, session)
    assert said[-1].blocks == (TextBlock(text=ANSWER),)
    await session.close()


async def test_a_draft_that_never_starts_leaves_her_reply_trimmable(rt: Runtime) -> None:
    """A history due a summary starts no draft, so nothing read her reply: it is still
    cut to what was heard."""
    stt, provider = judging(rt, say(ANSWER))
    voiced(rt)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "listen.start"})
    await speak(session, room(0.2) + tone(0.6))
    stt.stream.say("when should we leave", final=True, turn="done")
    await speak(session, room(0.1))
    await page.until("turn.complete")
    await idle(session)
    await played(session, page, 1)

    rt.compact_at = 1  # any history is due a summary now
    await session.handle({"type": "listen.start"})
    await speak(session, room(0.2) + tone(0.6))
    stt.stream.say("and after that", final=False, turn="maybe")
    await asyncio.sleep(0.05)
    assert session._draft is None and len(provider.calls) == 1
    await session.handle({"type": "barge"})

    assert page.all("interrupted")[-1]["kept"] == "First thing to know.…"
    said = await last_said(rt, session)
    assert said[-1].blocks == (TextBlock(text="First thing to know.…" + INTERRUPTED),)
    await session.close()
