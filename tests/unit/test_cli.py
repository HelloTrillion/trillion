import logging
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from trillion import __version__
from trillion.cli.main import _port_is_free, main


def test_version_flag_prints_version(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"trillion {__version__}"


def test_module_is_runnable():
    out = subprocess.run(
        [sys.executable, "-m", "trillion.cli.main", "--version"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert out.startswith("trillion ")


def test_run_refuses_a_port_that_is_taken(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", 0))
        port = taken.getsockname()[1]
        assert main(["run", "--no-open", "--port", str(port)]) == 2
    assert f"port {port} is already in use" in capsys.readouterr().err
    assert not (tmp_path / "trillion.db").exists()  # refused before anything was opened


@pytest.mark.skipif(sys.platform == "win32", reason="SO_REUSEADDR can take a live port on Windows")
def test_a_port_left_in_time_wait_by_a_stopped_server_counts_as_free() -> None:
    # The server closes first, as Trillion does on Ctrl+C with a page still open: its
    # end of the connection waits about a minute in TIME_WAIT with nothing listening.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]
        server.listen()
        with socket.create_connection(("127.0.0.1", port)):
            conn, _ = server.accept()
            conn.close()
    assert _port_is_free("127.0.0.1", port)


def test_run_refuses_a_grant_that_is_not_a_folder(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert main(["run", "--no-open", "--grant", str(tmp_path / "missing")]) == 2
    assert "not a folder" in capsys.readouterr().err


APP_TOKEN = "Zq" * 21 + "x"  # 43 URL-safe characters, as the desktop app makes


def test_run_refuses_a_malformed_token_from_the_app(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path))
    monkeypatch.setenv("TRILLION_SESSION_TOKEN", "short/one")
    assert main(["run", "--no-open"]) == 2
    out, err = capsys.readouterr()
    assert err == "trillion: TRILLION_SESSION_TOKEN must be at least 32 URL-safe characters\n"
    assert "short/one" not in out + err
    assert not (tmp_path / "trillion.db").exists()  # refused before anything was opened


def test_run_with_the_apps_token_never_prints_it(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import webbrowser

    from trillion.api import runtime
    from trillion.cli import server
    from trillion.secrets.store import MemoryStore

    for name in ("TRILLION_OPENAI_BASE_URL", "TRILLION_MODEL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path))
    monkeypatch.setenv("TRILLION_SESSION_TOKEN", APP_TOKEN)
    real_open = runtime.open_runtime
    held: list[Any] = []

    async def open_without_keychain(**kwargs: Any) -> Any:
        rt = await real_open(**kwargs, persona=tmp_path, secrets=MemoryStore())
        held.append(rt)
        return rt

    async def serve(self: Any, sockets: Any = None) -> None:
        return None

    opened: list[str] = []
    monkeypatch.setattr(runtime, "open_runtime", open_without_keychain)
    monkeypatch.setattr(server.Server, "serve", serve)
    monkeypatch.setattr(webbrowser, "open", opened.append)
    factory = logging.getLogRecordFactory()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    try:
        assert main(["run", "--port", str(port)]) == 0
    finally:
        logging.setLogRecordFactory(factory)
    out, err = capsys.readouterr()
    assert held and held[0].token == APP_TOKEN
    assert "#token=" not in out + err
    assert APP_TOKEN not in out + err
    assert "started by the desktop app" in out
    assert opened == []  # the app shows the page itself


def test_run_explains_a_half_configured_local_model(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRILLION_OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.delenv("TRILLION_MODEL", raising=False)
    assert main(["run", "--no-open"]) == 2
    assert "TRILLION_MODEL" in capsys.readouterr().err


def test_doctor_trace_reads_the_timings_and_changes_nothing(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("TRILLION_HOME", str(home))
    assert main(["doctor", "--trace"]) == 0
    assert "No turns recorded yet" in capsys.readouterr().out
    assert not home.exists()  # measuring made nothing
    assert main(["doctor", "--trace", "0"]) == 2
    assert "1 or more" in capsys.readouterr().err
