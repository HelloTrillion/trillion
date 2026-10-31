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


def test_this_test_run_is_a_source_checkout():
    assert paths.source_checkout()


def test_an_installed_package_is_not_a_source_checkout(monkeypatch, tmp_path):
    site = tmp_path / "lib" / "python3.13" / "site-packages" / "trillion"
    site.mkdir(parents=True)
    monkeypatch.setattr(paths, "package_root", lambda: site)
    assert not paths.source_checkout()
    # A `src/trillion` with no project file above it is not a checkout either.
    stray = tmp_path / "elsewhere" / "src" / "trillion"
    stray.mkdir(parents=True)
    monkeypatch.setattr(paths, "package_root", lambda: stray)
    assert not paths.source_checkout()
    (tmp_path / "elsewhere" / "pyproject.toml").write_text("[project]\n")
    assert paths.source_checkout()
