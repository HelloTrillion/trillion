"""The desktop app's lifeline: the server it started stops when the app is gone (I9)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from types import SimpleNamespace

from trillion.cli.server import stop_when_closed


def test_end_of_input_asks_the_server_to_stop() -> None:
    read, write = os.pipe()
    server = SimpleNamespace(should_exit=False)
    with os.fdopen(read, "rb") as stream:
        thread = stop_when_closed(stream, server)
        os.write(write, b"anything the app might send is ignored\n")
        time.sleep(0.1)
        assert server.should_exit is False
        os.close(write)
        thread.join(timeout=5)
    assert server.should_exit is True


def test_a_process_whose_input_closes_stops() -> None:
    script = (
        "import sys, time, types\n"
        "from trillion.cli.server import stop_when_closed\n"
        "s = types.SimpleNamespace(should_exit=False)\n"
        "stop_when_closed(sys.stdin.buffer, s)\n"
        "print('ready', flush=True)\n"
        "while not s.should_exit: time.sleep(0.02)\n"
        "print('stopped', flush=True)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None and child.stdin is not None
        assert child.stdout.readline().strip() == "ready"
        child.stdin.close()  # the app is gone
        assert child.wait(timeout=10) == 0
        assert child.stdout.read().strip() == "stopped"
    finally:
        if child.poll() is None:
            child.kill()
