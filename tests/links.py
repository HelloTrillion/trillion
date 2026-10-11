"""Make links in tests without needing a Windows privilege most accounts lack."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Windows refuses to create a symlink for an account without Developer Mode or admin.
_ERROR_PRIVILEGE_NOT_HELD = 1314


def symlink_or_skip(link: Path, target: Path) -> None:
    """Make `link` a symlink to `target`, or skip the test if Windows lacks the privilege.

    Only that one error skips. Any other failure to make the link is a real problem and
    still fails the test."""
    try:
        link.symlink_to(target)
    except OSError as error:
        if getattr(error, "winerror", None) != _ERROR_PRIVILEGE_NOT_HELD:
            raise
        pytest.skip("this account may not create symlinks (Windows needs Developer Mode)")


def junction(link: Path, target: Path) -> None:
    """Make `link` a Windows junction (a folder link) to `target`.

    Unlike a symlink, a junction needs no special privilege, so these tests run on any
    Windows account. Remove one with `link.rmdir()`, which leaves the target alone."""
    if sys.platform != "win32":
        raise RuntimeError("junctions exist only on Windows")
    import _winapi

    _winapi.CreateJunction(str(target), str(link))
