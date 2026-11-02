"""Staying quiet in a session (VOI-7): a chosen silence ends the turn without a word
and without the notice an empty reply gets."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_server import home, make, only, propose  # noqa: F401
from tests.api.test_session import Page, idle
from tests.brain.fakes import USAGE, ScriptedProvider, say, use
from tests.voice.audio import frames, room, tone
from tests.voice.fakes import FakeSTT
from trillion.api.listening import QUIET_LISTEN_S
from trillion.api.runtime import ListenSettings, Runtime, open_runtime
from trillion.api.session import NUDGE, Session
from trillion.brain.types import Done, ModelEvent, ToolResultBlock
from trillion.secrets.store import MemoryStore
from trillion.tools.quiet import QUIET


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
