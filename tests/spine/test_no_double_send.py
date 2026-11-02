"""OPS-1: a send killed between the provider and the record goes out once.

A child process approves and runs a send through a local stand-in provider and dies
the moment the provider answered, before the record is written. The parent opens the
same database, as a restart would, and checks."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
import urllib.parse
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.spine.conftest import FakeClock
from tests.spine.fakes import HttpSend
from trillion import paths
from trillion.spine.confirm import Status
from trillion.spine.contract import ToolContext
from trillion.spine.deny_floor import DenyPolicy
from trillion.spine.registry import ToolRegistry
from trillion.spine.router import SETTLE_NONE_AFTER_MS, Router
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.spine_store import SqliteSpineStore

REPO = Path(__file__).resolve().parents[2]
CHILD = Path(__file__).with_name("kill_child.py")


class Provider:
    """Counts sends and remembers their tags. Refuses every send when `refuse`."""

    def __init__(self, refuse: bool) -> None:
        self.refuse = refuse
        self.sends = 0
        self.tags: set[str] = set()
        self.lock = threading.Lock()
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if provider.refuse:
                    self._reply(503, {"error": "unavailable"})
                    return
                with provider.lock:
                    provider.sends += 1
                    provider.tags.add(json.loads(body)["tag"])
                self._reply(200, {"ok": True})

            def do_GET(self) -> None:
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                tag = query.get("tag", [""])[0]
                with provider.lock:
                    found = tag in provider.tags
                self._reply(200, {"found": found})

            def _reply(self, code: int, body: dict[str, object]) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: object) -> None:
                pass  # keep test output quiet

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "trillion-home"
    monkeypatch.setenv("TRILLION_HOME", str(home))
    return home


def start_server(refuse: bool = False) -> Iterator[Provider]:
    provider = Provider(refuse)
    try:
        yield provider
    finally:
        provider.close()


@pytest.fixture
def provider() -> Iterator[Provider]:
    yield from start_server()


@pytest.fixture
def refusing_provider() -> Iterator[Provider]:
    yield from start_server(refuse=True)


def run_child(home: Path, url: str, die_after_any_answer: bool = False) -> int:
    argv = [sys.executable, "-I", str(CHILD), str(REPO), url]
    if die_after_any_answer:
        argv.append("--die-after-any-answer")
    env = {**os.environ, "TRILLION_HOME": str(home)}
    done = subprocess.run(argv, env=env, cwd=REPO, timeout=60, capture_output=True, text=True)
    assert done.returncode == 9, done.stderr[-2000:]
    return done.returncode


@contextlib.asynccontextmanager
async def restarted(url: str) -> AsyncIterator[tuple[Router, SqliteSpineStore, FakeClock]]:
    """The parent's view after the crash: the same database, a new router."""
    conn = await connect(paths.db_path())
    try:
        await migrate(conn)
        store = SqliteSpineStore(conn)
        registry = ToolRegistry()
        registry.register(HttpSend(url))
        # The child's clock starts at the same instant, so the record's times line up.
        clock = FakeClock()
        yield Router(registry, store, policy=DenyPolicy.default(), clock=clock), store, clock
    finally:
        await conn.close()


async def test_a_send_killed_after_the_provider_accepted_goes_out_once(
    home: Path, tmp_path: Path, provider: Provider
) -> None:
    run_child(home, provider.url)
    assert provider.sends == 1  # it died between the provider and the record

    async with restarted(provider.url) as (router, store, _):
        [executing] = await store.list_by_status(Status.EXECUTING)
        settled = await router.recover((tmp_path,))
        assert [(s.id, s.outcome) for s in settled] == [(executing.id, "succeeded")]
        a = await store.get_pending(executing.id)
        assert a is not None and a.status is Status.SUCCEEDED
        events = [d.event for d in await store.list_decisions(executing.id)]
        assert events[-1] == "recovered:succeeded"
    assert provider.sends == 1  # recovery never sends


async def test_a_send_the_provider_refused_can_be_asked_for_again(
    home: Path, tmp_path: Path, refusing_provider: Provider
) -> None:
    run_child(home, refusing_provider.url, die_after_any_answer=True)
    assert refusing_provider.sends == 0

    async with restarted(refusing_provider.url) as (router, store, clock):
        [executing] = await store.list_by_status(Status.EXECUTING)
        pid = executing.id
        # "Never went" counts only once the action has been quiet five minutes.
        assert await router.recover((tmp_path,)) == []
        a = await store.get_pending(pid)
        assert a is not None and a.status is Status.EXECUTING
        assert a.effect_status == "unchecked"

        clock.advance(SETTLE_NONE_AFTER_MS)
        settled = await router.check_unchecked()
        assert [(s.id, s.outcome) for s in settled] == [(pid, "not_sent")]
        events = [d.event for d in await store.list_decisions(pid)]
        assert events[-1] == "recovered:not_sent"

        # The session that raised it died; a new one may ask for a fresh card.
        ctx = ToolContext(owner_id="local", session_id="s2", turn=1, call_id="c1", via="text")
        again = await router.represent(pid, ctx)
        assert again.status == "confirmation_required"
    assert refusing_provider.sends == 0
