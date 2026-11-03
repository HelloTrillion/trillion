"""Staying quiet in a session (VOI-7): a chosen silence ends the turn without a word
and without the notice an empty reply gets."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_drafts import judging
from tests.api.test_overlap_watch import steady
from tests.api.test_server import home, make, only, propose  # noqa: F401
from tests.api.test_session import Page, create, idle
from tests.api.test_session_notices import voiced
from tests.brain.fakes import USAGE, ScriptedProvider, is_naming, say, use
from tests.voice.audio import frames, room, tone
from tests.voice.fakes import FakeSTT
from trillion.api import session as session_module
from trillion.api.listening import FOLLOW_UP_S, QUIET_LISTEN_S
from trillion.api.runtime import ListenSettings, Runtime, open_runtime
from trillion.api.session import NUDGE, Session
from trillion.brain.types import Done, ModelEvent, ToolResultBlock
from trillion.secrets.store import MemoryStore
from trillion.tools.quiet import QUIET
from trillion.voice.types import Word


def test_a_typed_sign_off_ends_quiet_with_no_notice(make: Any) -> None:
    app = make(use(QUIET, {"kind": "done"}))
    with app.connect() as chat:
        msgs = chat.say("thanks, that's all")
    complete = only(msgs, "turn.complete")
    assert complete["stop"] == "quiet" and complete["quiet"] == "done"
    assert not [m for m in msgs if m["type"] in ("notice", "reply.delta", "error")]
    # One model call: nothing after the silence she chose.
    assert len(app.provider.calls) == 1


def test_an_empty_reply_still_says_so(make: Any) -> None:
    app = make([Done(stop_reason="end", usage=USAGE)])
    with app.connect() as chat:
        msgs = chat.say("hello")
    complete = only(msgs, "turn.complete")
    assert complete["stop"] == "empty" and "quiet" not in complete
    assert only(msgs, "notice") == {"type": "notice", "code": "stopped", "reason": "empty"}


def test_she_answers_a_card_tap_rather_than_staying_quiet(make: Any) -> None:
    app = make()
    app.provider._replies += [
        propose(app),
        # Her own turn, after the card: a silence she may choose.
        use(QUIET, {"kind": "done"}),
        # The turn that answers the tap: refused, so she answers it.
        use(QUIET, {"kind": "done"}),
        say("Done, the note is written."),
    ]
    with app.connect() as chat:
        card = only(chat.say("write a note"), "confirm.request")
        chat.ws.send_json({"type": "confirm.shown", "pending_id": card["pending_id"]})
        assert chat.tap("confirm", card["pending_id"])["status"] == "succeeded"
        msgs = chat.until("turn.complete")
    # The user's own turn could end quiet; the turn answering the tap could not.
    complete = only(msgs, "turn.complete")
    assert complete["stop"] == "end" and "quiet" not in complete
    results = [
        b for m in app.provider.calls[-1][1] for b in m.blocks if isinstance(b, ToolResultBlock)
    ]
    assert results[-1].is_error and "note from the app" in results[-1].content


# ── A spoken session (VOI-7, VOI-8, VOI-15d) ─────────────────────────────


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


def hearing(rt: Runtime, *replies: Any) -> FakeSTT:
    stt = FakeSTT(streaming=True)
    rt.stt = stt
    rt.listening = ListenSettings(kind="deepgram", choice="en")
    rt.provider = ScriptedProvider(*replies)
    return stt


async def speak(session: Session, pcm: bytes) -> None:
    for frame in frames(pcm):
        await session.audio(frame)
        await asyncio.sleep(0)


async def said(session: Session, stt: FakeSTT, words: str) -> None:
    """The user says `words` into the microphone that is open now."""
    await speak(session, room(0.2) + tone(0.6))
    stt.stream.say(words, final=True)
    await speak(session, room(0.6))


async def spoken_turn(session: Session, stt: FakeSTT, words: str) -> None:
    await session.handle({"type": "listen.start"})
    await said(session, stt, words)
    await idle(session)


def nudges(rt: Runtime) -> int:
    calls = rt.provider.calls  # type: ignore[union-attr]
    return sum(
        1
        for _system, messages in calls
        if any(NUDGE in str(getattr(b, "text", "")) for b in messages[-1].blocks)
    )


def orbs(page: Page) -> list[str]:
    return [m["orb"] for m in page.all("state")]


async def test_a_trailed_off_thought_keeps_the_microphone_open_then_nudges_once(
    rt: Runtime,
) -> None:
    stt = hearing(
        rt,
        use(QUIET, {"kind": "listening"}),
        # The nudge's own turn may not stay quiet: it is the host's.
        use(QUIET, {"kind": "listening"}, call_id="q2"),
        say("Go on, I'm with you."),
    )
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await spoken_turn(session, stt, "so I was thinking we could, um")
    await page.until("mic.start", 2)
    complete = page.all("turn.complete")[-1]
    assert complete["stop"] == "quiet" and complete["quiet"] == "listening"
    assert page.all("reply.delta") == [] and page.all("notice") == []
    assert page.all("mic.start")[-1] == {"type": "mic.start", "follow_up": True, "quiet": True}
    assert orbs(page)[-1] == "listening" and session._window is not None
    # A page's "played" for the silence never opens a second window.
    await session.handle({"type": "playback.ended", "turn_id": 1, "played_ms": 0})
    assert len(page.all("mic.start")) == 2

    # Eight seconds and not a word: one nudge, spoken as their turn was.
    await speak(session, room(QUIET_LISTEN_S + 0.2))
    assert page.all("mic.stop")[-1]["reason"] == "nothing"
    await page.until("turn.complete", 2)
    await idle(session)
    assert nudges(rt) == 1
    assert "".join(m["text"] for m in page.all("reply.delta")) == "Go on, I'm with you."
    assert page.all("turn.complete")[-1]["stop"] == "end"
    # No second window and no second nudge: after it, the usual follow-up only.
    assert session._window is None and len(page.all("mic.start")) == 2
    await session.handle({"type": "playback.ended", "turn_id": 1, "played_ms": 900})
    assert page.all("mic.start")[-1] == {"type": "mic.start", "follow_up": True}
    await speak(session, room(3.2))
    await idle(session)
    assert nudges(rt) == 1 and len(rt.provider.calls) == 3  # type: ignore[union-attr]
    await session.close()


async def test_speaking_in_the_window_is_a_turn_and_no_nudge(rt: Runtime) -> None:
    stt = hearing(rt, use(QUIET, {"kind": "listening"}), say("A budget problem, then."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await spoken_turn(session, stt, "maybe what I need is")
    await page.until("mic.start", 2)
    await said(session, stt, "more money for the team")
    await page.until("turn.complete", 2)
    await idle(session)
    assert nudges(rt) == 0
    last = rt.provider.calls[-1][1]  # type: ignore[union-attr]
    # She sees both parts, in order: the trailed-off line, her silence, then the rest.
    texts = [str(getattr(b, "text", "")) for m in last for b in m.blocks]
    assert any("maybe what I need is" in t for t in texts)
    assert "more money for the team" in texts[-1]
    await speak(session, room(QUIET_LISTEN_S + 0.2))
    assert nudges(rt) == 0
    await session.close()


async def test_pressing_to_talk_in_the_window_takes_the_floor(rt: Runtime) -> None:
    stt = hearing(rt, use(QUIET, {"kind": "listening"}), say("Got it."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await spoken_turn(session, stt, "I was wondering if")
    await page.until("mic.start", 2)
    await session.handle({"type": "listen.start"})
    assert page.all("mic.start")[-1] == {"type": "mic.start", "follow_up": False}
    assert session._window is None
    await said(session, stt, "we could move the launch")
    await page.until("turn.complete", 2)
    await idle(session)
    assert nudges(rt) == 0 and page.all("error") == []
    await session.close()


@pytest.mark.parametrize("how", ["close", "interrupt"])
async def test_a_window_never_outlives_its_conversation(rt: Runtime, how: str) -> None:
    """The eval starts a fresh conversation right after a quiet `listening`: the window
    and its nudge go with the old one, and nothing is left running."""
    stt = hearing(rt, use(QUIET, {"kind": "listening"}), say("never"))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await spoken_turn(session, stt, "so I was thinking")
    await page.until("mic.start", 2)
    listener = session._listener
    assert listener is not None and session._window is listener
    if how == "close":
        await session.close()
    else:
        await session.handle({"type": "interrupt"})
    assert session._listener is None and session._window is None
    assert session._task is None and stt.stream.closed
    await speak(session, room(QUIET_LISTEN_S + 0.2))
    await asyncio.sleep(0.05)
    assert nudges(rt) == 0 and len(rt.provider.calls) == 1  # type: ignore[union-attr]
    if how == "interrupt":
        await session.close()


async def test_a_spoken_sign_off_closes_the_microphone_and_is_seen(rt: Runtime) -> None:
    stt = hearing(rt, use(QUIET, {"kind": "done"}))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await spoken_turn(session, stt, "okay thanks that's all")
    complete = page.all("turn.complete")[-1]
    assert complete["stop"] == "quiet" and complete["quiet"] == "done"
    assert page.all("seen") == [{"type": "seen"}]
    assert page.all("mic.stop")[-1] == {"type": "mic.stop", "reason": "done"}
    assert orbs(page)[-1] == "idle"
    # Nothing reopens: not at the turn's end, and not on the page's "played".
    await session.handle({"type": "playback.ended", "turn_id": 1, "played_ms": 0})
    assert len(page.all("mic.start")) == 1
    await session.close()


async def test_a_typed_sign_off_is_seen_again_after_a_reload(rt: Runtime) -> None:
    hearing(rt, say("Here it is."), use(QUIET, {"kind": "done"}))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "what's on today"})
    await idle(session)
    await session.handle({"type": "turn.text", "text": "thanks, that's all"})
    await idle(session)
    assert page.all("seen") == [{"type": "seen"}]
    await session.close()

    again = Page(rt)
    reopened = await Session.start(rt, again.send, session.id)
    (history,) = again.all("history")
    assert history["messages"] == [
        {"role": "user", "text": "what's on today"},
        {"role": "assistant", "text": "Here it is."},
        {"role": "user", "text": "thanks, that's all", "seen": True},
    ]
    await reopened.close()


class _Held(ScriptedProvider):
    """Holds its reply until let go, so the page can report her heard meanwhile."""

    def __init__(self, *replies: Any) -> None:
        super().__init__(*replies)
        self.go = asyncio.Event()
        self.asked = asyncio.Event()

    async def stream(self, **kw: Any) -> AsyncIterator[ModelEvent]:  # type: ignore[override]
        self.asked.set()
        await self.go.wait()
        async for ev in super().stream(**kw):
            yield ev


async def test_played_before_the_turn_is_over_still_reopens_the_microphone(
    rt: Runtime,
) -> None:
    """The hot-mic race: `playback.ended` while the turn's task is still set."""
    stt = hearing(rt)
    voiced(rt)
    provider = _Held(say("Morning."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "listen.start"})
    await said(session, stt, "good morning")
    await provider.asked.wait()
    assert session._task is not None
    await session.handle({"type": "playback.ended", "turn_id": 1, "played_ms": 0})
    assert len(page.all("mic.start")) == 1
    provider.go.set()
    await idle(session)
    await page.until("mic.start", 2)
    assert page.all("mic.start")[-1] == {"type": "mic.start", "follow_up": True}
    assert orbs(page)[-1] == "listening"
    await session.close()


def test_leaving_after_a_quiet_listening_leaves_nothing_running(make: Any) -> None:
    """As the persona eval does it (evals/harness.py): a spoken turn over the socket with
    a scripted recognizer and no voice, then the socket goes for the next case."""
    app = make(use(QUIET, {"kind": "listening"}), say("never"))
    rt = app.rt
    rt.tts, rt.speech = None, None
    stt = FakeSTT(streaming=False)
    rt.stt, rt.listening = stt, ListenSettings(kind="deepgram", choice="en")
    stt.on_finish = ["so I was thinking we could, um"]
    with app.connect() as chat:
        chat.ws.send_json({"type": "listen.start"})
        chat.until("mic.start")
        for frame in frames(tone(0.5) + room(2.2)):
            chat.ws.send_bytes(frame)
        msgs = chat.until("turn.complete")
        assert only(msgs, "turn.complete")["quiet"] == "listening"
        # The window opens for them to go on.
        assert chat.until("mic.start")[-1] == {
            "type": "mic.start",
            "follow_up": True,
            "quiet": True,
        }
        (session,) = rt.sessions
        assert session._window is not None
    portal = app.client.portal
    assert portal is not None

    async def settled() -> None:
        for _ in range(400):
            if session._closed:
                break
            await asyncio.sleep(0.005)

    portal.call(settled)
    assert rt.sessions == set() and session._listener is None and session._window is None
    assert session._task is None and session._watchdog is None
    assert all(s.closed for s in stt.streams)
    assert len(app.provider.calls) == 1


class _HeldAfter(ScriptedProvider):
    """Answers its first `free` calls at once, then holds each later one until let go."""

    def __init__(self, *replies: Any, free: int) -> None:
        super().__init__(*replies)
        self.free = free
        self.go = asyncio.Event()
        self.asked = asyncio.Event()

    async def stream(self, **kw: Any) -> AsyncIterator[ModelEvent]:  # type: ignore[override]
        if len(self.calls) >= self.free and not is_naming(kw["system"]):
            self.asked.set()
            await self.go.wait()
        async for ev in super().stream(**kw):
            yield ev


async def test_an_earlier_turns_played_never_opens_the_microphone_after_a_later_one(
    rt: Runtime,
) -> None:
    """Her spoken turn still plays; a tap starts the turn that answers it; the first
    turn's "played" arrives while that one runs. The microphone must not open when it
    ends: its own words are still being heard."""
    stt = hearing(rt)
    voiced(rt)
    provider = _HeldAfter(
        create(rt, "a.txt", "c1"), say("Shall I?"), say("Done, it's written."), free=2
    )
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await spoken_turn(session, stt, "write a note called a")
    (card,) = page.all("confirm.request")
    first = page.all("audio.end")[-1]["turn"]
    await session.handle({"type": "confirm", "pending_id": card["pending_id"]})
    await provider.asked.wait()
    assert session._task is not None
    await session.handle({"type": "playback.ended", "turn_id": first, "played_ms": 900})
    provider.go.set()
    await idle(session)
    await page.until("turn.complete", 2)
    # Past where the window would have opened.
    await asyncio.sleep(0.05)
    assert orbs(page)[-1] == "idle"
    assert page.all("audio.end")[-1]["turn"] != first
    assert len(page.all("mic.start")) == 1
    await session.close()


async def test_a_refused_sign_off_is_never_marked_seen(rt: Runtime) -> None:
    """Refused on the turn that answers a tap, so she answered: no mark, now or after
    a reload."""
    hearing(
        rt,
        create(rt, "a.txt", "c1"),
        say("Shall I?"),
        use(QUIET, {"kind": "done"}, call_id="q1"),
        say("Done, it's written."),
    )
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "write a note called a"})
    await idle(session)
    (card,) = page.all("confirm.request")
    await session.handle({"type": "confirm", "pending_id": card["pending_id"]})
    await page.until("turn.complete", 2)
    await idle(session)
    assert page.all("seen") == []
    await session.close()
    again = Page(rt)
    reopened = await Session.start(rt, again.send, session.id)
    (history,) = again.all("history")
    assert not any(m.get("seen") for m in history["messages"])
    await reopened.close()


async def test_a_typed_trailing_off_is_answered(rt: Runtime) -> None:
    """Typing has no pauses to wait through: `listening` is refused and she answers."""
    hearing(rt, use(QUIET, {"kind": "listening"}, call_id="q1"), say("Go on, what's the idea?"))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "I was wondering if"})
    await idle(session)
    await asyncio.sleep(0.02)
    complete = page.all("turn.complete")[-1]
    assert complete["stop"] == "end" and "quiet" not in complete
    assert "".join(m["text"] for m in page.all("reply.delta")) == "Go on, what's the idea?"
    assert page.all("mic.start") == [] and page.all("seen") == []
    assert session._window is None and session._listener is None
    last = rt.provider.calls[-1][1]  # type: ignore[union-attr]
    (result,) = [b for b in last[-1].blocks if isinstance(b, ToolResultBlock)]
    assert result.is_error and "typed" in result.content.lower()
    await session.close()


# ── Over a watch: Flux hearing, her voice made in the core, barge-in on ──────
#
# The owner's setup. The page streams the microphone to the core while she answers
# (`overlap.watch`), so when her turn ends quiet the watch, not `_after_turn`, is what
# goes on listening. It must still be the quiet window, and only words hold it open.


class _Played(Page):
    """A page that reports her silence heard to the end as soon as it is told it is
    over, so the "played" arrives while her turn is still finishing."""

    def __init__(self, rt: Runtime) -> None:
        super().__init__(rt)
        self.session: Session | None = None

    async def send(self, body: dict[str, Any]) -> None:
        await super().send(body)
        if body.get("type") == "audio.end" and self.session is not None:
            turn = body["turn"]
            await self.session.handle({"type": "playback.ended", "turn_id": turn, "played_ms": 0})


async def quiet_over_a_watch(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch, *, early: bool = False
) -> tuple[FakeSTT, Page, Session]:
    """A spoken turn answered by a quiet `listening` while the page watches over her,
    and her silence then reported heard: the window is open."""
    monkeypatch.setattr(session_module, "NO_AUDIO_S", 30.0)
    stt, provider = judging(
        rt,
        use(QUIET, {"kind": "listening"}),
        # The nudge's own turn may not stay quiet: it is the host's.
        use(QUIET, {"kind": "listening"}, call_id="q2"),
        say("Go on, I'm with you."),
    )
    steady(rt)
    held = provider.hold(0)
    page = _Played(rt) if early else Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "listen.start"})
    await speak(session, room(0.2) + tone(0.6))
    stt.stream.say("so I was thinking about the launch", final=True, turn="done")
    await speak(session, room(0.1))
    for _ in range(400):
        if provider.running == 1:
            break
        await asyncio.sleep(0.005)
    # The page goes on streaming over her answer, as it does with barge-in on.
    await session.handle({"type": "overlap.watch"})
    assert session._overlap is not None and session._overlap.watching
    if isinstance(page, _Played):
        page.session = session
    held.set()
    await page.until("turn.complete")
    await idle(session)
    assert page.all("turn.complete")[-1]["quiet"] == "listening"
    if not early:
        turn = page.all("audio.end")[-1]["turn"]
        await session.handle({"type": "playback.ended", "turn_id": turn, "played_ms": 0})
    # The watch sees her silence is over on its next frame.
    await speak(session, room(0.2))
    await page.until("mic.start", 2)
    return stt, page, session


@pytest.mark.parametrize("early", [False, True])
async def test_over_a_watch_the_quiet_window_opens_and_nudges_once(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch, early: bool
) -> None:
    _, page, session = await quiet_over_a_watch(rt, monkeypatch, early=early)
    assert page.all("mic.start")[-1] == {"type": "mic.start", "follow_up": True, "quiet": True}
    assert session._listener is not None and session._window is session._listener
    assert session._overlap is None
    # Past the usual three-second follow-up: still open.
    await speak(session, room(FOLLOW_UP_S + 1.0))
    assert session._listener is not None and nudges(rt) == 0
    await speak(session, room(QUIET_LISTEN_S - FOLLOW_UP_S))
    await page.until("mic.stop", 2)
    assert page.all("mic.stop")[-1]["reason"] == "nothing"
    await page.until("turn.complete", 2)
    await idle(session)
    assert nudges(rt) == 1
    assert "".join(m["text"] for m in page.all("reply.delta")) == "Go on, I'm with you."
    await session.close()


async def test_over_a_watch_a_cough_does_not_hold_the_window_open(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, page, session = await quiet_over_a_watch(rt, monkeypatch)
    # Loud enough for the meter, but the recognizer heard no words in it.
    await speak(session, room(1.0) + tone(0.5, 0.4) + room(1.0) + tone(0.3, 0.4))
    await speak(session, room(QUIET_LISTEN_S))
    await page.until("mic.stop", 2)
    assert page.all("mic.stop")[-1]["reason"] == "nothing"
    await page.until("turn.complete", 2)
    await idle(session)
    assert nudges(rt) == 1
    await session.close()


async def test_over_a_watch_words_in_the_window_are_a_turn(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    stt, page, session = await quiet_over_a_watch(rt, monkeypatch)
    listener = session._listener
    assert listener is not None
    at = listener.sent_s
    await speak(session, tone(0.8, 0.4))
    words = (
        Word(text="more", start_s=at + 0.1, end_s=at + 0.3),
        Word(text="money", start_s=at + 0.3, end_s=at + 0.6),
    )
    stt.stream.say("more money", final=True, turn="done", words=words)
    await speak(session, room(0.3))
    await page.until("turn.complete", 2)
    await idle(session)
    assert nudges(rt) == 0
    last = rt.provider.calls[-1][1]  # type: ignore[union-attr]
    assert "more money" in str(getattr(last[-1].blocks[-1], "text", ""))
    await session.close()
