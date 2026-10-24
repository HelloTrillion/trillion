"""She names a conversation after her first reply, once (SUR-1)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_session import Page
from tests.brain.fakes import ScriptedProvider, is_naming, say, use
from trillion.api.runtime import OWNER, Runtime, open_runtime
from trillion.api.session import Session
from trillion.brain.types import ModelEvent, TextBlock
from trillion.secrets.store import MemoryStore


@pytest.fixture
async def rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    granted = tmp_path / "granted"
    granted.mkdir()
    runtime = await open_runtime(
        granted_roots=(granted,), persona=tmp_path, secrets=MemoryStore(), env={}
    )
    yield runtime
    await runtime.close()


class SlowNamer(ScriptedProvider):
    """Holds every naming call until `named` is set; turns run as scripted."""

    def __init__(self, *replies: list[ModelEvent]) -> None:
        super().__init__(*replies, names=True)
        self.named = asyncio.Event()

    async def stream(self, **kw: Any) -> AsyncIterator[ModelEvent]:
        if is_naming(kw["system"]):
            await self.named.wait()
        async for ev in super().stream(**kw):
            yield ev


async def test_a_title_follows_her_first_reply(rt: Runtime) -> None:
    rt.provider = ScriptedProvider(say("Monday at three."), say("Moving the dentist"), names=True)
    page = Page(rt)
    s = await Session.start(rt, page.send, None)
    assert page.all("session")[0]["title"] is None
    await s.handle({"type": "turn.text", "text": "move the dentist to monday"})
    await page.until("conversation.title")
    assert page.all("conversation.title")[0] == {
        "type": "conversation.title",
        "id": s.id,
        "title": "Moving the dentist",
    }
    assert await rt.conversations.title_of(s.id, OWNER) == ("Moving the dentist", "model")
    await s.close()


async def test_it_names_once(rt: Runtime) -> None:
    provider = ScriptedProvider(say("One."), say("First title"), say("Two."), names=True)
    rt.provider = provider
    page = Page(rt)
    s = await Session.start(rt, page.send, None)
    await s.handle({"type": "turn.text", "text": "one"})
    await page.until("conversation.title")
    await s.handle({"type": "turn.text", "text": "two"})
    await page.until("turn.complete", 2)
    await asyncio.sleep(0.05)
    assert len(page.all("conversation.title")) == 1
    assert provider.naming_calls == 1
    await s.close()


async def test_a_failed_name_keeps_the_first_words_and_is_not_retried(rt: Runtime) -> None:
    provider = ScriptedProvider(say("One."), say("Two."))  # naming answers nothing
    rt.provider = provider
    page = Page(rt)
    s = await Session.start(rt, page.send, None)
    await s.handle({"type": "turn.text", "text": "plan the week"})
    await page.until("turn.complete")
    await s.handle({"type": "turn.text", "text": "and the next"})
    await page.until("turn.complete", 2)
    await asyncio.sleep(0.05)
    assert provider.naming_calls == 1
    assert page.all("conversation.title") == []
    assert await rt.conversations.title_of(s.id, OWNER) == ("plan the week", "words")
    await s.close()


async def test_a_resumed_named_conversation_is_not_renamed(rt: Runtime) -> None:
    provider = ScriptedProvider(say("Hi."), say("Again."), names=True)
    rt.provider = provider
    page = Page(rt)
    s = await Session.start(rt, page.send, None)
    await rt.conversations.set_title(s.id, OWNER, "Mine", "user")
    s._title_source = "user"  # as a rename through the API does (Task 4)
    await s.handle({"type": "turn.text", "text": "hello"})
    await page.until("turn.complete")
    await asyncio.sleep(0.05)
    assert page.all("conversation.title") == []
    await s.close()
    page2 = Page(rt)
    s2 = await Session.start(rt, page2.send, s.id)
    assert page2.all("session")[0]["title"] == "Mine"
    await s2.handle({"type": "turn.text", "text": "hello again"})
    await page2.until("turn.complete")
    await asyncio.sleep(0.05)
    assert page2.all("conversation.title") == []
    assert provider.naming_calls == 0
    await s2.close()


async def test_a_slow_namer_does_not_hold_the_next_turn(rt: Runtime) -> None:
    provider = SlowNamer(say("One."), say("Two."), say("Slow title"))
    rt.provider = provider
    page = Page(rt)
    s = await Session.start(rt, page.send, None)
    await s.handle({"type": "turn.text", "text": "one"})
    await page.until("turn.complete")
    await s.handle({"type": "turn.text", "text": "two"})
    await page.until("turn.complete", 2)
    # Her second reply came while the naming call was still held.
    assert [m["text"] for m in page.all("reply.delta")] == ["One.", "Two."]
    assert page.all("conversation.title") == []
    provider.named.set()
    await page.until("conversation.title")
    assert page.all("conversation.title")[0]["title"] == "Slow title"
    await s.close()


async def test_closing_stops_a_naming_call_in_flight(rt: Runtime) -> None:
    provider = SlowNamer(say("One."), say("Never asked"))
    rt.provider = provider
    page = Page(rt)
    s = await Session.start(rt, page.send, None)
    await s.handle({"type": "turn.text", "text": "one"})
    await page.until("turn.complete")
    naming = s._naming
    assert naming is not None
    await s.close()
    assert naming.cancelled()
    assert page.all("conversation.title") == []


async def test_she_is_named_from_her_answer_not_what_she_said_before_a_tool(
    rt: Runtime,
) -> None:
    look = use("file_list", {"path": str(rt.granted_roots[0])}, text="Let me check. ")
    provider = ScriptedProvider(look, say("Nothing there yet."), say("Empty folder"), names=True)
    rt.provider = provider
    page = Page(rt)
    s = await Session.start(rt, page.send, None)
    await s.handle({"type": "turn.text", "text": "what is in my folder"})
    await page.until("conversation.title")
    _, sent = provider.calls[-1]
    body = "".join(b.text for b in sent[0].blocks if isinstance(b, TextBlock))
    assert "what is in my folder" in body and "Nothing there yet." in body
    assert "Let me check." not in body
    await s.close()


async def test_a_naming_call_finishing_after_a_rename_changes_nothing(rt: Runtime) -> None:
    provider = SlowNamer(say("One."), say("Slow title"))
    rt.provider = provider
    page = Page(rt)
    s = await Session.start(rt, page.send, None)
    await s.handle({"type": "turn.text", "text": "one"})
    await page.until("turn.complete")
    await rt.conversations.set_title(s.id, OWNER, "Mine", "user")
    await s.renamed("Mine")
    provider.named.set()
    await asyncio.sleep(0.1)
    assert [m["title"] for m in page.all("conversation.title")] == ["Mine"]
    assert await rt.conversations.title_of(s.id, OWNER) == ("Mine", "user")
    await s.close()
