"""Adding, signing in to, changing and removing MCP servers while Trillion runs (CON-5)."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import httpx2
import pytest

from tests.spine.conftest import Spine
from trillion.connectors.mcp import catalog, paste
from trillion.connectors.mcp.config import ConfigError, change, load, parse_server
from trillion.connectors.mcp.servers import McpServers
from trillion.connectors.mcp.signin import SignIns
from trillion.secrets.store import MemoryStore
from trillion.spine.contract import Risk

ROOT = Path(__file__).resolve().parents[2]
CALLBACK = "http://127.0.0.1:7790/mcp/callback"
FIXTURE = {
    "command": sys.executable,
    "args": ["-m", "tests.mcp.fixture_server"],
    "env": {"PYTHONPATH": str(ROOT)},
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _serve(ttl: int) -> Iterator[str]:
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.mcp.oauth_server", str(port), str(ttl)],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        proc.wait(5)


@pytest.fixture
def signs_in() -> Iterator[str]:
    yield from _serve(ttl=3600)


@pytest.fixture
def expires_fast() -> Iterator[str]:
    yield from _serve(ttl=1)


@pytest.fixture
async def manager(spine: Spine, tmp_path: Path) -> AsyncIterator[McpServers]:
    servers = McpServers(spine.registry, MemoryStore(), tmp_path / "logs")
    servers.callback_url = CALLBACK
    yield servers
    await servers.stop()


async def _until(check: object, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while not check():  # type: ignore[operator]
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.05)


async def _browser(manager: McpServers, name: str) -> None:
    """What the person's browser does: open the sign-in page, allow, and come back."""
    state = manager.states[name]
    await _until(lambda: state.status == "signing_in")
    async with httpx2.AsyncClient() as browser:
        page = await browser.get(state.sign_in_url)
    back = page.headers["location"]
    assert back.startswith(CALLBACK)
    assert manager.sign_ins.arrive(dict(parse_qsl(urlparse(back).query))) == name


# ── Signing in ───────────────────────────────────────────────────────────


async def test_a_server_that_needs_signing_in_waits_for_the_browser(
    spine: Spine, manager: McpServers, signs_in: str
) -> None:
    secrets = manager._secrets
    await manager.connect(parse_server("notes", {"url": signs_in}))
    await _browser(manager, "notes")
    await _until(lambda: manager.states["notes"].status == "connected")
    assert spine.registry.get("mcp_notes_whoami") is not None
    saved = json.loads(secrets.get("mcp/notes/sign-in") or "{}")
    assert saved["tokens"]["access_token"] and saved["client"]["client_id"]

    # Started again, it is still signed in: nobody is asked.
    await manager.connect(parse_server("notes", {"url": signs_in}))
    await manager.ready(name="notes")
    assert manager.states["notes"].status == "connected"


async def test_an_expired_sign_in_is_refreshed_without_asking(
    spine: Spine, manager: McpServers, expires_fast: str
) -> None:
    await manager.connect(parse_server("notes", {"url": expires_fast}))
    await _browser(manager, "notes")
    await _until(lambda: manager.states["notes"].status == "connected")
    first = json.loads(manager._secrets.get("mcp/notes/sign-in") or "{}")["tokens"]
    await asyncio.sleep(1.5)  # the token has expired
    await manager.connect(parse_server("notes", {"url": expires_fast}))
    await manager.ready(name="notes")
    assert manager.states["notes"].status == "connected"
    again = json.loads(manager._secrets.get("mcp/notes/sign-in") or "{}")["tokens"]
    assert again["access_token"] != first["access_token"]


async def test_a_return_nobody_is_waiting_for_is_turned_away() -> None:
    sign_ins = SignIns()
    assert sign_ins.arrive({"state": "made-up", "code": "x"}) is None
    state = sign_ins.expect("notes", "https://auth.example/authorize?state=abc123")
    assert sign_ins.arrive({"state": "wrong", "code": "x"}) is None
    assert sign_ins.arrive({"state": state, "error": "access_denied"}) == "notes"
    with pytest.raises(Exception, match="did not allow"):
        await sign_ins.wait(state)


async def test_disconnecting_while_it_waits_for_a_sign_in_stops_waiting(
    spine: Spine, manager: McpServers, signs_in: str
) -> None:
    await manager.connect(parse_server("notes", {"url": signs_in}))
    await _until(lambda: manager.states["notes"].status == "signing_in")
    await manager.remove("notes")
    assert "notes" not in manager.states and not manager.sign_ins._waiting


# ── Connecting and disconnecting while running ──────────────────────────


async def test_a_server_connects_and_leaves_without_a_restart(
    spine: Spine, manager: McpServers
) -> None:
    config = parse_server("fixture", FIXTURE)
    await manager.connect(config)
    await manager.ready(name="fixture")
    assert manager.states["fixture"].status == "connected"
    assert spine.registry.get("mcp_fixture_echo") is not None
    await manager.remove("fixture")
    assert spine.registry.get("mcp_fixture_echo") is None


async def test_a_new_choice_applies_at_once_and_a_waiting_card_cannot_run(
    spine: Spine, manager: McpServers
) -> None:
    await manager.connect(parse_server("fixture", FIXTURE))
    await manager.ready(name="fixture")
    r = await spine.call("mcp_fixture_echo", {"text": "hi"})
    assert r.status == "confirmation_required"  # asks first by default
    manager.retune(parse_server("fixture", {**FIXTURE, "tools": {"echo": {"risk": "read"}}}))
    assert (await spine.call("mcp_fixture_echo", {"text": "hi"})).status == "succeeded"
    assert spine.registry.definition("mcp_fixture_echo").risk is Risk.READ  # type: ignore[union-attr]

    card = await spine.call("mcp_fixture_save_note", {"file_path": "a.md", "text": "x"})
    await manager.remove("fixture")
    done = await spine.router.resolve(
        card.pending_id or "", "confirm", spine.ctx(turn=2, via="tap")
    )
    assert done.status != "succeeded"


async def test_a_server_that_stops_takes_its_tools_with_it(
    spine: Spine, manager: McpServers
) -> None:
    await manager.connect(parse_server("fixture", FIXTURE))
    await manager.ready(name="fixture")
    await manager.disconnect("fixture")
    assert not [d for d in spine.registry.definitions() if d.name.startswith("mcp_fixture")]


# ── The file ─────────────────────────────────────────────────────────────


def test_the_file_is_changed_whole_and_kept_private(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"kept": {"command": "x"}}, "other": 1}))
    change(path, lambda s: s.__setitem__("new", {"url": "https://n.example/mcp"}))
    raw = json.loads(path.read_text())
    assert set(raw["mcpServers"]) == {"kept", "new"} and raw["other"] == 1
    if os.name != "nt":  # Windows has no owner-only mode bits; its ACLs are another matter
        assert path.stat().st_mode & 0o077 == 0
    assert [s.name for s in load(path).servers] == ["kept", "new"]


def test_a_file_being_edited_by_hand_is_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text("{half written")
    with pytest.raises(ConfigError):
        change(path, lambda s: s.clear())
    assert path.read_text() == "{half written"


# ── Featured servers ─────────────────────────────────────────────────────


def test_a_featured_key_goes_to_the_keychain_and_the_file_only_names_it() -> None:
    raw, secrets = catalog.build(catalog.BY_ID["github"], "github", {"token": " ghp_abc123 "})
    assert raw["headers"] == {"Authorization": "Bearer keychain:mcp/github/token"}
    assert secrets == {"mcp/github/token": "ghp_abc123"}
    assert parse_server("github", raw).key_names() == ["mcp/github/token"]
    with pytest.raises(catalog.CatalogError):
        catalog.build(catalog.BY_ID["github"], "github", {})


def test_every_featured_entry_is_one_trillion_can_use() -> None:
    assert len({e.id for e in catalog.ENTRIES}) == len(catalog.ENTRIES)
    for e in catalog.ENTRIES:
        if e.how in ("google", "slack", "telegram"):
            with pytest.raises(catalog.CatalogError):
                catalog.build(e, e.id, {})
            continue
        raw, _ = catalog.build(e, e.id, {f.id: "a-value-12345" for f in e.fields})
        config = parse_server(e.id, raw)
        assert config.shown == e.title
        # A sign-in entry is an https address on the service's own domain; anything
        # local is a pinned package.
        if e.how == "sign_in":
            assert e.url.startswith("https://") and config.signs_in
        if e.command:
            assert any("@" in a.lstrip("@") for a in e.args), e.id


# ── Pasting ──────────────────────────────────────────────────────────────


def test_a_pasted_claude_desktop_snippet_has_its_key_moved_out() -> None:
    (p,) = paste.read(
        json.dumps(
            {
                "mcpServers": {
                    "Brave Search": {
                        "command": "npx",
                        "args": ["-y", "@brave/brave-search-mcp-server"],
                        "env": {"BRAVE_API_KEY": "BSA-real-key-1234567890"},
                    }
                }
            }
        )
    )
    assert p.name == "brave-search"
    assert p.entry["env"] == {"BRAVE_API_KEY": "keychain:mcp/brave-search/brave_api_key"}
    assert [(s.ref, s.value) for s in p.secrets] == [
        ("mcp/brave-search/brave_api_key", "BSA-real-key-1234567890")
    ]


def test_a_placeholder_key_is_left_for_the_person_to_fill() -> None:
    (p,) = paste.read(
        '"x": {"url": "https://x.example/mcp", "headers": {"Authorization": "Bearer <YOUR_TOKEN>"}}'
    )
    assert p.entry["headers"]["Authorization"] == "Bearer keychain:mcp/x/authorization"
    assert p.secrets[0].value == ""


def test_an_address_or_a_command_is_enough() -> None:
    (url,) = paste.read("https://mcp.linear.app/mcp")
    assert url.name == "linear" and url.entry == {"url": "https://mcp.linear.app/mcp"}
    (cmd,) = paste.read("GITHUB_TOKEN=abc12345678 npx -y @modelcontextprotocol/server-github")
    assert cmd.name == "github" and cmd.entry["command"] == "npx"
    assert cmd.entry["env"]["GITHUB_TOKEN"].startswith("keychain:")
    (vscode,) = paste.read(
        '{"servers": {"docs": {"type": "http", "url": "https://d.example/mcp"}}}'
    )
    assert vscode.entry == {"url": "https://d.example/mcp"}


def test_what_cannot_be_used_says_why() -> None:
    for text, why in (
        ("", "paste"),
        ("{nope", "JSON"),
        ('{"a": 1}', "no server"),
        ("http://example.com/mcp", "https"),
    ):
        with pytest.raises(ConfigError, match=why):
            paste.read(text)


def test_every_featured_service_has_its_icon() -> None:
    icons = ROOT / "apps" / "web" / "public" / "apps"
    for e in catalog.ENTRIES:
        icon = icons / f"{e.id}.png"
        assert icon.is_file(), e.id
        assert icon.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", e.id
    assert {p.stem for p in icons.glob("*.png")} == {e.id for e in catalog.ENTRIES}


async def test_a_listener_that_fails_never_stops_a_change(
    spine: Spine, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    told: list[int] = []

    def fails() -> None:
        told.append(1)
        raise RuntimeError("listener broke")

    servers = McpServers(spine.registry, MemoryStore(), tmp_path / "logs", changed=fails)
    config = parse_server("notes", {**FIXTURE, "disabled": True})
    state = await servers.connect(config)
    assert state.status == "disabled" and len(told) == 1  # told once, not per step
    servers.retune(config)
    await servers.remove("notes")
    assert "notes" not in servers.states and len(told) == 3
    await servers.connect(config)
    await servers.stop()
    assert len(told) == 4  # stopping tells nothing
    assert "MCP change not told: RuntimeError" in caplog.text
    assert "listener broke" not in caplog.text
