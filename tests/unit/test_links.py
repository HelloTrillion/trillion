"""The link helper skips only for the missing Windows privilege, and fails on anything else."""

from pathlib import Path

import pytest

from tests.links import symlink_or_skip


class _MissingPrivilege(OSError):
    """Stands in for WinError 1314 on every platform: `winerror` is read-only in some places."""

    winerror = 1314


def _refuse_with(error: OSError):
    def symlink_to(self: Path, target: Path, target_is_directory: bool = False) -> None:
        raise error

    return symlink_to


def test_a_missing_windows_privilege_skips_the_test(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "symlink_to", _refuse_with(_MissingPrivilege("no privilege")))
    with pytest.raises(pytest.skip.Exception):
        symlink_or_skip(tmp_path / "link", tmp_path / "real")


def test_any_other_error_still_fails_the_test(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "symlink_to", _refuse_with(PermissionError("denied")))
    with pytest.raises(PermissionError):
        symlink_or_skip(tmp_path / "link", tmp_path / "real")


def test_a_link_is_made_when_the_account_may_make_one(tmp_path):
    real = tmp_path / "real"
    real.write_text("x")
    symlink_or_skip(tmp_path / "link", real)
    assert (tmp_path / "link").read_text() == "x"
