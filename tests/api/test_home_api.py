"""Home (SUR-1a): /api/workspace/home says what each briefing tile can show, from what
is set up, without reading a connector or calling out."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

import socket
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_server import App, home, make  # noqa: F401
from trillion import paths
from trillion.api.runtime import OWNER
from trillion.connectors.google import config
from trillion.connectors.google.config import Account
from trillion.connectors.mcp.config import parse_server
from trillion.connectors.mcp.servers import ServerState, ToolInfo
from trillion.connectors.slack import USER_TOKEN
from trillion.persona import day
from trillion.spine.contract import JSON, ActionSummary, BaseTool, Risk, ToolDefinition

PATH = "/api/workspace/home"
NOW = 1_793_000_000_000


class _Read(BaseTool):
    def __init__(self, name: str) -> None:
        self.definition = ToolDefinition(
            name=name,
            description="A read.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            risk=Risk.READ,
            category="mcp",
            timeout_s=5.0,
        )

    def describe(self, args: JSON) -> ActionSummary:
        return ActionSummary(text="Read the list")


@pytest.fixture
def app(make: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> App:
    # No Google app unless a test gives one: never the copy bundled with this checkout.
    monkeypatch.setattr(config, "BUNDLED", tmp_path / "no-client.json")
    built: App = make()
    built.rt.clock = lambda: NOW
    return built


def get(app: App) -> dict[str, Any]:
    r = app.client.get(PATH, headers=app.auth)
    assert r.status_code == 200
    return r.json()  # type: ignore[no-any-return]


def tile(out: dict[str, Any], tile_id: str) -> dict[str, Any]:
    (found,) = [t for t in out["tiles"] if t["id"] == tile_id]
    return found


def service(out: dict[str, Any], tile_id: str, service_id: str) -> dict[str, Any]:
    (found,) = [s for s in tile(out, tile_id)["services"] if s["id"] == service_id]
    return found


def add_server(app: App, name: str, catalog: str, *, disabled: bool = False) -> None:
    rt = app.rt
    assert rt.mcp is not None
    tool = f"mcp_{name.replace('-', '_')}_list"
    rt.registry.register(_Read(tool))
    raw: dict[str, Any] = {"url": "https://x.example/mcp", "catalog": catalog}
    if disabled:
        raw["disabled"] = True
    info = ToolInfo(name=tool, remote="list", description="", risk="read", read_only_hint=True)
    rt.mcp.states[name] = ServerState(config=parse_server(name, raw), tools=[info])


def test_it_needs_the_session_token(app: App) -> None:
    assert app.client.get(PATH).status_code == 401


def test_the_shape_with_nothing_set_up(app: App) -> None:
    out = get(app)
    assert set(out) == {
        "user_name",
        "agent_name",
        "briefing",
        "ready",
        "total",
        "next",
        "tiles",
        "removed",
    }
    assert out["removed"] == []
    assert out["briefing"] == "build" and (out["ready"], out["total"]) == (1, 9)
    assert [t["id"] for t in out["tiles"]] == [
        "next_up",
        "free_time",
        "inbox",
        "tasks",
        "mentions",
        "due_soon",
        "kpis",
        "files",
        "checks",
    ]
    assert set(out["tiles"][0]) == {"id", "title", "type", "what", "state", "services", "more"}
    assert set(out["tiles"][0]["services"][0]) == {
        "id",
        "title",
        "color",
        "glyph",
        "how",
        "category",
        "state",
        "settings_key",
    }
    # No Google app yet: Slack is the one-click sign-in to suggest.
    assert out["next"] == {
        "tile": "mentions",
        "service": "slack",
        "text": "Connect Slack next: one sign-in adds Mentions",
    }
    # Looking does not make today's day conversation.
    assert app.rows("SELECT count(*) FROM conversations") == [(0,)]


def test_google_counts_as_one_click_once_its_app_is_set_up(app: App) -> None:
    app.secrets.set(config.CLIENT_KEY, '{"id": "9-x.apps.example", "secret": "s"}')
    assert get(app)["next"]["service"] == "google-calendar"


def test_google_and_slack_set_up(app: App) -> None:
    rt = app.rt
    assert rt.google is not None and rt.slack is not None
    rt.google.saved.accounts.append(
        Account(email="sam@example.com", services=["calendar", "gmail", "drive"])
    )
    rt.google.refresh_tools()
    rt.google.set_free({"calendar_events": "read", "calendar_busy": "read"})
    app.secrets.set(USER_TOKEN, "xoxp-test")
    rt.slack.refresh_tools()
    out = get(app)
    assert tile(out, "next_up")["state"] == "ready"
    assert service(out, "next_up", "google-calendar")["settings_key"] == "google:calendar"
    assert tile(out, "inbox")["state"] == "asks_first"
    assert service(out, "inbox", "gmail")["settings_key"] == "google:gmail"
    assert tile(out, "files")["state"] == "asks_first"
    assert service(out, "files", "google-drive")["settings_key"] == "google:drive"
    assert tile(out, "mentions")["state"] == "asks_first"
    assert service(out, "mentions", "slack")["settings_key"] == "chat:slack"
    assert out["ready"] == 3


def test_a_server_that_is_on_makes_its_tile_ready(app: App) -> None:
    add_server(app, "my-linear", "linear")
    out = get(app)
    assert tile(out, "due_soon")["state"] == "ready"
    linear = service(out, "due_soon", "linear")
    assert (linear["state"], linear["settings_key"]) == ("ready", "my-linear")
    assert out["ready"] == 2


def test_a_server_that_is_off_or_disabled_does_not(app: App) -> None:
    add_server(app, "my-stripe", "stripe", disabled=True)
    out = get(app)
    assert tile(out, "kpis")["state"] == "connect"
    assert service(out, "kpis", "stripe")["settings_key"] == ""
    # One without a catalogue entry feeds no tile.
    add_server(app, "my-notes", "")
    assert get(app)["ready"] == 1


async def _day(app: App) -> str:
    date = day.today(NOW, app.rt.zone())
    cid, _ = await app.rt.conversations.day_conversation(
        OWNER, date.isoformat(), day.title(date), NOW
    )
    return cid


def test_briefing_is_open_only_when_todays_board_has_widgets(app: App) -> None:
    portal = app.client.portal
    assert portal is not None
    cid = portal.call(_day, app)
    assert get(app)["briefing"] == "build"
    empty = {"title": "Your day", "open": True, "next_id": 1, "widgets": []}
    portal.call(app.rt.board_store.put, cid, OWNER, empty, NOW)
    assert get(app)["briefing"] == "build"
    full = empty | {"next_id": 2, "widgets": [{"id": "w1", "type": "note", "text": "Hi"}]}
    portal.call(app.rt.board_store.put, cid, OWNER, full, NOW)
    assert get(app)["briefing"] == "open"
    # Yesterday's board is not today's briefing.
    app.rt.clock = lambda: NOW + 86_400_000
    assert get(app)["briefing"] == "build"


def test_it_makes_no_network_call(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a network call")

    rt = app.rt
    assert rt.google is not None
    rt.google.saved.accounts.append(Account(email="sam@example.com", services=["gmail"]))
    app.secrets.set(USER_TOKEN, "xoxp-test")
    add_server(app, "my-sentry", "sentry")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    assert get(app)["total"] == 9


def test_a_deleted_day_is_not_a_built_briefing(app: App) -> None:
    portal = app.client.portal
    assert portal is not None
    cid = portal.call(_day, app)
    board = {"title": "Your day", "open": True, "next_id": 2, "widgets": [{"id": "w1"}]}
    portal.call(app.rt.board_store.put, cid, OWNER, board, NOW)
    assert get(app)["briefing"] == "open"
    assert portal.call(app.rt.conversations.delete, cid, OWNER, NOW)
    assert get(app)["briefing"] == "build"
    # Looking made no fresh day in its place.
    assert app.rows("SELECT count(*) FROM conversations WHERE kind = 'day'") == [(1,)]


def test_a_server_still_starting_is_to_connect_with_its_card(app: App) -> None:
    assert app.rt.mcp is not None
    raw = {"url": "https://x.example/mcp", "catalog": "sentry"}
    app.rt.mcp.states["my-sentry"] = ServerState(config=parse_server("my-sentry", raw), tools=[])
    out = get(app)
    assert tile(out, "checks")["state"] == "connect"
    sentry = service(out, "checks", "sentry")
    assert (sentry["state"], sentry["settings_key"]) == ("connect", "my-sentry")


# --- Arranging Home (D13, D14) -----------------------------------------------------

LAYOUT = PATH + "/layout"
IDS = ["next_up", "free_time", "inbox", "tasks", "mentions", "due_soon", "kpis", "files", "checks"]


def put(app: App, body: Any) -> dict[str, Any]:
    r = app.client.put(LAYOUT, json=body, headers=app.auth)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def test_putting_the_layout_needs_the_session_token(app: App) -> None:
    assert app.client.put(LAYOUT, json={"order": [], "removed": []}).status_code == 401


def test_put_saves_the_layout_and_answers_with_home(app: App) -> None:
    out = put(app, {"order": ["kpis", "inbox"], "removed": ["checks", "inbox"]})
    rest = [i for i in IDS if i not in ("kpis", "inbox", "checks")]
    assert [t["id"] for t in out["tiles"]] == ["kpis", *rest]
    assert [t["id"] for t in out["removed"]] == ["inbox", "checks"]
    assert out["removed"][0]["services"][0]["id"] == "gmail"
    assert (out["ready"], out["total"]) == (1, 7)
    # Kept by the core: the route answers the same from now on.
    assert get(app) == out
    portal = app.client.portal
    assert portal is not None
    saved = portal.call(app.rt.preferences.get, OWNER, "home_layout")
    assert saved == {"order": ["kpis", "inbox", *rest, "checks"], "removed": ["inbox", "checks"]}


def test_put_cleans_what_it_is_given(app: App) -> None:
    out = put(app, {"order": ["weather", "files", "files"], "removed": ["nope"]})
    assert [t["id"] for t in out["tiles"]] == ["files", *[i for i in IDS if i != "files"]]
    assert out["removed"] == []
    assert [t["id"] for t in put(app, {"order": [], "removed": ["kpis"]})["removed"]] == ["kpis"]


@pytest.mark.parametrize(
    "body",
    [
        {"order": "kpis", "removed": []},
        {"order": [1, 2], "removed": []},
        {"order": [], "removed": ["x" * 65]},
        {"order": ["kpis"] * 51, "removed": []},
        {"order": [], "removed": [], "extra": True},
        # Both lists are needed: an empty body never resets Home.
        {},
        {"order": IDS},
        {"removed": ["kpis"]},
    ],
)
def test_put_refuses_what_is_not_a_layout(app: App, body: Any) -> None:
    assert app.client.put(LAYOUT, json=body, headers=app.auth).status_code == 422
    assert get(app)["removed"] == []


def test_the_route_after_a_removal_counts_only_the_tiles_shown(app: App) -> None:
    add_server(app, "my-linear", "linear")
    assert (get(app)["ready"], get(app)["total"]) == (2, 9)
    out = put(app, {"order": IDS, "removed": ["tasks", "due_soon", "mentions"]})
    assert (out["ready"], out["total"]) == (0, 6)
    # Slack's tile is off Home, so it is never the one to suggest.
    assert out["next"] is not None and out["next"]["service"] != "slack"


def test_put_tells_every_open_page(app: App) -> None:
    with app.connect() as one, app.connect() as two:
        put(app, {"order": ["checks"], "removed": []})
        for chat in (one, two):
            assert chat.until("home")[-1] == {"type": "home"}


def test_a_stored_layout_that_is_not_json_is_the_default(app: App) -> None:
    put(app, {"order": ["checks"], "removed": ["kpis"]})
    with closing(sqlite3.connect(paths.db_path())) as db, db:
        db.execute("UPDATE preferences SET value_json = '{not json' WHERE key = 'home_layout'")
    out = get(app)
    assert [t["id"] for t in out["tiles"]] == IDS and out["removed"] == []
    # And it can be saved over.
    assert [t["id"] for t in put(app, {"order": [], "removed": ["kpis"]})["removed"]] == ["kpis"]
