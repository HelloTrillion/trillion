"""Asked to see her browser big, she opens its window on the user's screen
(browser_show): one message to her conversation's page, and nothing else."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from trillion.browser.browse import BrowserShow
from trillion.spine.contract import Risk, ToolContext


class Page:
    def __init__(self, cid: str) -> None:
        self.id = cid
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def _send(self, kind: str, **body: Any) -> None:
        self.sent.append((kind, body))


def ctx(cid: str) -> ToolContext:
    return ToolContext(owner_id="local", session_id=cid, turn=1, call_id="c", via="text")


async def test_it_opens_the_window_on_her_conversations_page_only() -> None:
    here, there = Page("conv"), Page("other")
    tool = BrowserShow()
    await tool.attach(SimpleNamespace(sessions={here, there}))  # type: ignore[arg-type]
    result = await tool.execute({}, ctx("conv"))
    assert result.ok
    assert here.sent == [("browser.show", {})] and there.sent == []


async def test_with_no_page_open_it_says_so() -> None:
    tool = BrowserShow()
    await tool.attach(SimpleNamespace(sessions=set()))  # type: ignore[arg-type]
    result = await tool.execute({}, ctx("conv"))
    assert not result.ok and result.error_kind == "not_open"


def test_it_only_reads_and_takes_nothing() -> None:
    d = BrowserShow.definition
    assert d.risk is Risk.READ and d.input_schema["properties"] == {}
