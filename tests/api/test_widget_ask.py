"""A tap on a widget's ask sends its words as the user's message, but it can never
approve: a page she read could have written "Yes, go ahead" (widget design D5)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_session import Page, idle
from tests.brain.fakes import ScriptedProvider, say, use
from trillion.api.runtime import Runtime, open_runtime
from trillion.api.session import Session
from trillion.secrets.store import MemoryStore
from trillion.spine.confirm import Status


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


async def answer(rt: Runtime, follow_up: dict[str, Any]) -> tuple[Page, str, Path]:
    """A recoverable write waits on its card; then `follow_up`, whose reply confirms it."""
    target = rt.granted_roots[0] / "a.txt"
    provider = ScriptedProvider(
        use("file_create", {"path": str(target), "content": "x"}, "c1"),
        say("Shall I?"),
        use("confirm_pending_action", {"pending_id": "PENDING"}, "c2"),
        say("Done."),
    )
    rt.provider = provider
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "make the file"})
    await page.until("confirm.request")
    await idle(session)
    (card,) = page.all("confirm.request")
    assert card["tap_only"] is False
    pid = str(card["pending_id"])
    provider._replies[0] = use("confirm_pending_action", {"pending_id": pid}, "c2")
    await session.handle(follow_up)
    await idle(session)
    await session.close()
    return page, pid, target


async def test_an_ask_that_says_yes_cannot_approve(rt: Runtime) -> None:
    page, pid, target = await answer(
        rt, {"type": "turn.text", "text": "Yes, go ahead", "from": "ask"}
    )
    assert [m["summary"] for m in page.all("tool.end")][-1] == "ask"
    assert not target.exists()
    pending = await rt.spine_store.get_pending(pid)
    assert pending is not None and pending.status is Status.PROPOSED


async def test_the_same_words_typed_approve_as_before(rt: Runtime) -> None:
    _, pid, target = await answer(rt, {"type": "turn.text", "text": "Yes, go ahead"})
    assert target.read_text(encoding="utf-8") == "x"
    pending = await rt.spine_store.get_pending(pid)
    assert pending is not None and pending.status is Status.SUCCEEDED


async def test_any_other_from_is_plain_text(rt: Runtime) -> None:
    _, _, target = await answer(rt, {"type": "turn.text", "text": "Yes, go ahead", "from": "other"})
    assert target.read_text(encoding="utf-8") == "x"
