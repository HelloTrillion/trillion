import subprocess
import sys

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
