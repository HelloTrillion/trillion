"""When she offers a helper (the your helpers spec, Suggestions): the areas her tools
belong to, a need counted on local days in the last 14 in your own chats, and what stops
an offer (a helper that covers it, a no, an offer in the last day)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.scheduler.conftest import NY, ms
from trillion.connectors.mcp.config import ServerConfig
from trillion.connectors.mcp.servers import McpServers, ServerState, ToolInfo
from trillion.helpers import needs
from trillion.helpers.needs import area_of, label, repeated, to_offer
from trillion.secrets.store import MemoryStore
from trillion.spine.audit import AuditRecord
from trillion.spine.registry import ToolRegistry
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.spine_store import SqliteSpineStore

NOW = ms("2026-11-05T09:00")
DAY = 24 * 3600 * 1000
CHAT = {"s1": "chat", "s2": "day", "h1": "helper", "r1": "routine", "x1": "scheduled"}


@dataclass
class Named:
    name: str
    job: str


@pytest.mark.parametrize(
    ("tool", "area"),
    [
        ("gmail_search", "email"),
        ("gmail_send", "email"),
        ("calendar_events", "calendar"),
        ("drive_search", "files"),
        ("file_read", "files"),
        ("stripe_revenue", "payments"),
        ("slack_read", "slack"),
        ("web_fetch", "web"),
        ("memory_write", None),
        ("workspace_show", None),
        ("rule_keep", None),
        ("schedule_list", None),
        ("day_open", None),
        ("helper_ask", None),
        ("stay_quiet", None),
        ("describe_self", None),
        # An MCP tool's area comes from its server, and none is known here.
        ("mcp_github_list_issues", None),
    ],
)
def test_an_area_comes_from_the_tool_name(tool: str, area: str | None) -> None:
    assert area_of(tool) == area


def servers(*listed: tuple[str, str]) -> McpServers:
    """MCP servers listed but not connected: the names they would give their tools."""
    found = McpServers(ToolRegistry(), MemoryStore(), Path("logs"))
    for name, catalog in listed:
        config = ServerConfig(name=name, transport="http", url="https://x.test", catalog=catalog)
        found.states[name] = ServerState(config=config, status="disabled")
    return found


def mcp_areas(found: McpServers) -> needs.McpAreas:
    def area(tool: str) -> str | None:
        server = found.server_of(tool)
        return needs.server_area(server.name, server.catalog) if server is not None else None

    return area


@pytest.mark.parametrize(
    ("tool", "area"),
    [
        ("mcp_google_drive_search", "mcp:google-drive"),
        ("mcp_google_list", "mcp:google"),
        ("mcp_my_server_read", "mcp:my_server"),
        ("mcp_my_other", None),
        ("mcp_pay_stripe_api_read", "payments"),
        ("mcp_unknown_tool", None),
        ("gmail_search", "email"),
    ],
)
def test_an_mcp_tools_area_is_its_servers(tool: str, area: str | None) -> None:
    found = servers(("google-drive", ""), ("google", ""), ("my_server", ""), ("pay", "stripe"))
    assert area_of(tool, mcp_areas(found)) == area


def test_a_connected_servers_own_tools_name_it() -> None:
    found = servers(("a", ""), ("a_b", ""))
    # "mcp_a_b_x" could be either: the server that registered it says which.
    found.states["a"].tools.append(
        ToolInfo(name="mcp_a_b_x", remote="b_x", description="", risk="read", read_only_hint=True)
    )
    assert area_of("mcp_a_b_x", mcp_areas(found)) == "mcp:a"
    assert area_of("mcp_a_b_y", mcp_areas(found)) == "mcp:a_b"


def test_an_area_reads_as_words() -> None:
    assert label("email") == "email"
    assert label("slack") == "Slack"
    assert label("mcp:github") == "github"
    assert label("mcp:google-drive") == "google-drive"


def at(local: str) -> int:
    return ms(local, NY)


def test_three_local_days_in_fourteen_not_three_calls() -> None:
    many_one_day = [("gmail_search", "s1", at("2026-11-04T09:00"))] * 5
    assert repeated(many_one_day, CHAT, NY, NOW) == []
    three_days = [
        ("gmail_search", "s1", at("2026-10-23T09:00")),
        ("gmail_read", "s2", at("2026-11-01T09:00")),
        ("gmail_send", "s1", at("2026-11-05T08:00")),
    ]
    assert repeated(three_days, CHAT, NY, NOW) == ["email"]


def test_days_are_the_users_local_days() -> None:
    # 18:00 and 20:00 in New York are one day there, though two days in UTC.
    calls = [
        ("web_fetch", "s1", at("2026-11-02T18:00")),
        ("web_fetch", "s1", at("2026-11-02T20:00")),
        ("web_fetch", "s1", at("2026-11-04T09:00")),
    ]
    assert repeated(calls, CHAT, "UTC", NOW) == ["web"]
    assert repeated(calls, CHAT, NY, NOW) == []
    calls.append(("web_fetch", "s1", at("2026-11-03T23:30")))
    assert repeated(calls, CHAT, NY, NOW) == ["web"]


def test_older_than_fourteen_days_does_not_count() -> None:
    calls = [
        ("slack_read", "s1", at("2026-10-22T09:00")),
        ("slack_read", "s1", at("2026-10-30T09:00")),
        ("slack_read", "s1", at("2026-11-05T08:00")),
    ]
    assert repeated(calls, CHAT, NY, NOW) == []


@pytest.mark.parametrize("session", ["h1", "r1", "x1", "gone"])
def test_helper_and_run_conversations_do_not_count(session: str) -> None:
    calls = [
        ("gmail_search", "s1", at("2026-11-01T09:00")),
        ("gmail_search", "s1", at("2026-11-02T09:00")),
        ("gmail_search", session, at("2026-11-03T09:00")),
    ]
    assert repeated(calls, CHAT, NY, NOW) == []


def test_her_own_tools_never_count() -> None:
    calls = [("memory_write", "s1", NOW - d * DAY) for d in range(5)]
    assert repeated(calls, CHAT, NY, NOW) == []


def test_the_area_used_on_most_days_comes_first() -> None:
    calls = [("web_fetch", "s1", NOW - d * DAY) for d in range(3)]
    calls += [("calendar_events", "s1", NOW - d * DAY) for d in range(5)]
    assert repeated(calls, CHAT, NY, NOW) == ["calendar", "web"]


def test_an_offer_is_the_first_area_not_covered_or_declined() -> None:
    areas = ["email", "payments", "web"]
    assert to_offer(areas, [], [], None, NOW) == "email"
    assert to_offer(areas, [], ["email"], None, NOW) == "payments"
    inbox = Named("Inbox", "Keeps the user's mail in order.")
    assert to_offer(areas, [inbox], [], None, NOW) == "payments"
    invoices = Named("Chaser", "Chases late invoices.")
    assert to_offer(areas, [inbox, invoices], [], None, NOW) == "web"
    assert to_offer(areas, [inbox, invoices], ["web"], None, NOW) is None


def test_covered_is_a_word_not_part_of_one() -> None:
    # "Prevented" holds "event", but not as a word of its own.
    assert to_offer(["calendar"], [Named("Guard", "Prevented things.")], [], None, NOW)
    assert to_offer(["calendar"], [Named("Diary", "Watches my events.")], [], None, NOW) is None


def test_an_mcp_area_is_covered_by_its_server_name() -> None:
    gh = Named("GitHub", "Triages new issues.")
    assert to_offer(["mcp:github"], [gh], [], None, NOW) is None
    assert to_offer(["mcp:linear"], [gh], [], None, NOW) == "mcp:linear"
    drive = Named("Docs", "Tidies my Google Drive.")
    assert to_offer(["mcp:google-drive"], [drive], [], None, NOW) is None


def test_at_most_one_offer_a_day() -> None:
    assert to_offer(["email"], [], [], NOW - DAY + 1000, NOW) is None
    assert to_offer(["email"], [], [], NOW - DAY, NOW) == "email"


def test_the_window_reaches_back_fourteen_days() -> None:
    assert needs.since(NOW) <= NOW - 14 * DAY


async def test_recent_tools_are_the_allowed_calls_since(tmp_path: Path) -> None:
    conn = await connect(tmp_path / "t.db")
    await migrate(conn)
    store = SqliteSpineStore(conn)

    def row(n: int, tool: str, decision: str, at: int) -> AuditRecord:
        return AuditRecord(
            owner_id="local",
            call_id=f"c{n}",
            tool=tool,
            args_hash="h",
            risk="read",
            decision=decision,
            outcome="succeeded",
            duration_ms=1,
            session_id="s1",
            at=at,
        )

    await store.append_audit(row(1, "gmail_search", "executed", NOW - 20 * DAY))
    await store.append_audit(row(2, "gmail_search", "executed", NOW - DAY))
    await store.append_audit(row(3, "gmail_send", "executed", NOW - 1000))
    await store.append_audit(row(6, "gmail_draft", "proposed", NOW - 900))
    await store.append_audit(row(4, "slack_read", "refused", NOW - 500))
    await store.append_audit(row(5, "web_fetch", "executed", NOW))
    got = await store.recent_tools("local", NOW - 14 * DAY)
    assert sorted(got) == sorted(
        [
            ("gmail_search", "s1", NOW - DAY),
            ("gmail_send", "s1", NOW - 1000),
            ("web_fetch", "s1", NOW),
        ]
    )
    assert await store.recent_tools("other", NOW - 14 * DAY) == []
    await conn.close()
