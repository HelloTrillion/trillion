"""`trillion doctor --seamless`: each measure, plainly, with its target from the spec.
Offline, read-only, and fine with nothing recorded."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from typing import Any

import pytest

from trillion import paths
from trillion.cli import seamless
from trillion.cli.main import main
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.measures_store import (
    Asks,
    FirstRun,
    Measures,
    SqliteMeasuresStore,
    ToolTurns,
)

SPEC_TARGETS = (
    "target: 0 with a model already available, 1 (pick a model) with none",
    "target: under 30, with a model available",
    "target: down by half for someone who uses her daily",
    "target: reported, as the sign of asks that didn't need you",
    "target: most",
)


def test_with_no_database_every_measure_says_not_recorded() -> None:
    out = seamless.report(None)
    assert out.startswith("Seamless: what she asks of you, counted on this computer only.")
    for target in SPEC_TARGETS:
        assert target in out
    assert out.count("not recorded") == 5


def test_with_nothing_recorded_yet() -> None:
    m = Measures(
        first_run=None,
        asks=(Asks(days=7, shown=0, quick=0), Asks(days=28, shown=0, quick=0)),
        tool_turns=ToolTurns(days=28, turns=0, in_time=0),
    )
    out = seamless.report(m)
    assert "Questions before her first reply: not recorded (this install began before" in out
    assert "Asks (cards shown): 0 cards shown in the last 7 days; 0 cards shown in" in out
    assert "nothing changed: 0 of 0 in the last 7 days (-)" in out
    assert "words spoken before the 2-second mark: none recorded in the last 28 days" in out


def test_every_measure_with_its_number() -> None:
    m = Measures(
        first_run=FirstRun(
            opened_at=1_000_000, model_ready=True, questions=3, first_reply_at=1_042_400
        ),
        asks=(Asks(days=7, shown=12, quick=9), Asks(days=28, shown=40, quick=30)),
        tool_turns=ToolTurns(days=28, turns=5, in_time=1),
    )
    lines = seamless.report(m).splitlines()
    assert "Questions before her first reply: 3 (a model was available)" in lines
    assert "Seconds from opening the app to her first reply: 42 (a model was available)" in lines
    assert (
        "Asks (cards shown): 12 cards shown in the last 7 days;"
        " 40 cards shown in the last 28 days, about 10.0 a week"
    ) in lines
    assert (
        "Asks approved within 30 seconds with nothing changed:"
        " 9 of 12 in the last 7 days (75%); 30 of 40 in the last 28 days (75%)"
    ) in lines
    assert (
        "Tool turns over 2 seconds with words spoken before the 2-second mark:"
        " 1 of 5 in the last 28 days (20%), her voice on"
    ) in lines


def test_a_first_run_still_waiting_for_her_reply() -> None:
    f = FirstRun(opened_at=0, model_ready=False, questions=1, first_reply_at=None)
    out = seamless.report(Measures(first_run=f, asks=(), tool_turns=None))
    assert "Questions before her first reply: 1 so far; she hasn't replied yet" in out
    assert "(no model was available)" in out
    assert "to her first reply: not yet: she hasn't replied" in out


# This machine. On Windows an event loop wakes itself through a socket pair, which it
# makes by connecting to itself: that never leaves the machine, so it is allowed.
LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def test_the_command_reads_this_computer_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    left: list[str] = []
    real_lookup, real_connect = socket.getaddrinfo, socket.socket.connect

    def lookup(host: Any, *args: Any, **kw: Any) -> Any:
        name = host.decode() if isinstance(host, bytes) else str(host)
        if name not in LOOPBACK:
            left.append(name)
            raise socket.gaierror(socket.EAI_NONAME, "the network is off")
        return real_lookup(host, *args, **kw)

    def dial(sock: socket.socket, address: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else str(address)
        if sock.family in (socket.AF_INET, socket.AF_INET6) and host not in LOOPBACK:
            left.append(str(host))
            raise OSError("the network is off")
        return real_connect(sock, address)

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr(socket.socket, "connect", dial)
    # Nothing at all yet: no database.
    assert main(["doctor", "--seamless"]) == 0
    assert capsys.readouterr().out.count("not recorded") == 5

    async def record() -> None:
        conn = await connect(paths.db_path())
        await migrate(conn)
        store = SqliteMeasuresStore(conn)
        await store.opened("local", at=1_000, model_ready=True)
        await store.asked("local")
        await store.replied("local", at=9_000)
        await conn.close()

    asyncio.run(record())
    assert main(["doctor", "--seamless"]) == 0
    out = capsys.readouterr().out
    assert "Questions before her first reply: 1 (a model was available)" in out
    assert "Seconds from opening the app to her first reply: 8 (a model was available)" in out
    # Nothing tried to leave this machine.
    assert left == []
