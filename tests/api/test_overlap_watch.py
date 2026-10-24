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
from tests.voice.fakes import FakeSTT
from trillion.api import session as session_module
from trillion.api.runtime import Runtime
from trillion.api.session import Session
from trillion.brain.loop import INTERRUPTED
from trillion.brain.types import TextBlock

__all__ = ["rt"]


@pytest.fixture(autouse=True)
def short_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_module, "ECHO_GRACE_S", 0.2)


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
    stt.stream.say("yeah sure", final=True, turn="done")
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
    await ended(session, page)
    # The recognizer's turn goes on: its report repeats the echo, which stays let go.
    stt.stream.say("third thing and the fourth", final=True, turn="done")
    await speak(session, tone(0.3) + room(0.1))
    await page.until("transcript.final", 2)
    assert watch.promoting and session._overlap is None
    assert len(stt.streams) == streams  # no new connection
    assert page.all("mic.start")[-1]["follow_up"] is True
    assert not page.all("overlap.over")
    assert page.all("transcript.final")[-1]["text"] == "and the fourth"
    await session.close()


async def test_nobody_speaking_after_her_end_opens_the_window_after_the_grace(
    rt: Runtime,
) -> None:
    _, page, session = await answered(rt)
    await watching(session)
    await ended(session, page)
    assert session._listener is None  # her echo may still come back
    await until(lambda: session._listener is not None)
    assert page.all("mic.start")[-1]["follow_up"] is True
    await session.close()


# ── Her echo after she ends is never the user's turn (the talking-to-herself loop) ──


async def ended(session: Session, page: Page) -> None:
    turn = page.all("reply.sentence")[0]["turn"]
    await session.handle({"type": "playback.ended", "turn_id": turn})


def calls(rt: Runtime) -> int:
    return len(rt.provider.calls)  # type: ignore[union-attr]


async def test_her_last_sentence_reported_after_she_ends_is_not_a_turn(rt: Runtime) -> None:
    """Live: her last sentence came back as the next turn, and she answered herself."""
    stt, page, session = await answered(rt)
    await played(session, page, 1, 2, 3)
    await watching(session)
    before = calls(rt)
    await ended(session, page)
    await speak(session, tone(0.3))
    # The recognizer reports her echo late, as its own turn, probably finished first.
    stt.stream.say("Third thing to know.", final=False, turn="maybe")
    await speak(session, tone(0.2))
    stt.stream.say("Third thing to know.", final=True, turn="done")
    await speak(session, room(0.2))
    await until(lambda: session._listener is not None)  # after the grace, echo let go
    await speak(session, room(session_module.FOLLOW_UP_S + 0.2))
    await page.until("mic.stop", 2)
    assert page.all("mic.stop")[-1]["reason"] == "nothing"
    await idle(session)
    assert len(page.all("transcript.final")) == 1 and session._turn == 1
    assert calls(rt) == before and session._draft is None
    await session.close()


async def test_a_real_follow_up_after_she_ends_is_heard_at_once(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    await played(session, page, 1, 2, 3)
    await watching(session)
    await ended(session, page)
    stt.stream.say("what about tomorrow", final=False)
    await speak(session, tone(0.2))
    await until(lambda: session._listener is not None)
    assert page.all("mic.start")[-1]["follow_up"] is True
    stt.stream.say("what about tomorrow", final=True, turn="done")
    await speak(session, room(0.1))
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == "what about tomorrow"
    assert session._turn == 2
    await session.close()


async def test_her_tail_then_their_words_in_one_turn_keeps_only_theirs(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    await played(session, page, 1, 2, 3)
    await watching(session)
    await ended(session, page)
    stt.stream.say("third thing to know", final=False)
    await speak(session, tone(0.2))
    assert session._listener is None
    stt.stream.say("third thing to know what about tomorrow", final=False)
    await speak(session, tone(0.2))
    await until(lambda: session._listener is not None)
    stt.stream.say("third thing to know what about tomorrow", final=True, turn="done")
    await speak(session, room(0.1))
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == "what about tomorrow"
    await session.close()


async def test_an_echo_of_her_first_sentence_before_it_is_reported_played(rt: Runtime) -> None:
    stt, provider = judging(rt, say(ANSWER))
    voiced(rt)
    held = provider.hold(0)
    page, session = await asked(rt, stt)
    await watching(session)
    held.set()
    await page.until("reply.sentence")
    # No playback report yet: what was sent to be played still counts as hers.
    stt.stream.say("first thing to know", final=False)
    await speak(session, tone(0.2))
    assert not page.all("overlap.duck") and not page.all("interrupted")
    await session.close()


async def test_a_spoken_turn_that_is_her_echo_is_dropped(rt: Runtime) -> None:
    """The safety net, for a window opened without a watch: her last words heard just
    after she ended are not answered, and no draft starts from them."""
    stt, page, session = await answered(rt)
    await played(session, page, 1, 2, 3)
    before = calls(rt)
    await ended(session, page)  # no watch: a fresh follow-up window
    await until(lambda: session._listener is not None)
    await speak(session, tone(0.3))
    stt.stream.say("Third thing to know.", final=False, turn="maybe")
    await speak(session, tone(0.1))
    stt.stream.say("Third thing to know.", final=True, turn="done")
    await speak(session, room(0.2))
    await page.until("mic.stop")
    await idle(session)
    assert len(page.all("transcript.final")) == 1 and session._turn == 1
    assert calls(rt) == before and session._draft is None
    assert [m for m in page.all("notice") if m.get("reason") == "unheard"]
    await session.close()


async def test_their_own_words_just_after_she_ends_are_answered(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    await played(session, page, 1, 2, 3)
    await ended(session, page)
    await until(lambda: session._listener is not None)
    await speak(session, tone(0.3))
    stt.stream.say("Tell me the fourth.", final=True, turn="done")
    await speak(session, room(0.2))
    await page.until("transcript.final", 2)
    assert session._turn == 2
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
    stt.stream.say("mm-hm yeah", final=False)
    await speak(session, tone(0.2))
    await page.until("overlap.duck")
    turn = page.all("reply.sentence")[0]["turn"]
    await session.handle({"type": "playback.ended", "turn_id": turn})
    assert session._listener is None  # still deciding
    stt.stream.say("mm-hm yeah", final=True, turn="done")
    await speak(session, room(0.1))
    await page.until("overlap.over")
    await until(lambda: session._listener is not None)
    assert session._listener is watch.listener and watch.listener.text() == ""
    assert page.all("mic.start")[-1]["follow_up"] is True
    history = await rt.conversations.history(session.id, "local")
    assert history[-1][1].blocks == (TextBlock(text=ANSWER),)
    await session.close()


# ── Review of VOI-9 ──────────────────────────────────────────────────────


async def test_a_growing_report_with_one_misheard_word_is_still_her(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    await played(session, page, 1)
    await watching(session)
    for words in ("first", "first thang", "first thang to", "first thang to know", "second"):
        stt.stream.say(words, final=False)
        await speak(session, tone(0.1))
    assert not page.all("overlap.duck") and not page.all("interrupted")
    await session.close()


async def test_new_settings_end_a_watch_that_is_deciding(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    await played(session, page, 1)
    await watching(session)
    stt.stream.say("what about", final=False)
    await speak(session, tone(0.2))
    await page.until("overlap.duck")
    await session.settings_changed()
    assert session._overlap is None and page.all("overlap.over")[-1]["end"] is True
    await session.close()


async def test_a_cut_in_while_their_words_are_saved_waits_for_them(rt: Runtime) -> None:
    """Going on talking right after the turn: their first words are saved first, then
    what follows stops her and is the next turn."""
    stt, provider = judging(rt, say("We leave at six."), say("The budget, then."))
    voiced(rt)
    held = provider.hold(0)
    stt.delay = 0.3  # the recognizer takes a moment to hand over the last words
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "listen.start"})
    await speak(session, room(0.2) + tone(0.6))
    stt.stream.say("tell me the plan", final=True, turn="done")
    await speak(session, room(0.1))
    await until(lambda: session._task is not None)
    await watching(session)
    stt.stream.say("and the budget", final=False)
    await speak(session, tone(0.2))
    assert not page.all("interrupted") and session._collecting
    await page.until("transcript.final")
    assert page.all("transcript.final")[0]["text"] == "tell me the plan"
    await speak(session, tone(0.1))
    await page.until("overlap.duck")
    stt.stream.say("and the budget for next week", final=False)
    await speak(session, tone(0.1))
    await page.until("interrupted")
    await until(lambda: session._listener is not None)
    held.set()
    stt.stream.say("and the budget for next week", final=True, turn="done")
    await speak(session, room(0.1))
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == "and the budget for next week"
    await idle(session)  # saved by now
    history = await rt.conversations.history(session.id, "local")
    said = [m.blocks[-1] for k, m in history if k == "user"]
    assert said == [
        TextBlock(text="tell me the plan"),
        TextBlock(text="and the budget for next week"),
    ]
    await session.close()


async def test_no_watch_while_her_voice_is_off(rt: Runtime) -> None:
    stt, provider = judging(rt, say("We leave at six."))
    voiced(rt)
    held = provider.hold(0)
    page, session = await asked(rt, stt)
    await session.handle({"type": "speech.mute", "muted": True})
    await session.handle({"type": "overlap.watch"})
    assert session._overlap is None and page.all("overlap.over")[-1]["end"] is True
    held.set()
    await session.close()


# ── Duck, then confirm ───────────────────────────────────────────────────

ALRIGHT = "Alright, properly this time. We start with the basics. Then the rest."


async def alright(rt: Runtime) -> tuple[FakeSTT, Page, Session]:
    stt, _ = judging(rt, say(ALRIGHT), say("Of course."))
    voiced(rt)
    page, session = await asked(rt, stt)
    await page.until("turn.complete")
    await idle(session)
    await played(session, page, 1)
    await watching(session)
    return stt, page, session


async def test_her_echo_misheard_ducks_her_but_does_not_stop_her(rt: Runtime) -> None:
    """Live: "Alright, properly this time." came back as "or writes properly this"."""
    stt, page, session = await alright(rt)
    stt.stream.say("or writes properly this", final=False)
    await speak(session, tone(0.2))
    await page.until("overlap.duck")
    await speak(session, tone(0.2))
    await page.until("overlap.over")
    await speak(session, tone(0.2))
    assert not page.all("interrupted") and session._listener is None and session._turn == 1
    assert session._overlap is not None and session._overlap.watching
    history = await rt.conversations.history(session.id, "local")
    assert history[-1][1].blocks == (TextBlock(text=ALRIGHT),)
    await session.close()


async def test_their_words_after_the_duck_stop_her_and_all_are_kept(rt: Runtime) -> None:
    stt, page, session = await alright(rt)
    stt.stream.say("what about", final=False)
    await speak(session, tone(0.2))
    await page.until("overlap.duck")
    assert not page.all("interrupted")
    stt.stream.say("what about the bus instead", final=False)
    await speak(session, tone(0.2))
    await page.until("interrupted")
    await until(lambda: session._listener is not None)
    stt.stream.say("what about the bus instead", final=True, turn="done")
    await speak(session, room(0.1))
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == "what about the bus instead"
    await session.close()


async def test_a_stop_word_not_hers_stops_her_at_once(rt: Runtime) -> None:
    stt, page, session = await alright(rt)
    stt.stream.say("wait", final=False)
    await speak(session, tone(0.1))
    await page.until("interrupted")
    await session.close()


async def test_a_stop_word_of_hers_does_not_stop_her(rt: Runtime) -> None:
    stt, _ = judging(rt, say("No, the train leaves at six. Then a bus."), say("Sure."))
    voiced(rt)
    page, session = await asked(rt, stt)
    await page.until("turn.complete")
    await idle(session)
    await played(session, page, 1)
    await watching(session)
    stt.stream.say("no the trained", final=False)
    await speak(session, tone(0.2))
    await speak(session, tone(1.2))
    assert not page.all("interrupted") and session._turn == 1
    await session.close()


# ── Re-review: short replies right after she ends are theirs (N1, N2) ────

LEAVE = "We could take the bus. Or would you rather leave at six?"


async def leave(rt: Runtime, *, watch: bool = True) -> tuple[FakeSTT, Page, Session]:
    stt, _ = judging(rt, say(LEAVE), say("Six it is."))
    voiced(rt)
    page, session = await asked(rt, stt)
    await page.until("turn.complete")
    await idle(session)
    await played(session, page, 1, 2)
    if watch:
        await watching(session)
    await ended(session, page)
    return stt, page, session


async def reply(session: Session, stt: FakeSTT, page: Page, words: str) -> None:
    """A person answering after a moment; the recognizer reports it a little later. The
    clock moves as the audio does."""
    await speak(session, room(0.5))
    await asyncio.sleep(0.5)
    await speak(session, tone(0.3))
    await asyncio.sleep(0.5)  # the recognizer's lag
    stt.stream.say(words, final=True, turn="done")
    await speak(session, room(0.2))


@pytest.mark.parametrize("words", ["Yes.", "Six?", "Thursday."])
async def test_a_one_word_reply_right_after_she_ends_is_a_turn(rt: Runtime, words: str) -> None:
    stt, page, session = await leave(rt)
    await reply(session, stt, page, words)
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == words and session._turn == 2
    await session.close()


@pytest.mark.parametrize("words", ["Six?", "No, leave at six instead"])
async def test_replies_reusing_her_words_are_never_dropped(rt: Runtime, words: str) -> None:
    stt, page, session = await leave(rt, watch=False)
    await reply(session, stt, page, words)
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == words
    await session.close()


async def test_her_last_two_words_come_back_at_once_are_not_a_turn(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live loop: "Standing by." came back as the user's turn, and she answered it.
    As live: her echo was in the air before her end, and is reported 0.7 s after it."""
    monkeypatch.setattr(session_module, "ECHO_GRACE_S", 1.0)
    stt, _ = judging(rt, say("Okay. Standing by."), say("Standing by."))
    voiced(rt)
    page, session = await asked(rt, stt)
    await page.until("turn.complete")
    await idle(session)
    await played(session, page, 1, 2)
    await watching(session)
    await speak(session, tone(0.6))  # her voice, coming back as she plays
    await ended(session, page)
    await speak(session, room(0.1))
    await asyncio.sleep(0.7)
    stt.stream.say("Standing by.", final=True, turn="done")
    await speak(session, room(0.1))
    await until(lambda: session._listener is not None)  # the window, her echo let go
    await speak(session, room(session_module.FOLLOW_UP_S + 0.2))
    await page.until("mic.stop", 2)
    assert page.all("mic.stop")[-1]["reason"] == "nothing"
    assert len(page.all("transcript.final")) == 1 and session._turn == 1
    await session.close()


async def test_her_last_two_words_reported_late_without_a_watch_are_not_a_turn(
    rt: Runtime,
) -> None:
    stt, _ = judging(rt, say("Okay. Standing by."), say("Standing by."))
    voiced(rt)
    page, session = await asked(rt, stt)
    await page.until("turn.complete")
    await idle(session)
    await played(session, page, 1, 2)
    await ended(session, page)  # a fresh window, no watch
    await until(lambda: session._listener is not None)
    await speak(session, room(0.1))
    await asyncio.sleep(0.7)
    stt.stream.say("Standing by.", final=True, turn="done")
    await speak(session, room(0.2))
    await page.until("mic.stop", 2)
    await page.until("notice")
    assert len(page.all("transcript.final")) == 1 and session._turn == 1
    await session.close()


async def test_a_click_as_she_ends_does_not_make_their_reply_her_echo(rt: Runtime) -> None:
    """A noise right after her end is not when the user's words began."""
    stt, page, session = await leave(rt)
    await speak(session, tone(0.1) + room(0.2))  # a click, just as she ends
    await reply(session, stt, page, "Six?")
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == "Six?" and session._turn == 2
    await session.close()


# ── Duck, then confirm: short real cut-ins (R1) ──────────────────────────


@pytest.mark.parametrize("words", ["what about Tuesday", "skip that"])
async def test_short_real_cut_ins_stop_her(rt: Runtime, words: str) -> None:
    stt, page, session = await alright(rt)
    stt.stream.say(words, final=False)
    await speak(session, tone(0.2))
    stt.stream.say(words, final=True, turn="done")
    await speak(session, tone(0.1) + room(0.2))
    await page.until("interrupted")
    await until(lambda: session._listener is not None or len(page.all("transcript.final")) > 1)
    # "skip that" ends on a word that leads on: the turn waits for more quiet.
    await speak(session, room(3.0))
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == words
    await session.close()
