"""Notices reach the page through the door (SUR-9), never over a live reply (VOI-17)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_session import Gated, Page, idle
from tests.brain.fakes import say
from tests.scheduler.conftest import FakeClock, ms, schedule
from tests.voice.test_speaker import FakeVoice
from trillion.api.runtime import Runtime, SpeechSettings, local_zone, open_runtime
from trillion.api.session import NOTICE_TURNS, Session
from trillion.brain.types import ContextBlock, TextBlock
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
    assert said["text"] == "Time to stretch." and said["turn"] == NOTICE_TURNS + 1
    # When she has said it, the microphone stays closed: it was not a spoken turn.
    await session.handle({"type": "playback.ended", "turn_id": NOTICE_TURNS + 1})
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
