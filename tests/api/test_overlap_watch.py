"""Watching while she speaks (VOI-9): the page streams the microphone, her echo is
told apart by its words, and only words that are not hers duck her and are decided."""

from __future__ import annotations

import asyncio

import pytest

from tests.api.test_drafts import judging
from tests.api.test_listening import hearing, speak
from tests.api.test_overlap_session import (
    ANSWER,
    answered,
    asked,
    played,
    rt,
    spoken_reply,
    until,
)
from tests.api.test_session import Page, idle
from tests.api.test_session_notices import voiced
from tests.brain.fakes import say
from tests.voice.audio import room, tone
from trillion.api import session as session_module
from trillion.api.runtime import Runtime
from trillion.api.session import Session
from trillion.brain.loop import INTERRUPTED
from trillion.brain.types import TextBlock

__all__ = ["rt"]


async def watching(session: Session) -> None:
    await session.handle({"type": "overlap.watch"})
    assert session._overlap is not None and session._overlap.watching


async def test_her_echo_alone_changes_nothing(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    await played(session, page, 1)
    streams, partials = len(stt.streams), len(page.all("transcript.partial"))
    await watching(session)
    assert len(stt.streams) == streams + 1  # one connection, for the watch
    assert session.facts().listening  # nothing is delivered over the microphone
    stt.stream.say("first thing to", final=False)
    await speak(session, tone(0.3))
    stt.stream.say("First thing to know.", final=True, turn="done")
    stt.stream.say("second thing", final=False)  # the next sentence, heard early
    await speak(session, tone(0.3))
    assert not page.all("overlap.duck") and not page.all("overlap.over")
    assert not page.all("interrupted") and len(page.all("transcript.partial")) == partials
    assert session._overlap is not None and session._overlap.watching
    history = await rt.conversations.history(session.id, "local")
    assert history[-1][1].blocks == (TextBlock(text=ANSWER),)
    await session.close()


async def test_an_acknowledgement_ducks_her_then_she_carries_on(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    await played(session, page, 1)
    await watching(session)
    stt.stream.say("yeah", final=True, turn="done")
    await speak(session, tone(0.2) + room(0.1))
    await page.until("overlap.over")
    kinds = [m["type"] for m in page.got if m["type"].startswith("overlap.")]
    assert kinds == ["overlap.duck", "overlap.over"]
    assert "end" not in page.all("overlap.over")[-1]  # still watching
    assert session._overlap is not None and session._overlap.watching
    assert not page.all("interrupted") and session._listener is None
    history = await rt.conversations.history(session.id, "local")
    assert history[-1][1].blocks == (TextBlock(text=ANSWER),)
    # The acknowledgement is let go: her echo after it is judged on its own.
    stt.stream.say("second thing to know", final=False)
    await speak(session, tone(0.2))
    assert len(page.all("overlap.duck")) == 1
    await session.close()


async def test_their_words_after_her_echo_stop_her_without_the_echo(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    await played(session, page, 1)
    await watching(session)
    stt.stream.say("first thing to know", final=False)
    await speak(session, tone(0.3))
    assert not page.all("overlap.duck")
    stt.stream.say("first thing to know wait the other one", final=False)
    await speak(session, tone(0.2))
    await page.until("interrupted")
    await until(lambda: session._listener is not None)
    assert page.all("overlap.duck")
    assert page.all("interrupted")[-1]["kept"] == "First thing to know.…"
    assert page.all("mic.start")[-1]["follow_up"] is False
    # Their first words are kept, and her echo before them is not.
    assert page.all("transcript.partial")[-1]["text"] == "wait the other one"
    stt.stream.say("first thing to know wait the other one please", final=True, turn="done")
    await speak(session, room(0.1))
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == "wait the other one please"
    await idle(session)
    history = await rt.conversations.history(session.id, "local")
    assert history[-2][1].blocks[-1] == TextBlock(text="wait the other one please")
    assert history[-3][1].blocks == (TextBlock(text="First thing to know.…" + INTERRUPTED),)
    await session.close()


async def test_her_reply_ending_while_watching_becomes_the_follow_up_window(
    rt: Runtime,
) -> None:
    stt, page, session = await answered(rt)
    await played(session, page, 1, 2, 3)
    await watching(session)
    watch = session._overlap
    assert watch is not None
    streams = len(stt.streams)
    stt.stream.say("third thing", final=False)  # her echo, let go
    await speak(session, tone(0.2))
    turn = page.all("reply.sentence")[0]["turn"]
    await session.handle({"type": "playback.ended", "turn_id": turn})
    await until(lambda: session._listener is not None)
    assert session._listener is watch.listener and session._overlap is None
    assert len(stt.streams) == streams  # no new connection
    assert page.all("mic.start")[-1]["follow_up"] is True
    assert not page.all("overlap.over")
    # The follow-up: the echo is not in it, and they are heard as a turn.
    # The recognizer's turn goes on: its report repeats the echo, which stays let go.
    stt.stream.say("third thing and the fourth", final=True, turn="done")
    await speak(session, tone(0.3) + room(0.1))
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == "and the fourth"
    await session.close()


async def test_a_follow_up_window_from_a_watch_closes_when_nobody_speaks(rt: Runtime) -> None:
    _, page, session = await answered(rt)
    await watching(session)
    turn = page.all("reply.sentence")[0]["turn"]
    await session.handle({"type": "playback.ended", "turn_id": turn})
    await until(lambda: session._listener is not None)
    await speak(session, room(session_module.FOLLOW_UP_S + 0.2))
    await page.until("mic.stop")
    assert page.all("mic.stop")[-1]["reason"] == "nothing"
    assert session._listener is None
    await session.close()


async def test_watching_while_her_turn_runs_and_cutting_in(rt: Runtime) -> None:
    stt, _, held, page, session = await spoken_reply(rt, say("We leave at six."), say("Okay."))
    await watching(session)
    stt.stream.say("actually make it seven", final=False)
    await speak(session, tone(0.3))
    await page.until("interrupted")
    await until(lambda: session._listener is not None)
    assert page.all("overlap.duck")
    held.set()
    stt.stream.say("actually make it seven", final=True, turn="done")
    await speak(session, room(0.1))
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == "actually make it seven"
    await session.close()


async def test_watching_starts_before_her_first_sound(rt: Runtime) -> None:
    """Right after the user's turn, before a word of her reply: the page has held the
    microphone open, and the watch starts at once."""
    stt, provider = judging(rt, say("We leave at six."))
    voiced(rt)
    held = provider.hold(0)
    page, session = await asked(rt, stt)
    await watching(session)
    assert session._speaker is None  # nothing of hers yet
    held.set()
    await page.until("reply.sentence")
    await played(session, page, 1)
    stt.stream.say("we leave at six", final=False)  # her echo, once she speaks
    await speak(session, tone(0.2))
    assert not page.all("overlap.duck")
    await session.close()


async def test_a_reply_that_never_sounds_ends_the_watch(rt: Runtime) -> None:
    stt, provider = judging(rt, say("We leave at six."))
    voiced(rt)
    held = provider.hold(0)
    page, session = await asked(rt, stt)
    await watching(session)
    await session.handle({"type": "speech.mute", "muted": True})  # her voice off meanwhile
    await speak(session, room(0.1))
    held.set()
    await page.until("turn.complete")
    await page.until("overlap.over")
    assert page.all("overlap.over")[-1]["end"] is True
    assert session._overlap is None and not session.facts().listening
    await session.close()


async def test_a_watch_with_nothing_of_hers_heard_for_long_ends(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_module, "WATCH_HOLD_S", 0.3)
    _, page, session = await answered(rt)
    await watching(session)
    await speak(session, room(0.2))
    await asyncio.sleep(0.4)
    await speak(session, room(0.1))
    await page.until("overlap.over")
    assert page.all("overlap.over")[-1]["end"] is True and session._overlap is None
    await session.close()


async def test_a_watch_the_page_stops_sending_to_ends(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_module, "NO_AUDIO_S", 0.2)
    stt, page, session = await answered(rt)
    await watching(session)
    stream = stt.stream
    await page.until("overlap.over")
    assert page.all("overlap.over")[-1]["end"] is True
    assert session._overlap is None and stream.closed
    await session.close()


async def test_the_page_can_stop_watching(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    await watching(session)
    stream = stt.stream
    await session.handle({"type": "overlap.unwatch"})
    assert session._overlap is None and stream.closed
    assert page.all("overlap.over")[-1]["end"] is True
    await session.close()


async def test_stop_while_watching_stops_everything(rt: Runtime) -> None:
    stt, _, _, page, session = await spoken_reply(rt, say("A long answer."))
    await watching(session)
    stream = stt.stream
    await session.handle({"type": "interrupt"})
    assert session._overlap is None and stream.closed
    await page.until("interrupted")
    await idle(session)
    assert not page.all("overlap.over") and session._listener is None
    await session.close()


async def test_closing_while_watching_lets_the_listener_go(rt: Runtime) -> None:
    stt, _, _, _, session = await spoken_reply(rt, say("A long answer."))
    await watching(session)
    stream = stt.stream
    await session.close()
    assert session._overlap is None and stream.closed


async def test_a_typed_message_ends_the_watch(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    await watching(session)
    stream = stt.stream
    await session.handle({"type": "turn.text", "text": "never mind"})
    assert stream.closed and page.all("overlap.over")[-1]["end"] is True
    await idle(session)
    await session.close()


async def test_changed_settings_end_the_watch(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    await watching(session)
    stream = stt.stream
    await session.settings_changed()
    assert session._overlap is None and stream.closed
    assert page.all("overlap.over")[-1]["end"] is True
    await session.close()


async def test_a_barge_while_watching_stops_her_now(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    await played(session, page, 1)
    await watching(session)
    stt.stream.say("first thing to know", final=False)  # her echo, judged
    await speak(session, tone(0.2))
    await session.handle({"type": "barge"})
    await until(lambda: session._listener is not None)
    assert page.all("interrupted")[-1]["kept"] == "First thing to know.…"
    stt.stream.say("first thing to know hello", final=True, turn="done")
    await speak(session, room(0.1))
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == "hello"
    await session.close()


async def test_speak_while_watching_her_finished_reply_is_a_turn(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    await watching(session)
    streams = len(stt.streams)
    await session.handle({"type": "listen.start"})
    await until(lambda: session._listener is not None)
    assert len(stt.streams) == streams and session._overlap is None
    assert page.all("mic.start")[-1]["follow_up"] is False
    await session.close()


async def test_an_old_style_overlap_while_watching_decides_at_once(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    await played(session, page, 1)
    await watching(session)
    await session.handle({"type": "overlap"})
    assert session._overlap is not None and not session._overlap.watching
    stt.stream.say("okay", final=True, turn="done")
    await speak(session, tone(0.2) + room(0.1))
    await page.until("overlap.over")
    assert session._overlap is not None and session._overlap.watching
    await session.close()


async def test_no_draft_starts_while_watching(rt: Runtime) -> None:
    _, _, session = await answered(rt)
    calls = len(rt.provider.calls)  # type: ignore[union-attr]
    await watching(session)
    await session._start_draft("hello there")
    assert session._draft is None and session._tail is not None
    assert len(rt.provider.calls) == calls  # type: ignore[union-attr]
    await session.close()


async def test_watching_needs_hearing_that_judges_turns_and_a_reply(rt: Runtime) -> None:
    hearing(rt)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "overlap.watch"})
    assert session._overlap is None
    assert page.all("overlap.over")[-1]["end"] is True
    await session.close()


async def test_watching_with_nothing_to_talk_over_is_refused(rt: Runtime) -> None:
    judging(rt, say("Sure."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "overlap.watch"})
    assert session._overlap is None
    assert page.all("overlap.over")[-1]["end"] is True
    await session.close()


async def test_hearing_that_fails_while_watching_ends_the_watch(rt: Runtime) -> None:
    from trillion.voice.types import VoiceError

    stt, page, session = await answered(rt)
    await watching(session)
    stream = stt.stream

    async def broken(pcm: bytes) -> None:
        raise VoiceError("unreachable")

    stream.send = broken  # type: ignore[method-assign]
    await speak(session, tone(0.2))
    await page.until("overlap.over")
    assert page.all("overlap.over")[-1]["end"] is True
    assert session._overlap is None and not page.all("interrupted")
    await session.close()


async def test_a_failed_watch_connection_is_refused(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    stt.fail = "unreachable"
    await session.handle({"type": "overlap.watch"})
    assert session._overlap is None and page.all("overlap.over")[-1]["end"] is True
    assert not page.all("interrupted")
    await session.close()


async def test_an_acknowledgement_as_she_ends_leads_to_the_follow_up_window(
    rt: Runtime,
) -> None:
    stt, page, session = await answered(rt)
    await played(session, page, 1, 2, 3)
    await watching(session)
    watch = session._overlap
    assert watch is not None
    stt.stream.say("mm-hm", final=False)
    await speak(session, tone(0.2))
    await page.until("overlap.duck")
    turn = page.all("reply.sentence")[0]["turn"]
    await session.handle({"type": "playback.ended", "turn_id": turn})
    assert session._listener is None  # still deciding
    stt.stream.say("mm-hm", final=True, turn="done")
    await speak(session, room(0.1))
    await page.until("overlap.over")
    await until(lambda: session._listener is not None)
    assert session._listener is watch.listener and watch.listener.text() == ""
    assert page.all("mic.start")[-1]["follow_up"] is True
    history = await rt.conversations.history(session.id, "local")
    assert history[-1][1].blocks == (TextBlock(text=ANSWER),)
    await session.close()
