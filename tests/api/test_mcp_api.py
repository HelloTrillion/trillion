"""Settings → Connected tools reads /api/mcp: what is connected, never a key (CON-5)."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from tests.api.test_server import App, home, make  # noqa: F401
from trillion import paths
from trillion.connectors.mcp.config import parse_server
from trillion.connectors.mcp.servers import ServerState, ToolInfo

PATH = "/api/mcp"
JSON = {"content-type": "application/json"}


def test_it_lists_servers_their_tools_and_whether_each_asks(make: Any) -> None:
    app: App = make()
    app.rt.secrets.set("mcp/notes", "the-actual-secret-value-123456")
    config = parse_server(
        "notes",
        {
            "url": "https://notes.example/mcp?key=abc",
            "headers": {"Authorization": "Bearer keychain:mcp/notes"},
            "tools": {"list": {"risk": "read"}},
        },
    )
    assert app.rt.mcp is not None
    app.rt.mcp.states["notes"] = ServerState(
        config=config,
        status="connected",
        tools=[
            ToolInfo(
                name="mcp_notes_list",
                remote="list",
                description="List notes",
                risk="read",
                read_only_hint=True,
            ),
            ToolInfo(
                name="mcp_notes_delete",
                remote="delete",
                description="Delete a note",
                risk="irreversible",
                read_only_hint=True,
            ),
        ],
    )
    got = app.client.get(PATH, headers=app.auth)
    assert got.status_code == 200
    (server,) = got.json()["servers"]
    assert server["status"] == "connected" and server["runs"] == "https://notes.example/mcp"
    tools = {t["name"]: t for t in server["tools"]}
    assert tools["list"]["asks_first"] is False
    # The server calls delete read-only; it still asks, and the page can say so.
    assert tools["delete"]["asks_first"] is True and tools["delete"]["says_read_only"] is True
    assert server["keys"] == [{"name": "mcp/notes", "saved": True}]
    assert "the-actual-secret-value" not in got.text and "key=abc" not in got.text


def test_it_needs_the_session_token(make: Any) -> None:
    app: App = make()
    assert app.client.get(PATH).status_code == 401


# ── Adding, changing and removing from Settings ──────────────────────────

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_TEXT = json.dumps(
    {
        "mcpServers": {
            "My Notes": {
                "command": sys.executable,
                "args": ["-m", "tests.mcp.fixture_server"],
                "env": {
                    "PYTHONPATH": str(ROOT),
                    "FIXTURE_TOKEN": "a-pasted-token-of-32-characters!",
                },
            }
        }
    }
)


def _add_pasted(app: App) -> dict[str, Any]:
    parsed = app.client.post(f"{PATH}/parse", json={"text": FIXTURE_TEXT}, headers=app.auth)
    assert parsed.status_code == 200, parsed.text
    (p,) = parsed.json()["servers"]
    assert p["entry"]["env"]["FIXTURE_TOKEN"] == "keychain:mcp/my-notes/fixture_token"  # noqa: S105
    added = app.client.post(
        f"{PATH}/servers",
        json={
            "entry": p["entry"],
            "proposed": p["name"],
            "title": "My notes",
            "secrets": {s["ref"]: s["value"] for s in p["secrets"]},
        },
        headers=app.auth,
    )
    assert added.status_code == 200, added.text
    return dict(added.json())


def test_a_pasted_server_is_added_connected_and_its_key_kept_out_of_the_file(
    make: Any,
) -> None:
    app: App = make()
    row = _add_pasted(app)
    assert row["status"] == "connected", row["problem"]
    assert row["title"] == "My notes" and row["name"] == "my-notes"
    assert app.secrets.get("mcp/my-notes/fixture_token") == "a-pasted-token-of-32-characters!"
    text = paths.mcp_config_path().read_text()
    assert "a-pasted-token" not in text and "keychain:mcp/my-notes/fixture_token" in text
    assert app.rt.registry.get("mcp_my_notes_echo") is not None
    listed = app.client.get(PATH, headers=app.auth).json()
    assert [s["name"] for s in listed["servers"]] == ["my-notes"]
    assert "a-pasted-token" not in json.dumps(listed)
    assert any(e["id"] == "notion" for e in listed["catalog"])


def test_letting_a_tool_run_without_asking_is_saved_and_applies_at_once(make: Any) -> None:
    app: App = make()
    _add_pasted(app)
    got = app.client.patch(
        f"{PATH}/servers/my-notes", json={"tools": {"echo": "read"}}, headers=app.auth
    )
    assert got.status_code == 200, got.text
    tools = {t["name"]: t for t in got.json()["tools"]}
    assert tools["echo"]["asks_first"] is False and tools["add"]["asks_first"] is True
    assert '"echo"' in paths.mcp_config_path().read_text()
    back = app.client.patch(
        f"{PATH}/servers/my-notes", json={"tools": {"echo": "ask"}}, headers=app.auth
    )
    assert {t["name"]: t for t in back.json()["tools"]}["echo"]["asks_first"] is True
    bad = app.client.patch(
        f"{PATH}/servers/my-notes", json={"tools": {"echo": "spend"}}, headers=app.auth
    )
    assert bad.status_code == 400


def test_turning_off_and_removing_take_its_tools_and_its_keys(make: Any) -> None:
    app: App = make()
    _add_pasted(app)
    off = app.client.patch(f"{PATH}/servers/my-notes", json={"disabled": True}, headers=app.auth)
    assert off.json()["status"] == "disabled"
    assert app.rt.registry.get("mcp_my_notes_echo") is None
    on = app.client.patch(f"{PATH}/servers/my-notes", json={"disabled": False}, headers=app.auth)
    assert on.json()["status"] == "connected"
    gone = app.client.delete(f"{PATH}/servers/my-notes", headers={**app.auth, **JSON})
    assert gone.status_code == 200
    assert app.rt.registry.get("mcp_my_notes_echo") is None
    assert app.secrets.get("mcp/my-notes/fixture_token") is None
    assert "my-notes" not in paths.mcp_config_path().read_text()


def test_a_featured_server_needs_its_fields_and_is_added_once(make: Any) -> None:
    app: App = make()
    missing = app.client.post(f"{PATH}/servers", json={"catalog": "github"}, headers=app.auth)
    assert missing.status_code == 400 and "token" in missing.json()["detail"]
    soon = app.client.post(f"{PATH}/servers", json={"catalog": "gmail"}, headers=app.auth)
    assert soon.status_code == 400
    assert (
        app.client.post(f"{PATH}/servers", json={"catalog": "nope"}, headers=app.auth).status_code
        == 404
    )


def test_a_key_can_only_be_saved_under_its_own_server(make: Any) -> None:
    app: App = make()
    added = app.client.post(
        f"{PATH}/servers",
        json={
            "entry": {
                "url": "https://x.example/mcp",
                "headers": {"Authorization": "Bearer keychain:mcp/x/authorization"},
            },
            "proposed": "x",
            "secrets": {"mcp/x/authorization": "tok-123456789", "model/anthropic": "stolen"},
        },
        headers=app.auth,
    )
    assert added.status_code == 200
    assert app.secrets.get("model/anthropic") is None


def test_the_sign_in_page_hands_a_code_only_to_a_sign_in_that_waits(make: Any) -> None:
    app: App = make()
    stale = app.client.get("/mcp/callback?state=nobody&code=x")
    assert stale.status_code == 400 and "expired" in stale.text

    from trillion.api.app import create_app, local_hosts

    assert app.rt.mcp is not None
    create_app(app.rt, hosts=local_hosts(7790))
    assert app.rt.mcp.callback_url == "http://127.0.0.1:7790/mcp/callback"


def test_writes_need_the_session_token(make: Any) -> None:
    app: App = make()
    assert (
        app.client.post(f"{PATH}/parse", json={"text": "https://x.example/mcp"}).status_code == 401
    )
    assert app.client.delete(f"{PATH}/servers/x").status_code == 401


def test_the_picker_page_has_its_own_policy_and_refuses_a_bad_ticket(make: Any) -> None:
    app: App = make()
    bad = app.client.get("/google/picker?ticket=nope")
    assert bad.status_code == 400 and "expired" in bad.text
    # Every other page keeps the strict policy.
    assert (
        "apis.google.com" not in app.client.get("/mcp/callback").headers["content-security-policy"]
    )
    assert (
        app.client.post("/google/picked", json={"ticket": "nope", "files": []}).status_code == 409
    )


def test_after_signing_in_the_tab_closes_itself_and_links_back_to_the_card() -> None:
    from trillion.api.mcp import _page, show_link

    assert show_link("google:gmail") == "/#show=google:gmail"
    page = _page("Signed in to Gmail", "Done.", back=show_link("google:gmail"), close=True)
    text = bytes(page.body).decode()
    assert 'href="/#show=google:gmail"' in text and "window.close()" in text
    nonce = page.headers["content-security-policy"].split("'nonce-")[1].split("'")[0]
    assert f'nonce="{nonce}"' in text  # only this page's own script may run
    assert "window.close" not in bytes(_page("Expired", "Try again.").body).decode()
