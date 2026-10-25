"""Her board tools through the real spine (D4): they run at once, never with a card,
are refused without changing the board, and are never offered unattended."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from trillion.api.runtime import Runtime, open_runtime
from trillion.secrets.store import MemoryStore
from trillion.spine.contract import ToolCall, ToolContext
from trillion.spine.registry import ToolRegistry
from trillion.spine.router import RouterResponse
from trillion.spine.untrusted import wrap
from trillion.storage.sqlite.board_store import BoardNotSaved
from trillion.tools.describe import NAME as DESCRIBE_SELF
from trillion.tools.workspace import CLOSE, REMOVE, SHOW, UPDATE
from trillion.workspace.blocks import BLOCK_TYPES
from trillion.workspace.widgets import TYPES as TYPES_OF_WIDGET

TOOLS = (SHOW, UPDATE, REMOVE, CLOSE)
STAT = {"type": "stat", "label": "Total", "value": "$41.20"}
CHART = {
    "type": "chart",
    "title": "Daily spend",
    "kind": "line",
    "x": ["Oct 1", "Oct 2"],
    "series": [{"name": "Spend", "values": [1.2, 3.4]}],
}


class Page:
    """A live page on a conversation, as the runtime sees a session."""

    def __init__(self, conversation_id: str) -> None:
        self.id = conversation_id
        self.got: list[tuple[str, dict[str, Any]]] = []

    async def send(self, kind: str, /, **body: Any) -> None:
        self.got.append((kind, body))


@pytest.fixture
async def rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    runtime = await open_runtime(persona=tmp_path, secrets=MemoryStore(), env={})
    await runtime.conversations.create("c1", "local", 1000)
    yield runtime
    await runtime.close()


async def call(rt: Runtime, name: str, args: dict[str, Any], **kw: Any) -> RouterResponse:
    ctx = ToolContext(
        **{"owner_id": "local", "session_id": "c1", "turn": 1, "call_id": "t1", "via": "text"} | kw
    )
    return await rt.router.execute(ToolCall(name=name, args=args), ctx)


async def saved(rt: Runtime) -> dict[str, Any] | None:
    return await rt.board_store.get("c1", "local")


async def test_show_runs_at_once_and_names_the_ids(rt: Runtime) -> None:
    here, elsewhere = Page("c1"), Page("c2")
    rt.sessions.update({here, elsewhere})
    done = await call(rt, SHOW, {"title": "October", "widgets": [STAT, CHART]})
    assert done.status == "succeeded" and done.pending_id is None
    assert 'Shown on the board: w1 stat "Total", w2 line chart "Daily spend".' in done.content
    ((kind, body),) = here.got
    assert kind == "board" and body["board"]["title"] == "October"
    assert elsewhere.got == []
    added = await call(rt, SHOW, {"widgets": [STAT], "mode": "add"})
    assert 'Shown on the board: w3 stat "Total".' in added.content


async def test_update_remove_and_close(rt: Runtime) -> None:
    await call(rt, SHOW, {"title": "October", "widgets": [STAT, CHART]})
    done = await call(rt, UPDATE, {"id": "w2", "widget": {**CHART, "kind": "bar"}})
    assert 'Updated on the board: w2 bar chart "Daily spend".' in done.content
    done = await call(rt, REMOVE, {"ids": ["w1"]})
    assert 'Taken off the board: w1 stat "Total".' in done.content
    done = await call(rt, CLOSE, {})
    assert done.status == "succeeded" and "kept" in done.content
    board = await saved(rt)
    assert board is not None and not board["open"] and len(board["widgets"]) == 1
    done = await call(rt, SHOW, {"widgets": []})
    assert 'The board is open again: w2 bar chart "Daily spend".' in done.content
    done = await call(rt, REMOVE, {"ids": ["all"]})
    assert "The board is empty and closed." in done.content


async def test_a_refused_call_leaves_the_board_as_it_was(rt: Runtime) -> None:
    await call(rt, SHOW, {"title": "October", "widgets": [STAT]})
    before = await saved(rt)
    bad = {**CHART, "x": ["Oct 1"]}
    done = await call(rt, SHOW, {"widgets": [bad]})
    assert done.status == "failed" and done.error_kind == "invalid"
    assert "series 'Spend'" in done.content
    gone = await call(rt, REMOVE, {"ids": ["w9"]})
    assert gone.status == "failed" and "w1" in gone.content
    assert await saved(rt) == before


async def test_no_board_outside_a_live_chat(rt: Runtime) -> None:
    done = await call(rt, SHOW, {"widgets": [STAT]}, session_id="nowhere")
    assert done.status == "failed" and done.error_kind == "invalid"
    assert await rt.board_store.get("nowhere", "local") is None


def test_never_offered_unattended() -> None:
    registry = ToolRegistry.discover()
    for name in TOOLS:
        d = registry.definition(name)
        # Wrapped as untrusted: results quote titles that may come from a page (SPN-5).
        assert d is not None and d.category == "workspace" and not d.trusted_output
        assert registry.unattended().definition(name) is None


async def test_refused_to_a_scheduled_run(rt: Runtime) -> None:
    done = await call(rt, SHOW, {"widgets": [STAT]}, via="schedule")
    assert done.status == "refused"
    assert await saved(rt) is None


async def test_describe_self_lists_them(rt: Runtime) -> None:
    tool = rt.registry.get(DESCRIBE_SELF)
    assert tool is not None
    ctx = ToolContext(owner_id="local", session_id="c1", turn=1, call_id="t1", via="text")
    report = json.loads((await tool.execute({}, ctx)).content)
    tools = {t["name"]: t for t in report["tools"]}
    for name in TOOLS:
        assert tools[name]["confirmation"] == "runs at once"
    assert "workspace_show" in report["board"]


async def test_results_are_wrapped_as_untrusted(rt: Runtime) -> None:
    done = await call(rt, SHOW, {"title": "October", "widgets": [STAT]})
    assert done.content == wrap(SHOW, 'Shown on the board: w1 stat "Total".')


async def test_a_board_that_cannot_be_saved_is_reported(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_: Any) -> None:
        raise BoardNotSaved("another owner's")

    monkeypatch.setattr(rt.board_store, "put", broken)
    done = await call(rt, SHOW, {"widgets": [STAT]})
    assert done.status == "failed" and done.error_kind == "save_failed"
    assert "could not be saved" in done.content


def _as_the_model_sees(name: str) -> dict[str, Any]:
    """A tool as a provider sends it (anthropic.py; openai_compat.py is the same data)."""
    d = ToolRegistry.discover().definition(name)
    assert d is not None
    return {"name": d.name, "description": d.description, "input_schema": dict(d.input_schema)}


def test_show_carries_a_card_per_type() -> None:
    text = _as_the_model_sees(SHOW)["description"]
    assert "next_up: event, start, end?, where?, people[]?, note?" in text
    for kind in TYPES_OF_WIDGET:
        assert f"{kind}: " in text, kind
    # The custom widget's blocks, by name.
    for block in BLOCK_TYPES:
        assert block in text, block


def test_the_four_tools_stay_small() -> None:
    size = len(json.dumps([_as_the_model_sees(name) for name in TOOLS]))
    assert size < 12_000, size


async def test_the_page_gets_blocks_and_her_source_is_stamped(rt: Runtime) -> None:
    page = Page("c1")
    rt.sessions.add(page)
    widget = {
        "type": "next_up",
        "event": "Design review",
        "start": "2026-10-25T14:30",
        "source": {"name": "Calendar", "at": "1999-01-01T00:00:00Z"},
        "asks": ["Prep me for this"],
    }
    done = await call(rt, SHOW, {"widgets": [widget]})
    assert done.status == "succeeded"
    ((_, body),) = page.got
    (shown,) = body["board"]["widgets"]
    assert shown["blocks"][0]["type"] == "time" and "event" not in shown
    assert shown["asks"] == ["Prep me for this"]
    assert shown["source"]["name"] == "Calendar"
    assert shown["source"]["at"] != "1999-01-01T00:00:00Z"
    assert shown["source"]["at"].endswith("Z")
    stored = await saved(rt)
    assert stored is not None and stored["widgets"][0]["event"] == "Design review"
