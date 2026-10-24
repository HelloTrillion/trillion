"""Watching while she speaks (VOI-9): the page streams the microphone, and each word
heard is judged hers or theirs by when it was said, what she was saying then and how
loud the microphone was (#102). Only their words duck her and are decided.

The clock is the recognizer's: seconds of audio the watching listener has sent. Her
voice here is a steady tone, one second per sentence, so where each of her sentences
lies on that clock, and how loud it was, is known exactly."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from tests.api.test_drafts import judging
from tests.api.test_listening import hearing, speak
from tests.api.test_overlap_session import asked, rt, until
from tests.api.test_session import Page, idle
from tests.brain.fakes import say
from tests.voice.audio import room, tone
from tests.voice.fakes import FakeSTT
from tests.voice.test_speaker import FakeVoice
from trillion.api import session as session_module
from trillion.api.runtime import Runtime, SpeechSettings
from trillion.api.session import Session
from trillion.brain.loop import INTERRUPTED
from trillion.brain.types import TextBlock
from trillion.voice.providers.system import SystemVoice
from trillion.voice.types import Word

__all__ = ["rt"]

ANSWER = "First thing to know. Second thing to know. Third thing to know."
# Her voice as sent, her echo as the microphone hears it (8% of her), and the user.
HER = 0.3
ECHO = HER * 0.08
LOUD = 0.6
SENTENCE_S = 1.0


class Steady(FakeVoice):
    """A voice made in the core: every sentence is `seconds` of tone at `HER`."""

    def __init__(self, seconds: float = SENTENCE_S) -> None:
        super().__init__()
        self._seconds = seconds

    async def synthesize(self, text: str, *, voice: str) -> AsyncIterator[bytes]:
        self.asked.append(text)
        yield tone(self._seconds, HER)


def steady(rt: Runtime, seconds: float = SENTENCE_S) -> None:
    rt.tts, rt.speech = Steady(seconds), SpeechSettings(kind="elevenlabs", voice="v1")


@pytest.fixture(autouse=True)
def patient(monkeypatch: pytest.MonkeyPatch) -> None:
    # A loaded machine can leave a few seconds between frames: not a page gone quiet.
    monkeypatch.setattr(session_module, "NO_AUDIO_S", 30.0)


async def watching(session: Session) -> None:
    await session.handle({"type": "overlap.watch"})
    assert session._overlap is not None and session._overlap.watching


async def answered(rt: Runtime, *replies: Any) -> tuple[FakeSTT, Page, Session]:
    """A spoken turn answered in full and over, while her reply still plays."""
    stt, _ = judging(rt, *(replies or (say(ANSWER),)), say("Of course."))
    steady(rt)
    page, session = await asked(rt, stt)
    await page.until("turn.complete")
    await idle(session)
    assert session._tail is not None
    return stt, page, session


async def spoken_reply(rt: Runtime, *replies: Any) -> tuple[FakeSTT, asyncio.Event, Page, Session]:
    """A spoken turn whose reply is held before the model's first word."""
    stt, provider = judging(rt, *replies)
    steady(rt)
    held = provider.hold(0)
    page, session = await asked(rt, stt)
    await until(lambda: provider.running == 1)
    return stt, held, page, session


async def played(session: Session, page: Page, *seqs: int) -> None:
    """The page reports her sentences playing, now."""
    turn = page.all("reply.sentence")[0]["turn"]
    for seq in seqs:
        await session.handle({"type": "playback.sentence", "turn_id": turn, "seq": seq})


async def ended(session: Session, page: Page) -> None:
    turn = page.all("reply.sentence")[0]["turn"]
    await session.handle({"type": "playback.ended", "turn_id": turn})


async def her(session: Session, seconds: float) -> None:
    """Her voice coming back through the microphone."""
    await speak(session, tone(seconds, ECHO))


async def them(session: Session, seconds: float) -> None:
    """The user, speaking up over her."""
    await speak(session, tone(seconds, LOUD))


def timed(text: str, start: float, end: float) -> tuple[Word, ...]:
    """The words of `text`, spread evenly from `start` to `end` on the recognizer's clock."""
    tokens = text.split()
    step = (end - start) / len(tokens)
    return tuple(
        Word(text=t, start_s=start + i * step, end_s=start + (i + 1) * step)
        for i, t in enumerate(tokens)
    )


def heard(stt: FakeSTT, *parts: tuple[str, float, float], **kw: Any) -> None:
    """One report of the recognizer's current turn, each part said over its span."""
    text = " ".join(p[0] for p in parts)
    words = tuple(w for p in parts for w in timed(*p))
    stt.stream.say(text, words=words, **kw)


def clock(session: Session) -> float:
    ov = session._overlap
    listener = ov.listener if ov is not None else session._listener
    assert listener is not None
    return listener.sent_s


def calls(rt: Runtime) -> int:
    return len(rt.provider.calls)  # type: ignore[union-attr]


# ── Her echo is hers ─────────────────────────────────────────────────────


async def test_her_echo_alone_changes_nothing(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    await watching(session)
    streams, partials = len(stt.streams), len(page.all("transcript.partial"))
    assert session.facts().listening  # nothing is delivered over the microphone
    await played(session, page, 1)
    await her(session, 1.0)
    heard(stt, ("First thing to snow.", 0.1, 0.9), final=True, turn="done")  # misheard
    await played(session, page, 2)
    await her(session, 0.6)
    heard(stt, ("second thing", 1.1, 1.5), final=False)
    await her(session, 0.2)
    assert len(stt.streams) == streams  # one connection, for the watch
    assert not page.all("overlap.duck") and not page.all("overlap.over")
    assert not page.all("interrupted") and len(page.all("transcript.partial")) == partials
    assert session._overlap is not None and session._overlap.watching
    history = await rt.conversations.history(session.id, "local")
    assert history[-1][1].blocks == (TextBlock(text=ANSWER),)
    await session.close()


async def test_an_echo_only_reply_never_ducks_her_nor_becomes_a_turn(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    await watching(session)
    before = calls(rt)
    for seq, text in enumerate(ANSWER.split(". "), start=1):
        at = clock(session)
        await played(session, page, seq)
        await her(session, 1.0)
        heard(stt, (text.lower().rstrip("."), at + 0.1, at + 0.9), final=True, turn="done")
    await ended(session, page)
    await speak(session, room(0.5))  # past her end: the window opens
    await until(lambda: session._listener is not None)
    await speak(session, room(session_module.FOLLOW_UP_S + 0.2))
    await page.until("mic.stop", 2)
    assert page.all("mic.stop")[-1]["reason"] == "nothing"
    await idle(session)
    assert not page.all("overlap.duck") and not page.all("interrupted")
    assert len(page.all("transcript.final")) == 1 and session._turn == 1
    assert calls(rt) == before and session._draft is None
    await session.close()


async def test_a_growing_report_with_one_misheard_word_is_still_her(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    await watching(session)
    await played(session, page, 1)
    for n, words in enumerate(("first", "first thang", "first thang to", "first thang to know")):
        await her(session, 0.2)
        heard(stt, (words, 0.05, 0.2 * (n + 1)), final=False)
    await her(session, 0.2)
    assert not page.all("overlap.duck") and not page.all("interrupted")
    await session.close()


async def test_her_echo_misheard_does_not_duck_her(rt: Runtime) -> None:
    """Live: "Alright, properly this time." came back as "or writes properly this"."""
    stt, page, session = await alright(rt)
    await her(session, 0.8)
    heard(stt, ("or writes properly this", 0.1, 0.7), final=False)
    await her(session, 0.4)
    assert not page.all("overlap.duck") and not page.all("interrupted")
    assert session._overlap is not None and session._overlap.watching
    await session.close()


async def test_a_stop_word_of_hers_does_not_stop_her(rt: Runtime) -> None:
    stt, page, session = await answered(rt, say("No, the train leaves at six. Then a bus."))
    await watching(session)
    await played(session, page, 1)
    await her(session, 0.8)
    heard(stt, ("no the trained", 0.1, 0.6), final=False)
    await her(session, 1.2)
    assert not page.all("overlap.duck") and not page.all("interrupted") and session._turn == 1
    await session.close()


async def test_her_stop_word_coming_back_does_not_stop_her(rt: Runtime) -> None:
    """Live: her "Stopped. For real this time," came back as "Stop." and stopped her."""
    reply = "Stopped. For real this time, here is the plan."
    stt, page, session = await answered(rt, say(reply))
    await watching(session)
    await played(session, page, 1)
    await her(session, 0.5)
    heard(stt, ("Stop.", 0.1, 0.4), final=True, turn="done")
    await her(session, 1.0)
    assert not page.all("interrupted") and session._turn == 1
    await session.close()


async def test_an_echo_of_her_first_sentence_before_it_is_reported_played(rt: Runtime) -> None:
    stt, held, page, session = await spoken_reply(rt, say(ANSWER))
    await watching(session)
    held.set()
    await page.until("reply.sentence")
    # No playback report yet: her first sentence is taken to start with the watch.
    await her(session, 0.6)
    heard(stt, ("first thing to know", 0.1, 0.5), final=False)
    await her(session, 0.2)
    assert not page.all("overlap.duck") and not page.all("interrupted")
    await session.close()


async def test_words_reported_without_times_are_judged_just_before_their_report(
    rt: Runtime,
) -> None:
    """A report with no word times: its words are taken as said 0.4 s before it."""
    stt, page, session = await answered(rt)
    await watching(session)
    await played(session, page, 1)
    await her(session, 1.0)
    stt.stream.say("first thing to snow", final=False)  # at 0.6: her echo, misheard
    await her(session, 0.2)
    assert not page.all("overlap.duck")
    await them(session, 0.6)
    stt.stream.say("first thing to snow what about the bus", final=False)  # at 1.4: theirs
    await them(session, 0.1)
    await page.until("overlap.duck")
    await session.close()


# ── Their words over her ─────────────────────────────────────────────────


async def test_an_acknowledgement_ducks_her_then_she_carries_on(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    await watching(session)
    await played(session, page, 1)
    await them(session, 0.4)
    heard(stt, ("yeah", 0.1, 0.3), final=True, turn="done")
    await her(session, 0.2)
    await page.until("overlap.over")
    kinds = [m["type"] for m in page.got if m["type"].startswith("overlap.")]
    assert kinds == ["overlap.duck", "overlap.over"]
    assert "end" not in page.all("overlap.over")[-1]  # still watching
    assert session._overlap is not None and session._overlap.watching
    assert not page.all("interrupted") and session._listener is None
    history = await rt.conversations.history(session.id, "local")
    assert history[-1][1].blocks == (TextBlock(text=ANSWER),)
    # The acknowledgement is let go, and her echo after it is hers.
    at = clock(session)
    await her(session, 0.3)
    heard(stt, ("thing to know", at, at + 0.3), final=False)
    await her(session, 0.2)
    assert len(page.all("overlap.duck")) == 1
    await session.close()


@pytest.mark.parametrize("words", ["Wait. Stop.", "stop"])
async def test_a_stop_word_not_hers_stops_her_at_once(rt: Runtime, words: str) -> None:
    stt, page, session = await answered(rt, say("Let me check the times. One moment."))
    await watching(session)
    await played(session, page, 1)
    await them(session, 0.4)
    heard(stt, (words, 0.1, 0.35), final=False)
    await them(session, 0.1)
    await page.until("interrupted")
    await session.close()


async def test_their_words_after_her_echo_stop_her_without_the_echo(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    await watching(session)
    await played(session, page, 1)
    await her(session, 1.0)
    heard(stt, ("first thing to know", 0.1, 0.9), final=False)
    await them(session, 0.8)
    assert not page.all("interrupted")
    heard(stt, ("first thing to know", 0.1, 0.9), ("wait the other one", 1.0, 1.8), final=False)
    await them(session, 0.2)
    await page.until("interrupted")
    await until(lambda: session._listener is not None)
    assert page.all("overlap.duck")
    assert page.all("interrupted")[-1]["kept"] == "First thing to know.…"
    assert page.all("mic.start")[-1]["follow_up"] is False
    # Their first words are kept, and her echo before them is not.
    assert page.all("transcript.partial")[-1]["text"] == "wait the other one"
    heard(
        stt,
        ("first thing to know", 0.1, 0.9),
        ("wait the other one please", 1.0, 2.0),
        final=True,
        turn="done",
    )
    await speak(session, room(0.1))
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == "wait the other one please"
    await idle(session)
    history = await rt.conversations.history(session.id, "local")
    assert history[-2][1].blocks[-1] == TextBlock(text="wait the other one please")
    assert history[-3][1].blocks == (TextBlock(text="First thing to know.…" + INTERRUPTED),)
    await session.close()


async def test_watching_while_her_turn_runs_and_cutting_in(rt: Runtime) -> None:
    stt, held, page, session = await spoken_reply(rt, say("We leave at six."), say("Okay."))
    await watching(session)
    await them(session, 0.5)
    heard(stt, ("actually make it seven", 0.05, 0.45), final=False)
    await them(session, 0.1)
    await page.until("interrupted")
    await until(lambda: session._listener is not None)
    assert page.all("overlap.duck")
    held.set()
    heard(stt, ("actually make it seven", 0.05, 0.45), final=True, turn="done")
    await speak(session, room(0.1))
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == "actually make it seven"
    await session.close()


async def test_watching_starts_before_her_first_sound(rt: Runtime) -> None:
    """Right after the user's turn, before a word of her reply: the page has held the
    microphone open, and the watch starts at once, on the timeline her reply takes."""
    stt, provider = judging(rt, say("We leave at six."))
    steady(rt)
    held = provider.hold(0)
    page, session = await asked(rt, stt)
    await watching(session)
    assert session._speaker is None  # nothing of hers yet
    await speak(session, room(0.5))
    held.set()
    await page.until("reply.sentence")
    await played(session, page, 1)  # at 0.5 on the recognizer's clock
    await her(session, 0.6)
    heard(stt, ("we leave at six", 0.6, 1.0), final=False)
    await her(session, 0.2)
    assert not page.all("overlap.duck")
    await session.close()


ALRIGHT = "Alright, properly this time. We start with the basics. Then the rest."


async def alright(rt: Runtime) -> tuple[FakeSTT, Page, Session]:
    stt, page, session = await answered(rt, say(ALRIGHT))
    await watching(session)
    await played(session, page, 1)
    return stt, page, session


async def test_their_words_after_the_duck_stop_her_and_all_are_kept(rt: Runtime) -> None:
    stt, page, session = await alright(rt)
    await them(session, 0.4)
    heard(stt, ("what about", 0.05, 0.35), final=False)
    await them(session, 0.1)
    await page.until("overlap.duck")
    assert not page.all("interrupted")
    await them(session, 0.4)
    heard(stt, ("what about the bus instead", 0.05, 0.85), final=False)
    await them(session, 0.1)
    await page.until("interrupted")
    await until(lambda: session._listener is not None)
    heard(stt, ("what about the bus instead", 0.05, 0.85), final=True, turn="done")
    await speak(session, room(0.1))
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == "what about the bus instead"
    await session.close()


@pytest.mark.parametrize("words", ["what about Tuesday", "skip that"])
async def test_short_real_cut_ins_stop_her(rt: Runtime, words: str) -> None:
    stt, page, session = await alright(rt)
    await them(session, 0.5)
    heard(stt, (words, 0.05, 0.45), final=False)
    await them(session, 0.1)
    heard(stt, (words, 0.05, 0.45), final=True, turn="done")
    await speak(session, room(0.2))
    await page.until("interrupted")
    await until(lambda: session._listener is not None or len(page.all("transcript.final")) > 1)
    # "skip that" ends on a word that leads on: the turn waits for more quiet.
    await speak(session, room(3.0))
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == words
    await session.close()


async def test_their_first_word_survives_the_recognizer_revising_its_turn(rt: Runtime) -> None:
    """Live: "Skip that." was saved as "that. No.". Her misheard echo, then the same
    recognizer turn revised to the user's words: "skip" is theirs from the start."""
    reply = "Tea was found in ancient China. That is the story."
    stt, page, session = await answered(rt, say(reply))
    await watching(session)
    await played(session, page, 1)
    await her(session, 0.5)
    heard(stt, ("or writes", 0.1, 0.4), final=False)  # her echo, misheard
    await them(session, 0.6)
    assert not page.all("overlap.duck")
    heard(stt, ("Skip that one please", 0.5, 1.05), final=False)  # revised
    await them(session, 0.1)
    await page.until("interrupted")
    await until(lambda: session._listener is not None or len(page.all("transcript.final")) > 1)
    heard(stt, ("Skip that one please.", 0.5, 1.05), final=True, turn="done")
    await speak(session, room(0.2))
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == "Skip that one please."
    await session.close()


async def test_her_own_words_shouted_over_her_once_her_echo_is_known_are_theirs(
    rt: Runtime,
) -> None:
    """ "send it" over her "Should I send it or wait?": hers by the words, theirs by how
    loud, once enough of her echo has been heard to know how much of her comes back."""
    reply = "Should I send it or wait for them? Tell me which."
    stt, page, session = await answered(rt, say(reply), say("Sent."))
    await watching(session)
    await played(session, page, 1)
    await her(session, 0.6)
    heard(stt, ("should i send it or", 0.05, 0.55), final=True, turn="done")
    await her(session, 0.1)
    assert session._echo.learned >= 5 and session._echo.value < 0.2
    assert not page.all("overlap.duck")
    await them(session, 0.4)
    heard(stt, ("send it", 0.7, 1.05), final=True, turn="done")
    await speak(session, room(0.2))
    await page.until("interrupted")
    await until(lambda: session._listener is not None or len(page.all("transcript.final")) > 1)
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == "send it"
    await session.close()


# ── Review of #102: words while she thinks, and after she is stopped ──


async def test_their_words_while_she_thinks_stay_theirs_once_her_voice_comes(
    rt: Runtime,
) -> None:
    """Review I1: the watch opens before her reply; what the user says meanwhile is
    not taken for her once her audio arrives, seconds later."""
    stt, provider = judging(rt, say("Sure, I can do that."), say("Tuesday too."))
    steady(rt)
    held = provider.hold(0)
    page, session = await asked(rt, stt)
    await watching(session)
    await speak(session, room(0.5))
    await them(session, 0.3)
    heard(stt, ("yeah", 0.55, 0.75), final=True, turn="done")  # an acknowledgement
    await speak(session, room(0.2))
    await page.until("overlap.over")
    held.set()
    await page.until("reply.sentence")
    await speak(session, room(0.1))
    ov = session._overlap
    assert ov is not None and ov.timeline is not None
    # Her sentence starts no earlier than its audio was sent, after the "yeah".
    ((start, _),) = ov.timeline.spans()
    assert start >= 0.75
    # Words said while she thought are theirs, judged now as when they were heard.
    said = timed("and also Tuesday", 0.1, 0.5)
    assert session._theirs(ov.listener, ov.timeline, said) == [True, True, True]
    await session.close()


async def test_their_words_after_she_is_stopped_are_theirs_though_she_had_more_to_say(
    rt: Runtime,
) -> None:
    """Review I2: her long sentence is cut where she was stopped, so the user saying
    the rest of it, after, is heard."""
    reply = "Should I send the email to Bob or wait until Monday?"
    stt, _ = judging(rt, say(reply), say("Sent."))
    steady(rt, seconds=6.0)
    page, session = await asked(rt, stt)
    await page.until("turn.complete")
    await idle(session)
    await watching(session)
    await played(session, page, 1)
    await her(session, 0.5)
    await them(session, 0.3)
    heard(stt, ("Stop.", 0.55, 0.8), final=False)
    await them(session, 0.1)
    await page.until("interrupted")
    await until(lambda: session._listener is not None)
    await speak(session, room(0.6))
    at = clock(session)
    await them(session, 0.8)
    heard(
        stt, ("Stop.", 0.55, 0.8), ("send the email to Bob.", at, at + 0.8), final=True, turn="done"
    )
    await speak(session, room(0.2))
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == "Stop. send the email to Bob."
    await session.close()


async def test_her_word_running_into_their_first_word_is_still_hers(rt: Runtime) -> None:
    """Live: "five -- What about Tuesday?" and "does it. What about Tuesday?": her echo
    word just before the user's first word, its time running into their voice, was
    taken as loud, and theirs. A word of theirs is loud all through; hers is not."""
    reply = "Should I send the email to Bob or wait until Monday?"
    stt, _ = judging(rt, say(reply), say("Tuesday."))
    steady(rt, seconds=6.0)
    page, session = await asked(rt, stt)
    await page.until("turn.complete")
    await idle(session)
    await watching(session)
    await played(session, page, 1)
    await her(session, 0.6)
    heard(stt, ("should i send the email", 0.05, 0.55), final=False)
    await her(session, 0.3)
    assert session._echo.learned >= 5 and not page.all("overlap.duck")
    await them(session, 0.6)  # from 0.9: "Bob" runs on into their voice
    parts = (("should i send the email", 0.05, 0.55), ("to Bob", 0.6, 1.0))
    heard(stt, *parts, ("what about Tuesday?", 1.0, 1.5), final=False)
    await them(session, 0.1)
    await page.until("interrupted")
    await until(lambda: session._listener is not None)
    heard(stt, *parts, ("what about Tuesday?", 1.0, 1.5), final=True, turn="done")
    await speak(session, room(0.2))
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == "what about Tuesday?"
    await session.close()


# ── The four live failures (#102) ────────────────────────────────────────

ASK = "Three hundred what? Not following, give me the actual ask."


async def asking(rt: Runtime) -> tuple[FakeSTT, Page, Session]:
    """Her two sentences, played and echoed, to her end at 2.0 on the clock."""
    stt, page, session = await answered(rt, say(ASK), say("Ok."))
    await watching(session)
    await played(session, page, 1)
    await her(session, 1.0)
    await played(session, page, 2)
    await her(session, 1.0)
    await ended(session, page)
    return stt, page, session


async def test_her_last_word_misheard_after_she_ends_is_not_a_turn(rt: Runtime) -> None:
    """Live: her final "ask." came back as "ass." and became the user's turn."""
    stt, page, session = await asking(rt)
    await speak(session, room(0.7))  # reported late, as live
    heard(stt, ("ass.", 1.75, 1.95), final=True, turn="done")
    await speak(session, room(0.2))
    await until(lambda: session._listener is not None)
    await speak(session, room(session_module.FOLLOW_UP_S + 0.2))
    await page.until("mic.stop", 2)
    assert page.all("mic.stop")[-1]["reason"] == "nothing"
    await idle(session)
    assert len(page.all("transcript.final")) == 1 and session._turn == 1
    assert not [m for m in page.all("notice") if m.get("reason") == "unheard"]
    await session.close()


async def test_her_late_echo_after_their_cut_in_is_not_their_words(rt: Runtime) -> None:
    """Live: "Skip that." was saved as "Skip that. -- three hundred.": her words, still
    coming back after she was stopped."""
    reply = "It comes to twenty seven hundred, or three hundred a month. Then taxes."
    stt, page, session = await answered(rt, say(reply), say("Ok."))
    await watching(session)
    await played(session, page, 1)
    await her(session, 0.3)
    await them(session, 0.4)
    heard(stt, ("skip that please", 0.3, 0.7), final=False)
    await them(session, 0.1)
    await page.until("interrupted")
    await until(lambda: session._listener is not None)
    await her(session, 0.4)  # her, still in the air as she stops
    heard(stt, ("skip that please", 0.3, 0.7), ("three hundred", 0.85, 1.15), final=False)
    await speak(session, room(0.1))
    heard(
        stt,
        ("Skip that please.", 0.3, 0.7),
        ("Three hundred.", 0.85, 1.15),
        final=True,
        turn="done",
    )
    await speak(session, room(0.2))
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == "Skip that please."
    await session.close()


STANDING = "Okay, I will wait here for you. Standing by."


async def test_her_short_last_sentence_reported_after_she_ends_is_not_a_turn(
    rt: Runtime,
) -> None:
    """Live: "Standing by." came back as the user's turn, and she answered it. As live:
    her echo was in the air before her end, and is reported 0.7 s after it."""
    stt, page, session = await answered(rt, say(STANDING), say("Standing by."))
    await watching(session)
    assert len(page.all("reply.sentence")) == 2
    before = calls(rt)
    await played(session, page, 1)
    await her(session, 1.0)
    await played(session, page, 2)
    await her(session, 1.0)
    await ended(session, page)
    await speak(session, room(0.7))
    heard(stt, ("Standing by.", 1.2, 1.8), final=False, turn="maybe")
    await speak(session, room(0.1))
    heard(stt, ("Standing by.", 1.2, 1.8), final=True, turn="done")
    await speak(session, room(0.1))
    await until(lambda: session._listener is not None)  # the window, her echo left out
    await speak(session, room(session_module.FOLLOW_UP_S + 0.2))
    await page.until("mic.stop", 2)
    assert page.all("mic.stop")[-1]["reason"] == "nothing"
    await idle(session)
    assert len(page.all("transcript.final")) == 1 and session._turn == 1
    assert calls(rt) == before and session._draft is None
    await session.close()


async def test_what_about_tuesday_amid_her_misheard_echo_is_exactly_theirs(
    rt: Runtime,
) -> None:
    """Live: "What about Tuesday?" was split into a turn of her misheard echo and
    "about Tuesday?". Her echo before and after their words is left out."""
    stt, page, session = await alright(rt)
    await her(session, 0.6)
    heard(stt, ("or writes", 0.1, 0.5), final=False)
    await them(session, 0.4)
    await played(session, page, 2)  # her next sentence starts at 1.0
    await them(session, 0.3)
    heard(stt, ("or writes", 0.1, 0.5), ("What about Tuesday?", 0.6, 1.25), final=False)
    await her(session, 0.1)
    await page.until("interrupted")
    await until(lambda: session._listener is not None)
    await her(session, 0.4)
    heard(
        stt,
        ("or writes", 0.1, 0.5),
        ("What about Tuesday?", 0.6, 1.25),
        ("the rest", 1.35, 1.7),
        final=True,
        turn="done",
    )
    await speak(session, room(0.2))
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == "What about Tuesday?"
    await session.close()


# ── After her end: the follow-up window ──────────────────────────────────


async def test_her_reply_ending_while_watching_becomes_the_follow_up_window(
    rt: Runtime,
) -> None:
    stt, page, session = await answered(rt)
    await watching(session)
    watch = session._overlap
    assert watch is not None
    streams = len(stt.streams)
    for seq in (1, 2, 3):
        await played(session, page, seq)
        await her(session, 1.0)
    heard(stt, ("third thing", 2.1, 2.5), final=False)  # her echo
    await ended(session, page)
    assert session._listener is None  # her echo is not over yet: 3.35 on the clock
    await speak(session, room(0.5))
    await until(lambda: session._listener is not None)
    await them(session, 0.5)
    heard(stt, ("third thing", 2.1, 2.5), ("and the fourth", 3.55, 3.95), final=True, turn="done")
    await speak(session, room(0.1))
    await page.until("transcript.final", 2)
    assert watch.promoting and session._overlap is None
    assert len(stt.streams) == streams  # no new connection
    assert page.all("mic.start")[-1]["follow_up"] is True
    assert not page.all("overlap.over")
    assert page.all("transcript.final")[-1]["text"] == "and the fourth"
    await session.close()


async def test_with_no_report_of_her_last_sentence_the_window_opens_on_her_estimated_end(
    rt: Runtime,
) -> None:
    """Her sentences never reported played on this clock: her end is estimated, and
    trusted once it is `FINAL_CAP_S` old."""
    _, page, session = await answered(rt)
    await watching(session)
    await ended(session, page)
    await speak(session, room(3.0))
    assert session._listener is None  # her end estimated at 3.35, not yet trusted
    await speak(session, room(3.5))
    await until(lambda: session._listener is not None)
    assert page.all("mic.start")[-1]["follow_up"] is True
    await session.close()


LEAVE = "We could take the bus. Or would you rather leave at six?"


async def leave(rt: Runtime) -> tuple[FakeSTT, Page, Session]:
    stt, page, session = await answered(rt, say(LEAVE), say("Six it is."))
    await watching(session)
    await played(session, page, 1)
    await her(session, 1.0)
    await played(session, page, 2)
    await her(session, 1.0)
    await ended(session, page)
    return stt, page, session


@pytest.mark.parametrize("words", ["Yes.", "Six?", "Thursday."])
async def test_a_one_word_reply_right_after_she_ends_is_a_turn(rt: Runtime, words: str) -> None:
    """Said half a second after her end (2.35 on the clock), even one of her words."""
    stt, page, session = await leave(rt)
    await speak(session, room(0.85))
    await them(session, 0.3)
    heard(stt, (words, 2.9, 3.1), final=True, turn="done")
    await speak(session, room(0.2))
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == words and session._turn == 2
    await session.close()


async def test_her_tail_then_their_words_in_one_turn_keeps_only_theirs(rt: Runtime) -> None:
    stt, page, session = await leave(rt)
    heard(stt, ("rather leave at six", 1.3, 1.9), final=False)
    await speak(session, room(0.6))
    await them(session, 0.6)
    heard(stt, ("rather leave at six", 1.3, 1.9), ("what about tomorrow", 2.65, 3.15), final=False)
    await speak(session, room(0.1))
    await until(lambda: session._listener is not None)
    heard(
        stt,
        ("rather leave at six", 1.3, 1.9),
        ("what about tomorrow", 2.65, 3.15),
        final=True,
        turn="done",
    )
    await speak(session, room(0.1))
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == "what about tomorrow"
    assert session._turn == 2
    await session.close()


async def test_an_acknowledgement_as_she_ends_leads_to_the_follow_up_window(
    rt: Runtime,
) -> None:
    stt, page, session = await answered(rt)
    await watching(session)
    watch = session._overlap
    assert watch is not None
    for seq in (1, 2):
        await played(session, page, seq)
        await her(session, 1.0)
    await played(session, page, 3)
    await them(session, 0.5)
    heard(stt, ("mm-hm yeah", 2.1, 2.4), final=False)
    await them(session, 0.1)
    await page.until("overlap.duck")
    await ended(session, page)
    assert session._listener is None  # still deciding
    heard(stt, ("mm-hm yeah", 2.1, 2.4), final=True, turn="done")
    await speak(session, room(0.1))
    await page.until("overlap.over")
    await speak(session, room(1.0))
    await until(lambda: session._listener is not None)
    assert session._listener is watch.listener and watch.listener.text() == ""
    assert page.all("mic.start")[-1]["follow_up"] is True
    history = await rt.conversations.history(session.id, "local")
    assert history[-1][1].blocks == (TextBlock(text=ANSWER),)
    await session.close()


async def test_a_follow_up_window_from_a_watch_closes_when_nobody_speaks(rt: Runtime) -> None:
    _, page, session = await answered(rt)
    await watching(session)
    await played(session, page, 1)
    await her(session, 1.0)
    await played(session, page, 2)
    await her(session, 1.0)
    await played(session, page, 3)
    await her(session, 1.0)
    await ended(session, page)
    await speak(session, room(0.5))
    await until(lambda: session._listener is not None)
    await speak(session, room(session_module.FOLLOW_UP_S + 0.2))
    await page.until("mic.stop")
    assert page.all("mic.stop")[-1]["reason"] == "nothing"
    assert session._listener is None
    await session.close()


# ── Without a watch: her last sentence is taken to end as the window opens ──


async def windowed(rt: Runtime) -> tuple[FakeSTT, Page, Session]:
    stt, _ = judging(rt, say(LEAVE), say("Six it is."))
    steady(rt)
    page, session = await asked(rt, stt)
    await page.until("turn.complete")
    await idle(session)
    await played(session, page, 1, 2)
    await ended(session, page)  # a fresh window, no watch
    await until(lambda: session._listener is not None)
    return stt, page, session


@pytest.mark.parametrize("words", ["Six?", "No, leave at six instead"])
async def test_replies_reusing_her_words_are_never_dropped(rt: Runtime, words: str) -> None:
    stt, page, session = await windowed(rt)
    await speak(session, room(0.5))
    await them(session, 0.6)
    heard(stt, (words, 0.55, 1.05), final=True, turn="done")
    await speak(session, room(0.2))
    await page.until("transcript.final", 2)
    assert page.all("transcript.final")[-1]["text"] == words
    await session.close()


async def test_her_last_words_as_a_window_opens_without_a_watch_are_not_a_turn(
    rt: Runtime,
) -> None:
    """The safety net: her echo still in the air as the window opens is dropped, and
    nothing is said about it."""
    stt, page, session = await windowed(rt)
    before = calls(rt)
    await her(session, 0.3)
    heard(stt, ("leave at six", 0.02, 0.3), final=False, turn="maybe")
    await speak(session, room(0.1))
    heard(stt, ("leave at six", 0.02, 0.3), final=True, turn="done")
    await speak(session, room(session_module.FOLLOW_UP_S + 0.2))
    await page.until("mic.stop", 2)
    await idle(session)
    assert len(page.all("transcript.final")) == 1 and session._turn == 1
    assert calls(rt) == before and session._draft is None
    assert not [m for m in page.all("notice") if m.get("reason") == "unheard"]
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


# ── The watch's bounds, and what ends it ─────────────────────────────────


async def test_a_reply_that_never_sounds_ends_the_watch(rt: Runtime) -> None:
    stt, provider = judging(rt, say("We leave at six."))
    steady(rt)
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
    stt, _, page, session = await spoken_reply(rt, say("A long answer."))
    await watching(session)
    stream = stt.stream
    await session.handle({"type": "interrupt"})
    assert session._overlap is None and stream.closed
    await page.until("interrupted")
    await idle(session)
    assert not page.all("overlap.over") and session._listener is None
    await session.close()


async def test_closing_while_watching_lets_the_listener_go(rt: Runtime) -> None:
    stt, _, _, session = await spoken_reply(rt, say("A long answer."))
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


async def test_new_settings_end_a_watch_that_is_deciding(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    await watching(session)
    await played(session, page, 1)
    await them(session, 0.3)
    heard(stt, ("what about", 0.05, 0.25), final=False)
    await them(session, 0.1)
    await page.until("overlap.duck")
    await session.settings_changed()
    assert session._overlap is None and page.all("overlap.over")[-1]["end"] is True
    await session.close()


async def test_a_barge_while_watching_stops_her_now(rt: Runtime) -> None:
    stt, page, session = await answered(rt)
    await watching(session)
    await played(session, page, 1)
    await her(session, 0.6)
    heard(stt, ("first thing to know", 0.1, 0.5), final=False)  # her echo, judged
    await her(session, 0.1)
    await session.handle({"type": "barge"})
    await until(lambda: session._listener is not None)
    assert page.all("interrupted")[-1]["kept"] == "First thing to know.…"
    await them(session, 0.3)
    heard(stt, ("first thing to know", 0.1, 0.5), ("hello", 0.75, 0.95), final=True, turn="done")
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
    await watching(session)
    await played(session, page, 1)
    await session.handle({"type": "overlap"})
    assert session._overlap is not None and not session._overlap.watching
    await them(session, 0.3)
    heard(stt, ("okay", 0.05, 0.25), final=True, turn="done")
    await speak(session, room(0.1))
    await page.until("overlap.over")
    assert session._overlap is not None and session._overlap.watching
    await session.close()


async def test_no_draft_starts_while_watching(rt: Runtime) -> None:
    _, _, session = await answered(rt)
    before = calls(rt)
    await watching(session)
    await session._start_draft("hello there")
    assert session._draft is None and session._tail is not None
    assert calls(rt) == before
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


async def test_watching_a_voice_the_page_makes_is_refused(rt: Runtime) -> None:
    """With no audio of hers in the core, her echo cannot be told apart by loudness."""
    _, held, page, session = await spoken_reply(rt, say("We leave at six."))
    rt.tts = SystemVoice()
    await session.handle({"type": "overlap.watch"})
    assert session._overlap is None and page.all("overlap.over")[-1]["end"] is True
    held.set()
    await idle(session)
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


async def test_no_watch_while_her_voice_is_off(rt: Runtime) -> None:
    _, held, page, session = await spoken_reply(rt, say("We leave at six."))
    await session.handle({"type": "speech.mute", "muted": True})
    await session.handle({"type": "overlap.watch"})
    assert session._overlap is None and page.all("overlap.over")[-1]["end"] is True
    held.set()
    await session.close()


async def test_a_cut_in_while_their_words_are_saved_waits_for_them(rt: Runtime) -> None:
    """Going on talking right after the turn: their first words are saved first, then
    what follows stops her and is the next turn."""
    stt, provider = judging(rt, say("We leave at six."), say("The budget, then."))
    steady(rt)
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
    await them(session, 0.3)
    heard(stt, ("and the budget", 0.05, 0.25), final=False)
    await them(session, 0.1)
    assert not page.all("interrupted") and session._collecting
    await page.until("transcript.final")
    assert page.all("transcript.final")[0]["text"] == "tell me the plan"
    await them(session, 0.1)
    await page.until("overlap.duck")
    heard(stt, ("and the budget for next week", 0.05, 0.45), final=False)
    await them(session, 0.1)
    await page.until("interrupted")
    await until(lambda: session._listener is not None)
    held.set()
    heard(stt, ("and the budget for next week", 0.05, 0.45), final=True, turn="done")
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
