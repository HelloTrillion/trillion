"""What `confirm.request` tells the page: a friendly name for the tool and why it
needs the user, both from the host, never from the model (SPN-2a)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.api.test_session import Page, idle
from tests.brain.fakes import USAGE, ScriptedProvider, say, use
from tests.spine.fakes import SpendTool
from trillion.api.runtime import Runtime, open_runtime
from trillion.api.session import Session
from trillion.brain.types import Done, ToolUse
from trillion.secrets.store import MemoryStore
from trillion.spine.contract import GATED
from trillion.spine.labels import LABELS

CONNECTED = {"mcp", "google", "slack"}


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


async def card_for(rt: Runtime, tool: str, args: dict[str, object]) -> dict[str, object]:
    rt.provider = ScriptedProvider(use(tool, args, "c1"), say("Shall I?"))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "go on"})
    await page.until("confirm.request")
    await idle(session)
    await session.close()
    (card,) = page.all("confirm.request")
    return card


async def test_a_new_file_is_a_change_a_spoken_yes_can_approve(rt: Runtime) -> None:
    path = str(rt.granted_roots[0] / "a.txt")
    card = await card_for(rt, "file_create", {"path": path, "content": "x"})
    assert card["tool_label"] == "Create a file"
    assert card["reason"] == "write" and card["tap_only"] is False


async def test_replacing_a_file_cannot_be_undone(rt: Runtime) -> None:
    target = rt.granted_roots[0] / "a.txt"
    target.write_text("old", encoding="utf-8")
    card = await card_for(rt, "file_overwrite", {"path": str(target), "content": "new"})
    assert card["tool_label"] == "Replace a file"
    assert card["reason"] == "irreversible" and card["tap_only"] is True


async def test_spending_says_so(rt: Runtime) -> None:
    rt.registry.register(SpendTool())
    card = await card_for(rt, "spend_money", {"amount_cents": 500})
    assert card["tool_label"] == "spend money"
    assert card["reason"] == "spend" and card["tap_only"] is True


async def test_every_first_party_tool_that_can_ask_has_a_friendly_name(rt: Runtime) -> None:
    asks = {
        d.name for d in rt.registry.definitions() if d.risk in GATED and d.category not in CONNECTED
    }
    assert asks and asks <= LABELS.keys(), asks - LABELS.keys()


async def test_the_same_call_twice_in_one_reply_puts_up_one_card(rt: Runtime) -> None:
    """A small model can repeat a call. The page gets one card, not one per copy."""
    args = {"path": str(rt.granted_roots[0] / "a.txt"), "content": "x"}
    twice = [
        ToolUse(id="c1", name="file_create", args=args),
        ToolUse(id="c2", name="file_create", args=args),
        Done(stop_reason="tool_use", usage=USAGE),
    ]
    other = {"path": str(rt.granted_roots[0] / "b.txt"), "content": "x"}
    rt.provider = ScriptedProvider(twice, use("file_create", other, "c3"), say("Shall I?"))
    page = Page(rt)
    session = await Session.start(rt, page.send, None)
    await session.handle({"type": "turn.text", "text": "go on"})
    await page.until("confirm.request")
    await idle(session)
    await session.close()
    cards = page.all("confirm.request")
    # The repeat gets no card of its own; a different file still does.
    assert len(cards) == 2 and cards[0]["pending_id"] != cards[1]["pending_id"]
    assert str(cards[0]["summary"]).startswith("Create ")
