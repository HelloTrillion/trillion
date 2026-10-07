import socket
import subprocess
import sys
from pathlib import Path

import pytest

from trillion import __version__
from trillion.cli.main import main


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


def test_run_refuses_a_grant_that_is_not_a_folder(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert main(["run", "--no-open", "--grant", str(tmp_path / "missing")]) == 2
    assert "not a folder" in capsys.readouterr().err


def test_run_explains_a_half_configured_local_model(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRILLION_OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.delenv("TRILLION_MODEL", raising=False)
    assert main(["run", "--no-open"]) == 2
    assert "TRILLION_MODEL" in capsys.readouterr().err
