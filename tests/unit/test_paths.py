from pathlib import Path

from trillion import paths


def test_home_defaults_to_dot_trillion(monkeypatch):
    monkeypatch.delenv("TRILLION_HOME", raising=False)
    assert paths.home() == Path.home() / ".trillion"


def test_home_honours_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "h"))
    assert paths.home() == tmp_path / "h"
    assert paths.db_path() == tmp_path / "h" / "trillion.db"
    assert paths.persona_dir() == tmp_path / "h" / "persona"
    assert paths.secrets_path() == tmp_path / "h" / "secrets.age"
    assert paths.models_dir() == tmp_path / "h" / "models"


def test_package_root_is_the_installed_package():
    assert (paths.package_root() / "paths.py").is_file()
