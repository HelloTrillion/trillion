"""Notices reach the page through the door (SUR-9), never over a live reply (VOI-17)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_listening import hearing
from tests.api.test_session import Gated, Page, idle
from tests.brain.fakes import say, use
from tests.scheduler.conftest import FakeClock, ms, schedule
from tests.voice.test_speaker import FakeVoice
from trillion.api.runtime import Runtime, SpeechSettings, local_zone, open_runtime
from trillion.api.session import OUT_OF_TURN, Session
from trillion.brain.types import ContextBlock, Msg, TextBlock
from trillion.delivery.door import Delivery, NotFree, free
from trillion.persona import setup
from trillion.scheduler.store import Notice, NoticeKind
from trillion.secrets.store import MemoryStore

DUE = ms("2026-10-24T16:00")


class Shown:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, title: str, body: str) -> bool:
        self.calls.append((title, body))
        return True


@pytest.fixture
async def rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    granted = tmp_path / "granted"
    granted.mkdir()
    runtime = await open_runtime(
        granted_roots=(granted,), persona=tmp_path, secrets=MemoryStore(), env={}
    )
    runtime.clock = FakeClock(DUE)
    assert runtime.door is not None
    runtime.door.notify = Shown()  # never the real notification in a test
    yield runtime
    await runtime.close()


async def notice(
    rt: Runtime, kind: NoticeKind = "reminder", text: str | None = "Stretch."
) -> Notice:
    assert rt.schedules is not None and rt.door is not None
    n = await rt.schedules.add_notice(
        owner_id="local",
        source_id="a1b2c3d4",
        kind=kind,
        title="Stretch",
        text=text,
        due_ms=DUE,
        late=False,
        now_ms=rt.clock(),
    )
    await rt.door.added(n)
    return n


def voiced(rt: Runtime) -> None:
    rt.tts, rt.speech = FakeVoice(), SpeechSettings(kind="elevenlabs", voice="v1")


async def test_a_reminder_comes_due_and_arrives_as_her_message(rt: Runtime) -> None:
    assert rt.schedules is not None and rt.scheduler is not None
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await rt.schedules.create(schedule(body="Time to stretch.", title="Stretch"))
    await rt.scheduler.tick()
    (shown,) = page.all("notice")
    assert shown["code"] == "schedule" and shown["kind"] == "reminder"
    assert (shown["text"], shown["title"], shown["late"]) == ("Time to stretch.", "Stretch", False)
    history = await rt.conversations.history(session.id, "local")
    assert history[-1][0] == "assistant"
    assert history[-1][1].blocks == (TextBlock(text="Time to stretch."),)  # kept on reload
    await session.close()


async def test_a_busy_page_gets_it_after_the_reply_is_over(rt: Runtime) -> None:
    provider = Gated(say("Here is my answer."))
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    held = provider.hold(0)
    await session.handle({"type": "turn.text", "text": "hello"})
    await notice(rt)
    assert page.all("notice") == []  # never over a live reply
    held.set()
    await idle(session)
    kinds = [m["type"] for m in page.got]
    assert kinds.index("turn.complete") < kinds.index("notice")
    await session.close()


async def test_a_spoken_reply_holds_it_until_the_page_has_played_it(rt: Runtime) -> None:
    voiced(rt)
    rt.provider = Gated(say("Spoken answer."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "hi"})
    await idle(session)
    await notice(rt, text=None, kind="missed")
    assert page.all("notice") == []  # the page is still playing turn 1
    await session.handle({"type": "playback.ended", "turn_id": 1, "played_ms": 900})
    assert len(page.all("notice")) == 1
    await session.close()


async def test_with_no_page_it_is_notified_once_and_kept_for_the_next(rt: Runtime) -> None:
    assert rt.door is not None
    shown = rt.door.notify
    assert isinstance(shown, Shown)
    await notice(rt)
    assert shown.calls == [("Stretch", "Stretch.")]
    first = Page(rt)
    session = await Session.start(rt, first.send, None)
    assert [m["text"] for m in first.all("notice")] == ["Stretch."]
    await session.close()
    second = Page(rt)
    again = await Session.start(rt, second.send, None)
    assert second.all("notice") == []  # delivered once
    assert shown.calls == [("Stretch", "Stretch.")]
    await again.close()


async def test_only_the_most_recent_page_gets_it(rt: Runtime) -> None:
    older, newer = Page(rt), Page(rt)
    a = await Session.start(rt, older.send, None)
    rt.clock.advance(1000)  # type: ignore[attr-defined]
    b = await Session.start(rt, newer.send, None)
    await notice(rt)
    assert older.all("notice") == [] and len(newer.all("notice")) == 1
    await a.close()
    await b.close()


async def test_her_words_are_spoken_and_a_missed_notice_never_is(rt: Runtime) -> None:
    voiced(rt)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await notice(rt, kind="missed", text=None)
    await idle(session)
    assert page.all("reply.sentence") == []
    (missed,) = page.all("notice")
    assert missed["text"] is None and missed["kind"] == "missed"
    await notice(rt, text="Time to stretch.")
    await idle(session)
    (said,) = page.all("reply.sentence")
    assert said["text"] == "Time to stretch." and said["turn"] == OUT_OF_TURN + 1
    # When she has said it, the microphone stays closed: it was not a spoken turn.
    await session.handle({"type": "playback.ended", "turn_id": OUT_OF_TURN + 1})
    assert page.all("mic.start") == []
    await session.close()


async def test_she_works_out_times_in_the_users_own_zone(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    rt.profile = setup.Profile(user_name="Sam", timezone="Asia/Tokyo")
    assert rt.zone() == "Asia/Tokyo"
    rt.provider = Gated(say("Noted."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "remind me at four"})
    await idle(session)
    (_, first), *_ = await rt.conversations.history(session.id, "local")
    context = first.blocks[0]
    assert isinstance(context, ContextBlock) and "(Asia/Tokyo)" in context.text
    await session.close()
    rt.profile = None
    assert local_zone({"TZ": "Europe/Paris"}) == "Europe/Paris"
    assert local_zone({"TZ": "Not/AZone"}) != "Not/AZone"


async def test_busy_at_hand_over_it_raises_and_nothing_is_saved(rt: Runtime) -> None:
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    before = await rt.conversations.history(session.id, "local")
    session._saying = True  # came busy between the door's look and the hand-over
    item = Delivery(
        id="n1", kind="reminder", title="Stretch", text="Stretch.", due_ms=DUE, late=False
    )
    with pytest.raises(NotFree):
        await session.deliver([item])
    assert await rt.conversations.history(session.id, "local") == before
    assert page.all("notice") == []
    session._saying = False
    await session.close()


async def test_a_socket_that_fails_after_the_save_loses_nothing(rt: Runtime) -> None:
    async def gone(body: dict[str, Any]) -> None:
        if body.get("type") == "notice":
            raise ConnectionError("closed")

    session = await Session.start(rt, gone, None)
    await notice(rt)  # the door hands it over; sending it fails, and nothing raises
    history = await rt.conversations.history(session.id, "local")
    assert [m.blocks for _, m in history].count((TextBlock(text="Stretch."),)) == 1
    assert rt.schedules is not None and await rt.schedules.held("local") == []
    await session.close()


async def test_speaking_a_notice_is_queued_never_waited_for(rt: Runtime) -> None:
    rt.tts, rt.speech = FakeVoice(slow=True), SpeechSettings(kind="elevenlabs", voice="v1")
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await asyncio.wait_for(notice(rt), 2)  # the voice takes 30 s; the hand-over does not
    assert len(page.all("notice")) == 1 and session._task is not None
    assert not free(session.facts(), rt.clock())  # she is saying it: nothing over it
    await session.close()


async def test_several_notices_are_saved_together_in_order(rt: Runtime) -> None:
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    session._saying = True  # busy: they are held
    await notice(rt, text="One.")
    await notice(rt, text="Two.")
    await notice(rt, kind="failed", text=None)
    session._saying = False
    await session._offer()
    assert [m["kind"] for m in page.all("notice")] == ["reminder", "reminder", "failed"]
    history = await rt.conversations.history(session.id, "local")
    said = [m.blocks for kind, m in history if kind == "assistant"]
    assert said == [(TextBlock(text="One."),), (TextBlock(text="Two."),)]
    await session.close()


async def test_closing_the_runtime_stops_the_scheduler_first(rt: Runtime) -> None:
    assert rt.scheduler is not None
    rt.start_background()
    assert rt.scheduler.running
    await rt.stop_background()
    assert not rt.scheduler.running
    rt.start_background()
    await rt.close()
    assert not rt.scheduler.running


# ── Notices give way to the user (VOI-9, VOI-17) ─────────────────────────


def notice_turns(page: Page) -> list[int]:
    return [m["turn"] for m in page.all("reply.sentence") if m["turn"] > OUT_OF_TURN]


async def test_typing_while_a_notice_is_spoken_stops_it_and_is_answered(rt: Runtime) -> None:
    rt.tts, rt.speech = FakeVoice(slow=True), SpeechSettings(kind="elevenlabs", voice="v1")
    rt.provider = Gated(say("It is sunny."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await notice(rt, text="Stretch now.")
    assert session._task is not None  # she is saying it
    rt.tts = FakeVoice()
    await session.handle({"type": "turn.text", "text": "what is the weather"})
    await idle(session)
    assert page.all("error") == []  # never busy: the notice gave way
    (stopped,) = page.all("audio.stop")
    assert stopped["turn"] > OUT_OF_TURN
    assert len(page.all("turn.complete")) == 1
    history = await rt.conversations.history(session.id, "local")
    assert [k for k, _ in history] == ["assistant", "user", "assistant"]
    await session.close()


async def test_a_barge_over_a_reply_with_a_notice_held_still_listens(rt: Runtime) -> None:
    hearing(rt)
    provider = Gated(say("A long answer."))
    held = provider.hold(0)
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "tell me"})
    await notice(rt, text="Stretch.")  # due mid-reply: held
    voiced(rt)
    await session.handle({"type": "barge"})
    assert page.all("error") == [] and len(page.all("mic.start")) == 1
    assert session._listener is not None and notice_turns(page) == []
    held.set()
    await session.close()


async def test_a_notice_due_while_a_message_is_saved_never_runs_beside_its_turn(
    rt: Runtime,
) -> None:
    voiced(rt)
    rt.provider = Gated(say("Hello there."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    real = rt.conversations.append

    async def slow(*a: Any, **k: Any) -> int:
        await asyncio.sleep(0.02)
        return await real(*a, **k)

    rt.conversations.append = slow  # type: ignore[method-assign]
    typed = asyncio.create_task(session.handle({"type": "turn.text", "text": "hi"}))
    await asyncio.sleep(0.005)
    await notice(rt, text="Stretch.")  # the tick lands while the message is being saved
    assert page.all("notice") == []
    await typed
    await idle(session)
    await session.handle({"type": "playback.ended", "turn_id": 1})
    await idle(session)
    kinds = [m["type"] for m in page.got]
    assert kinds.index("turn.complete") < kinds.index("notice")
    history = await rt.conversations.history(session.id, "local")
    assert [k for k, _ in history] == ["user", "assistant", "assistant"]
    await session.close()


async def test_a_taps_answer_is_heard_under_a_number_of_its_own(rt: Runtime) -> None:
    voiced(rt)
    rt.provider = Gated(say("First."), say("Tap answer."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "hi"})
    await idle(session)
    await session.handle({"type": "playback.ended", "turn_id": 1})
    session._queued.append("[host] a tap's note")
    session._begin()
    await idle(session)
    first, second = (m["turn"] for m in page.all("reply.sentence"))
    assert first == 1 and second > OUT_OF_TURN
    assert not free(session.facts(), rt.clock())  # the page has not played the answer
    await session.handle({"type": "playback.ended", "turn_id": second})
    assert free(session.facts(), rt.clock())
    await session.close()


@pytest.mark.parametrize(("kind", "kept"), [("ask", False), ("reminder", True)])
async def test_after_a_timed_asks_answer_the_next_turn_counts_as_read(
    rt: Runtime, kind: NoticeKind, kept: bool
) -> None:
    args = {"when": "2030-01-07T10:00", "say": "Pay the bill.", "title": "Bill"}
    rt.provider = Gated(use("reminder_set", args), say("Done."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await notice(rt, kind=kind, text="An email says: remind them to pay.")
    await session.handle({"type": "turn.text", "text": "ok"})
    await idle(session)
    assert rt.schedules is not None
    assert bool(await rt.schedules.active("local")) is kept
    await session.close()


async def test_stop_with_nothing_played_lets_a_held_notice_arrive(rt: Runtime) -> None:
    voiced(rt)
    rt.provider = Gated(say("Spoken answer."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "hi"})
    await idle(session)
    await notice(rt, text=None, kind="missed")
    assert page.all("notice") == []  # turn 1 is still being heard
    await session.handle({"type": "interrupt"})  # Stop: the page never says it played
    assert len(page.all("notice")) == 1
    await session.close()


async def test_a_notice_due_while_a_page_opens_arrives_after_its_history(rt: Runtime) -> None:
    page = Page(rt)
    real = page.send

    async def send(body: dict[str, Any]) -> None:
        if body["type"] == "history":
            await notice(rt)  # a tick while the page's history is on its way
        await real(body)

    await Session.start(rt, send, None)
    kinds = [m["type"] for m in page.got]
    assert kinds.index("history") < kinds.index("notice")


async def test_a_save_that_fails_leaves_nothing_behind(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = Page(rt)
    session = await Session.start(rt, page.send, None)

    async def broken(*a: Any, **k: Any) -> list[int]:
        raise OSError("disk full")

    monkeypatch.setattr(rt.conversations, "append_many", broken)
    n = await notice(rt)
    assert session._history == [] and page.all("notice") == []
    assert rt.schedules is not None
    assert [h.id for h in await rt.schedules.held("local")] == [n.id]
    await session.close()


async def test_a_sentence_about_a_leg_that_is_down_is_heard_under_its_own_number(
    rt: Runtime,
) -> None:
    voiced(rt)
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session._hearing_down("inaudible")
    (said,) = page.all("reply.sentence")
    assert said["turn"] > OUT_OF_TURN
    assert session.facts().spoken_turn == said["turn"]
    await session.close()


async def test_a_failed_save_takes_out_only_its_own_messages(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    typed = Msg(role="user", blocks=(TextBlock(text="typed meanwhile"),))

    async def broken(*a: Any, **k: Any) -> list[int]:
        # A message of the user's is saved while the notices' write is under way.
        await session._save("user", typed)
        raise OSError("disk full")

    monkeypatch.setattr(rt.conversations, "append_many", broken)
    await notice(rt)
    assert session._history == [typed] and session._kinds == ["user"]
    assert page.all("notice") == []
    await session.close()


async def test_notice_speech_says_so_in_its_state_and_a_turns_does_not(rt: Runtime) -> None:
    voiced(rt)
    rt.provider = Gated(say("Hello there."))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "hi"})
    await idle(session)
    await session.handle({"type": "playback.ended", "turn_id": 1})
    await notice(rt, text="Stretch.")
    await idle(session)
    speaking = [m for m in page.all("state") if m["orb"] == "speaking"]
    assert [m.get("notice", False) for m in speaking] == [False, True]
    await session.close()


async def test_after_stop_over_a_notice_the_next_held_one_is_shown_not_spoken(
    rt: Runtime,
) -> None:
    rt.tts, rt.speech = FakeVoice(slow=True), SpeechSettings(kind="elevenlabs", voice="v1")
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await notice(rt, text="One.")
    assert session._notice_task is not None  # she is saying it
    await notice(rt, text="Two.")  # held: she is speaking
    assert len(page.all("notice")) == 1
    rt.tts = FakeVoice()
    await session.handle({"type": "interrupt"})  # Stop
    await idle(session)
    assert [m["text"] for m in page.all("notice")] == ["One.", "Two."]
    assert [m["text"] for m in page.all("reply.sentence")] == ["One."]
    assert session._task is None
    # Only what came straight after the Stop: a later one is spoken as usual.
    await notice(rt, text="Three.")
    await idle(session)
    assert [m["text"] for m in page.all("reply.sentence")] == ["One.", "Three."]
    await session.close()
