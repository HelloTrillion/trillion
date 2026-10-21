"""Whether the Trillion running is the one on disk (ONB-11)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trillion import build
from trillion.cli import doctor


@pytest.fixture
def package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "trillion"
    (root / "api").mkdir(parents=True)
    (root / "api" / "session.py").write_text("x = 1\n", encoding="utf-8")
    (root / "persona.md").write_text("Be kind.\n", encoding="utf-8")
    monkeypatch.setattr(build.paths, "package_root", lambda: root)
    return root


def test_the_fingerprint_follows_the_code_and_the_page(package: Path, tmp_path: Path) -> None:
    page = tmp_path / "dist"
    page.mkdir()
    (page / "index.html").write_text("<script src=a1.js>", encoding="utf-8")
    before = build.fingerprint(page)
    assert before == build.fingerprint(page)

    (package / "api" / "session.py").write_text("x = 2\n", encoding="utf-8")
    after_code = build.fingerprint(page)
    assert after_code != before

    (page / "index.html").write_text("<script src=b2.js>", encoding="utf-8")
    assert build.fingerprint(page) != after_code


def test_compiled_files_do_not_count(package: Path) -> None:
    before = build.fingerprint(None)
    cache = package / "api" / "__pycache__"
    cache.mkdir()
    (cache / "session.cpython-313.pyc").write_bytes(b"\0")
    (package / "notes.txt").write_text("not code", encoding="utf-8")
    assert build.fingerprint(None) == before


def test_a_running_server_leaves_a_note_and_takes_it_away(package: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    note = build.announce(7790, None, home)
    assert build.read(home) == note and note.pid == os.getpid() and note.port == 7790
    assert "token" not in (home / build.RUNNING).read_text(encoding="utf-8")
    build.withdraw(home)
    assert build.read(home) is None


def test_another_process_note_is_left_alone(package: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    build.announce(7790, None, home)
    text = (home / build.RUNNING).read_text(encoding="utf-8")
    (home / build.RUNNING).write_text(
        text.replace(f'"pid": {os.getpid()}', '"pid": 1'), encoding="utf-8"
    )
    build.withdraw(home)
    assert build.read(home) is not None


def test_a_damaged_note_is_no_note(tmp_path: Path) -> None:
    (tmp_path / build.RUNNING).write_text("{not json", encoding="utf-8")
    assert build.read(tmp_path) is None
    assert build.read(tmp_path / "missing") is None


def test_doctor_says_when_what_runs_is_not_what_is_on_disk(
    package: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    build.announce(7790, None, home)
    monkeypatch.setattr(doctor, "_is_trillion", lambda host, port: port == 7790)

    same = doctor.check_running("127.0.0.1", None, home)
    assert same is not None and same.status == "ok" and "runs the files on disk" in same.detail

    (package / "api" / "session.py").write_text("x = 'fixed'\n", encoding="utf-8")
    changed = doctor.check_running("127.0.0.1", None, home)
    assert changed is not None and changed.status == "warn"
    assert "start it again" in changed.fix


def test_doctor_is_quiet_when_nothing_is_running(
    package: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    assert doctor.check_running("127.0.0.1", None, home) is None
    build.announce(7790, None, home)
    # A note a crashed server left, with nothing answering on its port.
    monkeypatch.setattr(doctor, "_is_trillion", lambda host, port: False)
    assert doctor.check_running("127.0.0.1", None, home) is None
