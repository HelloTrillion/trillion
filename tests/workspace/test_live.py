"""What a live widget remembers (the live widgets spec): only eligible reads, named by
their connector, matched by the widget's source, the last five; she cannot write
`live`, and the page never gets the reads."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_session import Page
from tests.brain.fakes import ScriptedProvider, say, use
from trillion.api.runtime import Runtime, open_runtime
from trillion.api.session import Session
from trillion.connectors.mcp.config import ServerConfig
from trillion.connectors.mcp.tool import McpTool, Offered, Outcome
from trillion.secrets.store import MemoryStore
from trillion.spine.contract import JSON, Risk, ToolCall, ToolContext, ToolDefinition
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.board_store import SqliteBoardStore
from trillion.storage.sqlite.conversation_store import SqliteConversationStore
from trillion.tools.workspace import SHOW, UPDATE
from trillion.workspace import live
from trillion.workspace.board import Board, BoardError
from trillion.workspace.live import Read, Reads
from trillion.workspace.service import Boards, for_page
from trillion.workspace.widgets import validate_widget

OWNER = "local"
KPIS = {
    "type": "kpis",
    "title": "Revenue",
    "source": "Stripe",
    "metrics": [{"label": "MRR", "value": "$4,200"}, {"label": "New", "value": "12"}],
}


def definition(name: str, risk: Risk = Risk.READ, category: str = "mcp") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=name,
        input_schema={"type": "object", "additionalProperties": False},
        risk=risk,
        category=category,
        timeout_s=5,
    )


async def _never(name: str, args: JSON, timeout: float) -> Outcome:
    raise AssertionError("never called")


def mcp_tool(
    remote: str = "list_charges",
    *,
    title: str = "Stripe",
    risk: Risk = Risk.READ,
    call: Any = _never,
) -> McpTool:
    server = ServerConfig(
        name="stripe", transport="stdio", command="x", title=title, risks={remote: risk}
    )
    args = {"limit": {"type": "integer"}, "charge": {"type": "string"}}
    offered = Offered(
        name=remote, description=remote, input_schema={"type": "object", "properties": args}
    )
    return McpTool(server, offered, f"mcp_stripe_{remote}", call)


def read(tool: str, connector: str = "Stripe", n: int = 0) -> Read:
    return Read(tool=tool, args={"n": n}, connector=connector)


# ── Which reads count ────────────────────────────────────────────────────


def test_only_reads_that_may_run_with_nobody_deciding() -> None:
    assert live.eligible(definition("mcp_stripe_list_charges"))
    assert live.eligible(definition("gmail_search", category="google"))
    for d in (
        definition("mcp_stripe_refund", Risk.WRITE),
        definition("mcp_stripe_pay", Risk.SPEND),
        definition("gmail_send", Risk.IRREVERSIBLE, "google"),
        definition("web_fetch", category="web"),
        definition("describe_self", category="meta"),
        definition("memory_recall", category="memory"),
        definition("workspace_show", category="workspace"),
        definition("schedule_list", category="schedule"),
        None,
    ):
        assert not live.eligible(d), d


def test_a_read_is_named_by_its_connectors_tile() -> None:
    assert live.connector("gmail_search", None) == "Gmail"
    assert live.connector("gmail_read", None) == "Gmail"
    assert live.connector("calendar_events", None) == "Google Calendar"
    assert live.connector("calendar_busy", None) == "Google Calendar"
    assert live.connector("drive_search", None) == "Google Drive"
    assert live.connector("slack_read", None) == "Slack"
    # An MCP tool by its server's title, or its name when it has none.
    assert live.connector("mcp_stripe_list_charges", mcp_tool()) == "Stripe"
    assert live.connector("mcp_stripe_list_charges", mcp_tool(title="")) == "stripe"
    # Anything else belongs to no connector, so no widget can name it.
    assert live.connector("file_read", None) is None
    assert live.connector("web_search", None) is None


@pytest.mark.parametrize(
    ("source", "title", "same"),
    [
        ("Stripe", "Stripe", True),
        (" stripe ", "Stripe", True),
        ("Calendar", "Google Calendar", True),
        ("google calendar", "Google Calendar", True),
        ("Jira", "Jira and Confluence", True),
        ("monday", "monday.com", True),
        ("Gmail", "Google Drive", False),
        ("Google", "Google Calendar", False),
        ("", "Stripe", False),
    ],
)
def test_a_source_names_a_connector_as_tiles_do(source: str, title: str, same: bool) -> None:
    assert live.matches(source, title) is same


def test_tile_names_are_the_pages() -> None:
    # The same rule as tileNames in apps/web/src/board.ts.
    assert live.tile_names("Google Calendar") == ["google calendar", "calendar"]
    assert live.tile_names("Jira and Confluence") == ["jira and confluence", "jira"]
    assert live.tile_names("monday.com") == ["monday.com", "monday"]
    assert live.tile_names("Stripe") == ["stripe"]


def test_reads_are_kept_per_turn_and_dropped_when_it_ends() -> None:
    reads = Reads()
    args = {"limit": 5}
    reads.record("c1", 1, "mcp_stripe_list_charges", args, "Stripe")
    args["limit"] = 99  # the arguments as called, not as later changed
    reads.record("c1", 2, "gmail_search", {}, "Gmail")
    reads.record("c2", 1, "slack_read", {}, "Slack")
    (one,) = reads.of("c1", 1)
    assert one == Read(tool="mcp_stripe_list_charges", args={"limit": 5}, connector="Stripe")
    reads.end("c1", 1)
    assert reads.of("c1", 1) == []
    assert [r.tool for r in reads.of("c1", 2)] == ["gmail_search"]
    assert [r.tool for r in reads.of("c2", 1)] == ["slack_read"]


def test_a_turn_keeps_a_bounded_number_of_reads() -> None:
    reads = Reads()
    for n in range(live.KEEP + 7):
        reads.record("c1", 1, "gmail_search", {"n": n}, "Gmail")
    kept = reads.of("c1", 1)
    assert len(kept) == live.KEEP and kept[-1].args == {"n": live.KEEP + 6}


# ── live: the last five of the source's reads ───────────────────────────


def test_live_is_the_last_five_reads_of_its_source() -> None:
    reads = [read("mcp_stripe_list_charges", n=n) for n in range(7)]
    reads.insert(3, read("gmail_search", "Gmail"))
    got = live.live_for({"type": "kpis", "source": {"name": "stripe"}}, reads)
    assert got == {
        "reads": [{"tool": "mcp_stripe_list_charges", "args": {"n": n}} for n in range(2, 7)],
        "every": 15,
    }


def test_no_source_or_no_matching_read_is_a_snapshot() -> None:
    reads = [read("gmail_search", "Gmail")]
    assert live.live_for({"type": "kpis"}, reads) is None
    assert live.live_for({"type": "kpis", "source": {"name": "Stripe"}}, reads) is None
    assert live.live_for({"type": "kpis", "source": {"name": "Stripe"}}, []) is None


def test_a_short_name_matches_the_connectors_reads() -> None:
    reads = [read("calendar_events", "Google Calendar")]
    got = live.live_for({"type": "next_up", "source": {"name": "Calendar"}}, reads)
    assert got is not None and got["every"] == 2


@pytest.mark.parametrize(
    ("kind", "minutes"),
    [
        ("next_up", 2),
        ("inbox", 2),
        ("mentions", 2),
        ("checks", 2),
        ("deploys", 2),
        ("services", 2),
        ("support_queue", 2),
        ("transactions", 15),
        ("invoices", 15),
        ("breakdown", 15),
        ("payouts", 15),
        ("pipeline", 15),
        ("deal", 15),
        ("contact", 15),
        ("campaign", 15),
        ("kanban", 15),
        ("sprint", 15),
        ("tasks", 15),
        ("due_soon", 15),
        ("kpis", 15),
        ("stat", 60),
        ("chart", 60),
        ("errors", 60),
        ("custom", 60),
    ],
)
def test_how_often_by_type(kind: str, minutes: int) -> None:
    assert live.every({"type": kind}) == minutes


def test_her_refresh_wins_and_off_keeps_the_default() -> None:
    assert live.every({"type": "stat", "refresh": 5}) == 5
    assert live.every({"type": "next_up", "refresh": "off"}) == 2


# ── What she writes ─────────────────────────────────────────────────────


def test_she_cannot_write_live() -> None:
    sneaky = {**KPIS, "live": {"reads": [{"tool": "mcp_stripe_refund", "args": {}}], "every": 2}}
    assert "live" not in validate_widget(sneaky, 1)


@pytest.mark.parametrize(
    ("given", "kept"),
    [("off", "off"), (" Off ", "off"), (2, 2), (1440, 1440), ("15", 15), (" 30 ", 30), (60.0, 60)],
)
def test_refresh_is_off_or_minutes(given: Any, kept: Any) -> None:
    assert validate_widget({**KPIS, "refresh": given}, 1)["refresh"] == kept


@pytest.mark.parametrize("given", [1, 0, 1441, -5, "soon", "1.5", 2.5, True, [5], {"m": 5}])
def test_refresh_out_of_range_is_refused(given: Any) -> None:
    with pytest.raises(BoardError, match="refresh"):
        validate_widget({**KPIS, "refresh": given}, 1)


def test_no_refresh_is_not_saved() -> None:
    assert "refresh" not in validate_widget(KPIS, 1)
    assert "refresh" not in validate_widget({**KPIS, "refresh": None}, 1)


# ── The service attaches it; the page never sees the reads ──────────────


class Pages:
    def __init__(self) -> None:
        self.sent: list[Board | None] = []

    async def __call__(self, conversation_id: str, board: Board | None) -> None:
        self.sent.append(board)


@pytest.fixture
async def boards(tmp_path: Path) -> AsyncIterator[tuple[Boards, Reads, Pages]]:
    conn = await connect(tmp_path / "t.db")
    await migrate(conn)
    chats = SqliteConversationStore(conn)
    await chats.create("c1", OWNER, 1000)
    reads, pages = Reads(), Pages()
    b = Boards(SqliteBoardStore(conn), chats, pages, clock=lambda: 5000, owner=OWNER, reads=reads)
    yield b, reads, pages
    await conn.close()


async def test_show_attaches_live_from_this_turns_reads(
    boards: tuple[Boards, Reads, Pages],
) -> None:
    b, reads, pages = boards
    reads.record("c1", 3, "mcp_stripe_list_charges", {"limit": 10}, "Stripe")
    reads.record("c1", 3, "gmail_search", {"q": "invoice"}, "Gmail")
    reads.record("c1", 2, "mcp_stripe_list_payouts", {}, "Stripe")  # another turn's
    note = {"type": "note", "text": "Hi"}
    board = await b.show("c1", "Money", [KPIS, note], turn=3)
    kpis, plain = board["widgets"]
    assert kpis["live"] == {
        "reads": [{"tool": "mcp_stripe_list_charges", "args": {"limit": 10}}],
        "every": 15,
    }
    assert "live" not in plain
    # The page: how often and how it is going, never the reads.
    page = pages.sent[-1]
    assert page is not None
    assert page["widgets"][0]["live"] == {"every": 15, "state": "idle"}
    assert "live" not in page["widgets"][1]
    assert "reads" not in repr(page) and "list_charges" not in repr(page)


async def test_without_a_turn_nothing_is_remembered(boards: tuple[Boards, Reads, Pages]) -> None:
    b, reads, _ = boards
    reads.record("c1", 1, "mcp_stripe_list_charges", {}, "Stripe")
    board = await b.show("c1", None, [KPIS])
    assert "live" not in board["widgets"][0]


async def test_adding_leaves_the_widgets_already_there(
    boards: tuple[Boards, Reads, Pages],
) -> None:
    b, reads, _ = boards
    reads.record("c1", 1, "mcp_stripe_list_charges", {"a": 1}, "Stripe")
    await b.show("c1", None, [KPIS], turn=1)
    reads.record("c1", 2, "mcp_stripe_list_charges", {"a": 2}, "Stripe")
    board = await b.show("c1", None, [KPIS], mode="add", turn=2)
    first, second = board["widgets"]
    assert first["live"]["reads"] == [{"tool": "mcp_stripe_list_charges", "args": {"a": 1}}]
    assert second["live"]["reads"] == [{"tool": "mcp_stripe_list_charges", "args": {"a": 2}}]


async def test_update_rebuilds_live_or_keeps_it(boards: tuple[Boards, Reads, Pages]) -> None:
    b, reads, _ = boards
    reads.record("c1", 1, "mcp_stripe_list_charges", {"a": 1}, "Stripe")
    await b.show("c1", None, [KPIS], turn=1)
    old = [{"tool": "mcp_stripe_list_charges", "args": {"a": 1}}]

    # A turn that read nothing from Stripe keeps the reads; her refresh sets how often.
    board = await b.update("c1", "w1", {**KPIS, "refresh": "5"}, turn=2)
    assert board["widgets"][0]["live"] == {"reads": old, "every": 5}

    # A turn that read Stripe again rebuilds them.
    reads.record("c1", 3, "mcp_stripe_list_balance", {}, "Stripe")
    board = await b.update("c1", "w1", KPIS, turn=3)
    new = [{"tool": "mcp_stripe_list_balance", "args": {}}]
    assert board["widgets"][0]["live"] == {"reads": new, "every": 15}

    # Named from somewhere else with nothing read from it: a snapshot.
    board = await b.update("c1", "w1", {**KPIS, "source": "PayPal"}, turn=4)
    assert "live" not in board["widgets"][0]
    # No source: a snapshot too.
    await b.update("c1", "w1", KPIS, turn=3)
    no_source = {k: v for k, v in KPIS.items() if k != "source"}
    board = await b.update("c1", "w1", no_source, turn=5)
    assert "live" not in board["widgets"][0]


def test_the_page_is_told_when_it_is_off_or_failed() -> None:
    reads = [{"tool": "mcp_stripe_list_charges", "args": {}}]
    widget: dict[str, Any] = {"id": "w1", **validate_widget(KPIS, 1)}
    widget["live"] = {"reads": reads, "every": 15}
    board: Board = {"title": "", "open": True, "next_id": 2, "widgets": [widget]}

    def shown(w: dict[str, Any]) -> Any:
        page = for_page({**board, "widgets": [w]})
        assert page is not None
        return page["widgets"][0].get("live")

    assert shown(widget) == {"every": 15, "state": "idle"}
    assert shown({**widget, "refresh": "off"}) is None
    failed = {
        **widget,
        "live": {**widget["live"], "failed": {"count": 2, "at": "2026-10-25T10:00Z"}},
    }
    assert shown(failed) == {"every": 15, "state": "failed", "failed_at": "2026-10-25T10:00Z"}


# ── Through a turn: the session records, the tool attaches ──────────────


@pytest.fixture
async def rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    runtime = await open_runtime(persona=tmp_path, secrets=MemoryStore(), env={})
    yield runtime
    await runtime.close()


def connect_stripe(rt: Runtime) -> list[tuple[str, JSON]]:
    called: list[tuple[str, JSON]] = []

    async def call(name: str, args: JSON, timeout: float) -> Outcome:
        called.append((name, dict(args)))
        return Outcome(ok=name != "list_disputes", text="MRR $4,200")

    for remote, risk in (
        ("list_charges", Risk.READ),
        ("list_disputes", Risk.READ),
        ("create_refund", Risk.WRITE),
    ):
        rt.registry.register(mcp_tool(remote, risk=risk, call=call), module="trillion.mcp")
    return called


async def test_a_turns_reads_become_the_widgets_live(rt: Runtime) -> None:
    called = connect_stripe(rt)
    rt.provider = ScriptedProvider(
        use("mcp_stripe_list_charges", {"limit": 10}, "c1"),
        use("mcp_stripe_list_disputes", {}, "c2"),  # fails: not remembered
        use("mcp_stripe_create_refund", {"charge": "ch_1"}, "c3"),  # a write: never
        use("web_fetch", {"url": "https://example.com"}, "c4"),
        use(SHOW, {"widgets": [KPIS]}, "c5"),
        say("It's up."),
        use(UPDATE, {"id": "w1", "widget": {**KPIS, "refresh": 30}}, "c6"),
        say("Every half hour."),
    )
    page = Page(rt)
    s = await Session.start(rt, page.send, None)
    await s.handle({"type": "turn.text", "text": "show my revenue"})
    await page.until("turn.complete", 1)
    saved = await rt.board_store.get(s.id, OWNER)
    assert saved is not None
    # Each ran, the write and the failed read too; only the read that worked counts.
    assert [name for name, _ in called] == ["list_charges", "list_disputes", "create_refund"]
    remembered = [{"tool": "mcp_stripe_list_charges", "args": {"limit": 10}}]
    assert saved["widgets"][0]["live"] == {"reads": remembered, "every": 15}
    shown = page.all("board")[-1]["board"]["widgets"][0]
    assert shown["live"] == {"every": 15, "state": "idle"}
    assert "list_charges" not in repr(page.all("board"))
    # The turn is over: its reads are gone.
    assert rt.reads.of(s.id, 1) == []

    # A later turn read nothing: the widget keeps its reads.
    await s.handle({"type": "turn.text", "text": "refresh it every half hour"})
    await page.until("turn.complete", 2)
    saved = await rt.board_store.get(s.id, OWNER)
    assert saved is not None
    assert saved["widgets"][0]["live"] == {"reads": remembered, "every": 30}
    await s.close()


async def test_the_tool_uses_the_calls_turn(rt: Runtime) -> None:
    await rt.conversations.create("c1", OWNER, 1000)
    rt.reads.record("c1", 4, "mcp_stripe_list_charges", {}, "Stripe")

    async def show(turn: int) -> Any:
        ctx = ToolContext(owner_id=OWNER, session_id="c1", turn=turn, call_id="t", via="text")
        done = await rt.router.execute(ToolCall(name=SHOW, args={"widgets": [KPIS]}), ctx)
        assert done.status == "succeeded"
        board = await rt.board_store.get("c1", OWNER)
        assert board is not None
        return board["widgets"][0].get("live")

    assert await show(3) is None
    assert (await show(4))["reads"] == [{"tool": "mcp_stripe_list_charges", "args": {}}]


def test_she_is_told_she_may_set_refresh() -> None:
    from trillion.workspace.schema import type_cards, widget_schema

    assert "refresh? ('off' or minutes)" in type_cards()
    # Minutes may come as text; the schema must not refuse what the kit takes.
    assert set(widget_schema()["properties"]["refresh"]["type"]) == {"string", "number", "null"}
    assert "live" not in widget_schema()["properties"]
