"""Feedback on what she speaks up about (D10, AGT-7) through the router: a plain
narrowing stands at once, anything else is a card; after a read (a heads-up's turn)
even a narrowing asks first; never with nobody there or from a tapped ask."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from trillion.spine.confirm import Refusal
from trillion.spine.contract import Risk, ToolCall, ToolContext
from trillion.spine.deny_floor import DenyPolicy
from trillion.spine.registry import ToolRegistry, runs_unattended
from trillion.spine.router import Router
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.attention_store import SqliteAttentionStore
from trillion.storage.sqlite.spine_store import SqliteSpineStore
from trillion.tools.speak_up import NARROW, WIDEN, SpeakUpNarrow, SpeakUpWiden

CTX = ToolContext(owner_id="local", session_id="chat-1", turn=1, call_id="t1", via="voice")
NARROWS = "Stop telling me about newsletters."
WIDENS = "watch: sam@example.com"


class Setup:
    def __init__(self, store: SqliteAttentionStore, router: Router) -> None:
        self.store, self.router = store, router
        self.narrow, self.widen = SpeakUpNarrow(), SpeakUpWiden()
        for tool in (self.narrow, self.widen):
            tool.bind(store, "local")

    async def notes(self, watcher: str = "email") -> str:
        return (await self.store.state("local", watcher)).notes


@pytest.fixture
async def it(tmp_path: Path) -> AsyncIterator[Setup]:
    spine_conn = await connect(tmp_path / "t.db")
    await migrate(spine_conn)
    attention_conn = await connect(tmp_path / "t.db")
    registry = ToolRegistry()
    router = Router(registry, SqliteSpineStore(spine_conn), policy=DenyPolicy.default())
    setup = Setup(SqliteAttentionStore(attention_conn), router)
    for tool in (setup.narrow, setup.widen):
        registry.register(tool)
    yield setup
    await attention_conn.close()
    await spine_conn.close()


def call(name: str, text: str, watcher: str = "email") -> ToolCall:
    return ToolCall(name=name, args=args(text, watcher))


def args(text: str, watcher: str = "email") -> dict[str, Any]:
    return {"watcher": watcher, "text": text}


async def test_a_narrowing_stands_at_once_and_is_read_back(it: Setup) -> None:
    done = await it.router.execute(call(NARROW, NARROWS), CTX)
    assert done.status == "succeeded"
    assert "Noted for urgent email: Stop telling me about newsletters." in done.content
    assert await it.notes() == NARROWS
    again = await it.router.execute(call(NARROW, "No receipts."), CTX)
    assert again.status == "succeeded"
    assert await it.notes() == f"{NARROWS}\nNo receipts."
    # Each watcher has its own.
    assert await it.notes("payments") == ""


@pytest.mark.parametrize(
    "text",
    [
        "Also tell me when Sam emails.",
        WIDENS,
        "Stop telling me about newsletters unless they are from Sam.",
    ],
)
async def test_text_that_widens_is_refused_as_a_narrowing(it: Setup, text: str) -> None:
    done = await it.router.execute(call(NARROW, text), CTX)
    assert done.status == "refused" and done.reason is Refusal.PRECONDITION_FAILED
    assert "speak_up_widen" in done.detail
    # The tool asks again itself, whatever got past its check.
    direct = await it.narrow.execute(args(text), CTX)
    assert not direct.ok and "speak_up_widen" in direct.content
    assert await it.notes() == ""


async def test_a_widening_is_a_card_and_stands_once_approved(it: Setup) -> None:
    card = await it.router.execute(call(WIDEN, WIDENS), CTX)
    assert card.status == "confirmation_required"
    assert card.summary == f"Speak up about urgent email: {WIDENS}"
    assert await it.notes() == ""
    tap = dataclasses.replace(CTX, via="tap", turn=2)
    done = await it.router.resolve(card.pending_id or "", "confirm", tap)
    assert done.status == "succeeded"
    assert await it.notes() == WIDENS


async def test_after_a_read_a_narrowing_does_not_stand_at_once(it: Setup) -> None:
    # The turn after a heads-up counts as read: its words may come from the email.
    read = dataclasses.replace(CTX, read_outside=True)
    done = await it.router.execute(call(NARROW, NARROWS), read)
    assert done.status == "refused" and "speak_up_widen" in done.detail
    assert not (await it.narrow.execute(args(NARROWS), read)).ok
    assert await it.notes() == ""
    # It can still be put on a card, for the user to approve.
    card = await it.router.execute(call(WIDEN, NARROWS), read)
    assert card.status == "confirmation_required"
    assert await it.notes() == ""


@pytest.mark.parametrize("via", ["schedule", "routine", "ask", "refresh", "channel"])
@pytest.mark.parametrize("name", [NARROW, WIDEN])
async def test_never_with_nobody_there_or_from_an_ask(it: Setup, via: str, name: str) -> None:
    ctx = dataclasses.replace(CTX, via=via)  # type: ignore[arg-type]
    text = NARROWS if name == NARROW else WIDENS
    r = await it.router.execute(call(name, text), ctx)
    assert r.status == "refused" and r.pending_id is None
    tool = it.narrow if name == NARROW else it.widen
    assert not (await tool.precheck(args(text), ctx)).ok
    assert not (await tool.execute(args(text), ctx)).ok
    assert await it.notes() == ""


async def test_neither_is_offered_to_a_run_nobody_watches() -> None:
    for tool in (SpeakUpNarrow(), SpeakUpWiden()):
        assert not runs_unattended(tool.definition)
    assert SpeakUpNarrow.definition.risk is Risk.WRITE
    assert SpeakUpWiden.definition.risk is Risk.IRREVERSIBLE


async def test_a_watcher_that_is_not_one_is_refused(it: Setup) -> None:
    r = await it.router.execute(call(NARROW, NARROWS, watcher="slack"), CTX)
    assert r.status == "refused"
    assert not (await it.narrow.execute(args(NARROWS, "slack"), CTX)).ok


async def test_full_notes_are_said_so(it: Setup) -> None:
    await it.store.set_notes("local", "email", "x" * 990)
    done = await it.router.execute(call(NARROW, NARROWS), CTX)
    assert done.status != "succeeded" and "full" in done.content + done.detail
    assert await it.notes() == "x" * 990
