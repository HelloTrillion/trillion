"""She can hear: the microphone, the turn it makes, and what happens when it fails
(VOI-1, VOI-3, VOI-4, VOI-8, VOI-14, VOI-15a, VOI-15b)."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_session import Page, idle
from tests.brain.fakes import ScriptedProvider, say, use
from tests.voice.audio import dead, frames, room, tone
from tests.voice.fakes import FakeSTT
from trillion import paths
from trillion.api import session as session_module
from trillion.api.runtime import ListenSettings, Runtime, open_runtime
from trillion.api.session import Session
from trillion.persona.prompt import degraded
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
        env={},
    )
    yield runtime
    await runtime.close()


def hearing(rt: Runtime, *replies: Any, streaming: bool = True) -> FakeSTT:
    stt = FakeSTT(streaming=streaming)
    rt.stt = stt
    rt.listening = ListenSettings(kind="deepgram", choice="en")
    rt.provider = ScriptedProvider(*replies)
    return stt


async def speak(session: Session, pcm: bytes) -> None:
    for frame in frames(pcm):
        await session.audio(frame)
        await asyncio.sleep(0)


def orbs(page: Page) -> list[str]:
    return [m["orb"] for m in page.all("state")]


async def test_she_is_told_which_messages_were_spoken(rt: Runtime) -> None:
    """#40: a transcript is marked as one, per message, and a typed message is not."""
    stt = hearing(rt, say("Heard you."), say("Read you."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "listen.start"})
    await speak(session, room(0.2) + tone(0.6))
    stt.stream.say("can you hear me at all", final=True)
    await speak(session, room(0.6))
    await idle(session)
    await session.handle({"type": "turn.text", "text": "and now I am typing"})
    await idle(session)

    def context_of(call: int) -> str:
        message = rt.provider.calls[call][1][-1]  # type: ignore[union-attr]
        return str(message.blocks[0].text)

    assert "THIS MESSAGE WAS SPOKEN" in context_of(0)
    assert "SPOKEN" not in context_of(1)
    # The spoken one keeps its mark when replayed: history is never rewritten.
    replayed = rt.provider.calls[1][1][0]  # type: ignore[union-attr]
    assert "THIS MESSAGE WAS SPOKEN" in str(replayed.blocks[0].text)


async def test_a_spoken_message_becomes_a_turn(rt: Runtime) -> None:
    stt = hearing(rt, say("Good morning."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    assert page.all("session")[0]["listening"] == {
        "kind": "deepgram",
        "choice": "en",
        "streaming": True,
        "turns": False,
    }

    await session.handle({"type": "listen.start"})
    assert page.all("mic.start") == [{"type": "mic.start", "follow_up": False}]
    assert orbs(page)[-2:] == ["arming", "listening"] and stt.choices_asked == ["en"]

    await speak(session, room(0.2) + tone(0.6))
    stt.stream.say("good", final=False)
    stt.stream.say("good morning", final=True)
    await speak(session, room(0.6))
    await idle(session)

    assert page.all("mic.stop") == [{"type": "mic.stop", "reason": "end"}]
    assert page.all("transcript.partial")[0]["text"] == "good"
    assert page.all("transcript.final") == [{"type": "transcript.final", "text": "good morning"}]
    assert "".join(m["text"] for m in page.all("reply.delta")) == "Good morning."
    assert orbs(page)[-4:] == ["listening", "processing", "speaking", "idle"]
    assert session._turn == 1  # a spoken message advances the turn, exactly as a typed one
    kinds = [k for k, _ in await rt.conversations.history(session.id, "local")]
    assert kinds == ["user", "assistant"]
    # It ended 0.4 s after the final segment, not after the full second of quiet.
    heard_s = len(stt.stream.audio) / 32_000
    assert 0.8 + 0.4 <= heard_s < 0.8 + 0.6 and stt.stream.closed
    await session.close()


async def test_a_spoken_yes_confirms_and_is_recorded_as_spoken(rt: Runtime) -> None:
    target = rt.granted_roots[0] / "a.txt"
    stt = hearing(
        rt, use("file_create", {"path": str(target), "content": "hi"}, "c1"), say("Shall I?")
    )
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "write a"})
    await idle(session)
    card = page.all("confirm.request")[0]["pending_id"]
    assert not target.exists()

    rt.provider._replies += [  # type: ignore[union-attr]
        use("confirm_pending_action", {"pending_id": card}, "c2"),
        say("Done."),
    ]
    await session.handle({"type": "listen.start"})
    await speak(session, tone(0.5))
    stt.stream.say("yes, go ahead")
    await speak(session, room(0.6))
    await idle(session)

    assert target.read_text(encoding="utf-8") == "hi"
    with closing(sqlite3.connect(paths.db_path())) as db:
        vias = [v for (v,) in db.execute("SELECT via FROM decisions WHERE event = 'approved'")]
    assert vias == ["voice"]
    await session.close()


async def test_a_batch_recognizer_shows_she_is_working_while_it_transcribes(rt: Runtime) -> None:
    stt = hearing(rt, say("Sure."), streaming=False)
    stt.on_finish = ["turn it off"]
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "listen.start"})
    await speak(session, tone(0.5) + room(2.2))  # a batch recognizer waits two seconds
    await idle(session)
    assert page.all("transcript.partial") == []
    assert page.all("transcript.final")[0]["text"] == "turn it off"
    assert orbs(page)[-4:] == ["listening", "processing", "speaking", "idle"]
    await session.close()


async def test_a_dead_microphone_is_said_out_loud(rt: Runtime) -> None:
    hearing(rt)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "listen.start"})
    await speak(session, dead(3.0))
    assert page.all("mic.stop")[-1]["reason"] == "inaudible"
    assert page.all("error")[-1] == {
        "type": "error",
        "code": "listening",
        "kind": "inaudible",
        "sentence": degraded("hearing", "inaudible"),
    }
    assert orbs(page)[-1] == "idle" and session._task is None and session._turn == 0
    assert rt.provider.calls == []  # type: ignore[union-attr]
    await session.close()


async def test_a_microphone_that_sends_nothing_at_all_is_noticed(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_module, "NO_AUDIO_S", 0.05)
    hearing(rt)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "listen.start"})
    await page.until("error")
    assert page.all("mic.stop")[-1]["reason"] == "inaudible"
    assert page.all("error")[-1]["kind"] == "inaudible" and session._listener is None
    await session.close()


async def test_nobody_speaking_ends_quietly_and_sound_without_words_is_explained(
    rt: Runtime,
) -> None:
    stt = hearing(rt)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "listen.start"})
    await speak(session, room(8.2))
    assert page.all("mic.stop")[-1]["reason"] == "nothing"
    assert page.all("error") == [] and orbs(page)[-1] == "idle"

    await session.handle({"type": "listen.start"})
    await speak(session, tone(0.5) + room(1.2))  # a noise the recognizer finds no words in
    await idle(session)
    assert page.all("notice")[-1] == {"type": "notice", "code": "listening", "reason": "unheard"}
    assert page.all("transcript.final") == [] and session._turn == 0
    assert len(stt.streams) == 2
    await session.close()


async def test_stopping_while_she_listens_throws_the_words_away(rt: Runtime) -> None:
    stt = hearing(rt)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "listen.start"})
    await speak(session, tone(0.5))
    stt.stream.say("delete everything", final=False)
    await asyncio.sleep(0.01)
    await session.handle({"type": "interrupt"})
    assert page.all("mic.stop")[-1]["reason"] == "cancelled"
    assert stt.stream.closed and not stt.stream.finished
    await session.audio(tone(0.2))  # late audio from the page goes nowhere
    assert session._turn == 0 and rt.provider.calls == []  # type: ignore[union-attr]
    assert await rt.conversations.history(session.id, "local") == []
    await session.close()


async def test_the_user_can_end_their_own_message(rt: Runtime) -> None:
    stt = hearing(rt, say("Okay."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "listen.stop"})  # nothing to stop: ignored
    await session.handle({"type": "listen.start"})
    await session.handle({"type": "listen.start"})  # a second click changes nothing
    await speak(session, tone(0.5))
    stt.stream.say("that is all")
    await asyncio.sleep(0.01)
    await session.handle({"type": "listen.stop"})
    await idle(session)
    assert page.all("transcript.final")[0]["text"] == "that is all"
    assert len(stt.streams) == 1 and len(page.all("mic.start")) == 1
    await session.close()


async def test_a_held_key_lets_a_pause_go_by_and_ends_on_release(rt: Runtime) -> None:
    """Hold to talk: a pause to think, however long, is not the end. Letting go is."""
    stt = hearing(rt, say("Okay."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "listen.start", "held": True})
    await speak(session, room(0.2) + tone(0.6))
    stt.stream.say("I need to remember to", final=True)
    await speak(session, room(2.5))  # thinking, with the key still down
    await asyncio.sleep(0.01)
    assert page.all("mic.stop") == [] and page.all("transcript.final") == []
    await speak(session, tone(0.5))
    stt.stream.say("call the dentist", final=True)
    await asyncio.sleep(0.01)
    await session.handle({"type": "listen.stop"})  # the key comes up
    await idle(session)
    (final,) = page.all("transcript.final")
    assert "remember" in final["text"] and "dentist" in final["text"]
    await session.close()


async def test_typing_wins_over_a_microphone_left_open(rt: Runtime) -> None:
    stt = hearing(rt, say("Typed it is."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "listen.start"})
    await session.handle({"type": "turn.text", "text": "never mind, typing"})
    await idle(session)
    assert stt.stream.closed and session._listener is None
    kinds = [k for k, _ in await rt.conversations.history(session.id, "local")]
    assert kinds == ["user", "assistant"]
    await session.close()


async def test_a_recognizer_that_fails_leaves_typing_working(rt: Runtime) -> None:
    stt = hearing(rt, say("Still here."))
    stt.fail = "unauthorized"
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "listen.start"})
    assert page.all("error")[-1] == {
        "type": "error",
        "code": "listening",
        "kind": "unauthorized",
        "sentence": degraded("hearing", "unauthorized"),
    }
    assert page.all("mic.stop")[-1]["reason"] == "failed" and orbs(page)[-1] == "idle"
    await session.handle({"type": "turn.text", "text": "hello"})
    await idle(session)
    assert "".join(m["text"] for m in page.all("reply.delta")) == "Still here."
    await session.close()


async def test_listening_needs_a_recognizer_a_model_and_a_free_moment(rt: Runtime) -> None:
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    assert page.all("session")[0]["listening"] is None
    await session.handle({"type": "listen.start"})
    assert page.all("error")[-1]["code"] == "no_listening"

    hearing(rt)
    rt.provider = None
    await session.handle({"type": "listen.start"})
    assert page.all("error")[-1]["code"] == "no_model"
    assert page.all("mic.start") == []
    await session.close()


async def test_the_microphone_reopens_after_she_is_heard_and_only_then(rt: Runtime) -> None:
    stt = hearing(rt, say("Morning."), say("Typed."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)

    # After a typed turn nothing reopens: she was not being spoken to.
    await session.handle({"type": "turn.text", "text": "hello"})
    await idle(session)
    await session.handle({"type": "playback.ended", "turn_id": 1, "played_ms": 900})
    assert page.all("mic.start") == []

    await session.handle({"type": "listen.start"})
    await speak(session, tone(0.5))
    stt.stream.say("good morning")
    await speak(session, room(0.6))
    await idle(session)
    # The reply being generated proves nothing; her having been heard does (VOI-15b).
    assert len(page.all("mic.start")) == 1
    await session.handle({"type": "playback.ended", "turn_id": 2, "played_ms": 900})
    assert page.all("mic.start")[-1] == {"type": "mic.start", "follow_up": True}
    assert orbs(page)[-1] == "listening"

    # Three seconds without a word and it closes again, without complaint (VOI-8).
    await speak(session, room(3.2))
    assert page.all("mic.stop")[-1]["reason"] == "nothing" and page.all("error") == []
    assert orbs(page)[-1] == "idle" and len(stt.streams) == 2
    await session.close()


async def test_closing_the_tab_closes_the_microphone(rt: Runtime) -> None:
    stt = hearing(rt)
    session = await Session.start(rt, Page(rt).send, None)
    await session.handle({"type": "listen.start"})
    await session.close()
    assert stt.stream.closed and session._listener is None
    await session.audio(tone(0.1))
