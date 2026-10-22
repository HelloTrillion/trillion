"""MCP servers the user connects become tools the spine gates (CON-5)."""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.spine.conftest import Spine
from trillion.connectors.mcp.config import ConfigError, load, parse_server, plain_secrets
from trillion.connectors.mcp.servers import McpServers
from trillion.connectors.mcp.tool import Unusable, schema_for, tool_name
from trillion.secrets.store import MemoryStore
from trillion.spine.confirm import Status
from trillion.spine.contract import Risk

FIXTURE = {"command": sys.executable, "args": ["-m", "tests.mcp.fixture_server"]}
ROOT = Path(__file__).resolve().parents[2]


def write(tmp: Path, servers: dict[str, object]) -> Path:
    path = tmp / "mcp.json"
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return path


@pytest.fixture
async def connected(
    spine: Spine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[McpServers]:
    monkeypatch.chdir(ROOT)  # so `-m tests.mcp.fixture_server` resolves
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    secrets = MemoryStore()
    secrets.set("mcp/fixture", "a-token-of-thirty-two-characters")
    entry = {
        **FIXTURE,
        "env": {"FIXTURE_TOKEN": "keychain:mcp/fixture", "PYTHONPATH": str(ROOT)},
        "tools": {
            "echo": {"risk": "read"},
            "add": {"risk": "read"},
            "token_length": {"risk": "read"},
            "hostile": {"risk": "read"},
        },
        "timeout_s": 2,
    }
    servers = McpServers(spine.registry, secrets, tmp_path / "logs")
    servers.start(load(write(tmp_path, {"fixture": entry})))
    await servers.ready(timeout=30)
    yield servers
    await servers.stop()


# ── The file ─────────────────────────────────────────────────────────────


def test_every_tool_asks_first_unless_the_user_said_otherwise() -> None:
    server = parse_server("x", {"command": "run", "tools": {"list": {"risk": "read"}}})
    assert server.risk_of("list") is Risk.READ
    assert server.risk_of("delete_everything") is Risk.IRREVERSIBLE


def test_an_entry_is_a_program_or_an_address() -> None:
    assert parse_server("a", {"command": "run", "args": ["--x"]}).transport == "stdio"
    assert parse_server("b", {"url": "https://example.com/mcp"}).transport == "http"
    for bad in (
        {},
        {"command": "run", "url": "https://x"},
        {"url": "ftp://x"},
        {"command": "run", "args": [1]},
        {"command": "run", "tools": {"t": {"risk": "maybe"}}},
        {"command": "run", "timeout_s": -1},
    ):
        with pytest.raises(ConfigError):
            parse_server("s", bad)


def test_a_key_is_never_sent_in_the_clear_to_another_machine() -> None:
    with pytest.raises(ConfigError, match="https"):
        parse_server("s", {"url": "http://example.com/mcp"})
    assert parse_server("s", {"url": "http://127.0.0.1:9000/mcp"}).transport == "http"


def test_a_broken_entry_is_reported_and_the_rest_still_load(tmp_path: Path) -> None:
    loaded = load(write(tmp_path, {"good": {"command": "run"}, "bad": {"url": "nope"}}))
    assert [s.name for s in loaded.servers] == ["good"] and "bad" in loaded.problems
    assert load(tmp_path / "missing.json").servers == ()
    (tmp_path / "mcp.json").write_text("{not json", encoding="utf-8")
    assert "mcp.json" in load(tmp_path / "mcp.json").problems


def test_a_key_written_into_the_file_is_pointed_out() -> None:
    server = parse_server(
        "s",
        {
            "url": "https://x.example/mcp",
            "headers": {
                "Authorization": "Bearer abcdefghijklmnopqrstuvwxyz123456",
                "X-Ok": "keychain:mcp/s",
            },
        },
    )
    assert plain_secrets(server) == ["Authorization"]
    assert server.key_names() == ["mcp/s"]


# ── The tools ────────────────────────────────────────────────────────────


def test_names_fit_the_spine_and_never_collide() -> None:
    assert tool_name("GitHub", "search-repos", set()) == "mcp_github_search_repos"
    long = tool_name("server", "x" * 80, set())
    assert len(long) <= 64
    first = tool_name("a", "b", set())
    assert tool_name("a", "b", {first}) != first


def test_a_schema_allows_nothing_the_card_does_not_show() -> None:
    schema, pathy = schema_for({"type": "object", "properties": {"file_path": {"type": "string"}}})
    assert schema["additionalProperties"] is False and pathy == ("file_path",)
    with pytest.raises(Unusable):
        schema_for({"type": "object", "patternProperties": {".*": {}}})
    with pytest.raises(Unusable):
        schema_for({"type": "array"})


# ── Connected, through the spine ─────────────────────────────────────────


async def test_its_tools_appear_with_the_risk_the_user_gave(
    spine: Spine, connected: McpServers
) -> None:
    state = connected.states["fixture"]
    assert state.status == "connected", state.problem
    risks = {t.remote: t.risk for t in state.tools}
    assert risks["echo"] == "read" and risks["save_note"] == "irreversible"
    # The server says hostile() only reads; that is shown, and the user's word decides.
    assert next(t for t in state.tools if t.remote == "save_note").read_only_hint is False
    assert spine.registry.get("mcp_fixture_echo") is not None


async def test_a_read_runs_and_its_answer_is_untrusted(spine: Spine, connected: McpServers) -> None:
    r = await spine.call("mcp_fixture_echo", {"text": "hello"})
    assert r.status == "succeeded" and "echo: hello" in r.to_model()
    hostile = await spine.call("mcp_fixture_hostile", {})
    assert "untrusted" in hostile.to_model()  # wrapped as someone else's words


async def test_anything_else_asks_first_and_runs_only_after_yes(
    spine: Spine, connected: McpServers
) -> None:
    r = await spine.call("mcp_fixture_save_note", {"file_path": "notes/today.md", "text": "hi"})
    assert r.status == "confirmation_required" and r.pending_id
    pending = await spine.store.get_pending(r.pending_id)
    assert pending is not None and "Use fixture's save_note" in pending.summary
    assert "notes/today.md" in pending.summary  # the card shows every argument
    done = await spine.router.resolve(r.pending_id, "confirm", spine.ctx(turn=2, via="tap"))
    assert done.status == "succeeded"


async def test_a_protected_path_is_refused_whichever_tool_asks(
    spine: Spine, connected: McpServers
) -> None:
    r = await spine.call(
        "mcp_fixture_save_note", {"file_path": "~/.ssh/authorized_keys", "text": "x"}
    )
    assert r.status == "refused"


async def test_an_id_that_only_looks_like_a_path_argument_is_fine(
    spine: Spine, connected: McpServers
) -> None:
    r = await spine.call("mcp_fixture_open_doc", {"file_id": "1aBcD-xyz"})
    assert r.status == "confirmation_required"


async def test_its_key_comes_from_the_keychain(spine: Spine, connected: McpServers) -> None:
    r = await spine.call("mcp_fixture_token_length", {})
    assert "32" in r.to_model()


async def test_a_failure_or_a_timeout_is_an_unknown_outcome(
    spine: Spine, connected: McpServers
) -> None:
    for name, args in (("mcp_fixture_fail", {}), ("mcp_fixture_slow", {"seconds": 5})):
        r = await spine.call(name, args)
        done = await spine.router.resolve(
            r.pending_id or "", "confirm", spine.ctx(turn=2, via="tap")
        )
        assert done.status in ("unknown", "failed"), (name, done.status)
        pending = await spine.store.get_pending(r.pending_id or "")
        assert pending is not None and pending.status in (Status.UNKNOWN, Status.FAILED)


async def test_a_server_that_will_not_start_says_why_and_stops_nothing(
    spine: Spine, tmp_path: Path
) -> None:
    servers = McpServers(spine.registry, MemoryStore(), tmp_path / "logs")
    servers.start(
        load(
            write(
                tmp_path,
                {
                    "missing": {"command": "no-such-program-anywhere"},
                    "keyless": {**FIXTURE, "env": {"T": "keychain:mcp/nothing"}},
                    "off": {**FIXTURE, "disabled": True},
                },
            )
        )
    )
    await servers.ready(timeout=10)
    assert servers.states["missing"].status == "failed"
    assert "keychain" in servers.states["keyless"].problem
    assert servers.states["off"].status == "disabled"
    await servers.stop()


def test_doctor_reads_the_file_and_starts_nothing(tmp_path: Path) -> None:
    from trillion.cli.doctor import check_mcp

    secrets = MemoryStore()
    secrets.set("mcp/good", "saved")
    path = write(
        tmp_path,
        {
            "good": {**FIXTURE, "env": {"T": "keychain:mcp/good"}},
            "keyless": {**FIXTURE, "env": {"T": "keychain:mcp/missing"}},
            "gone": {"command": "no-such-program-anywhere"},
            "leaky": {
                "url": "https://x.example/mcp",
                "headers": {"Authorization": "Bearer abcdefghijklmnopqrstuvwx"},
            },
            "broken": {"url": "nope"},
            "off": {**FIXTURE, "disabled": True},
        },
    )
    checks = {c.name + " " + c.status: c for c in check_mcp(path, secrets)}
    assert "MCP: good ok" in checks
    assert "mcp/missing" in checks["MCP: keyless fail"].detail
    assert "MCP: gone warn" in checks and "MCP: leaky warn" in checks
    assert "MCP: broken warn" in checks and "MCP: off info" in checks
    assert not any("abcdefghijklmnop" in c.detail for c in checks.values())  # never the key


def test_a_setting_that_is_not_a_key_is_not_called_one() -> None:
    server = parse_server(
        "s",
        {
            "command": "run",
            "env": {"PYTHONPATH": "/Users/someone/code/project/src", "API_TOKEN": "abcdefgh12345"},
        },
    )
    assert plain_secrets(server) == ["API_TOKEN"]
