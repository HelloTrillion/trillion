"""#2 and TESTING.md 1.3.2: a folder swapped for a link at the last moment changes nothing.

The floor checks a path when an action is approved; the tool opens it a moment
later. These tests win that race for the attacker: right after the tool has
worked out the path and right before it opens anything, a folder on the way is
replaced by a link to a folder outside the grant. Nothing may be read from or
written to the outside folder.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.links import symlink_or_skip
from tests.spine.conftest import Spine
from trillion.tools import beneath

pytestmark = pytest.mark.skipif(not beneath.WALKS, reason="the walk needs dir_fd (POSIX)")


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """A folder nobody granted, holding something worth stealing."""
    d = tmp_path / "outside"
    d.mkdir()
    (d / "secret.txt").write_text("the attacker wants this\n")
    (d / "log.txt").write_text("old\n")
    return d


def swap_before_open(monkeypatch: pytest.MonkeyPatch, sub: Path, to: Path) -> Callable[[], int]:
    """Replace `sub` with a link to `to` the instant before the walk starts."""
    real = beneath._walk
    swaps: list[int] = []

    def racing(top: Path, names: Any) -> int:
        if not swaps and sub.is_dir() and not sub.is_symlink():
            sub.rename(sub.with_name(sub.name + ".moved"))
            symlink_or_skip(sub, to)
            swaps.append(1)
        return real(top, names)

    monkeypatch.setattr(beneath, "_walk", racing)
    return lambda: len(swaps)


async def approve(spine: Spine, tool: str, args: dict[str, Any]) -> Any:
    """Propose, then approve with a tap: overwrite and edit can only be approved that way."""
    r = await spine.call(tool, args)
    assert r.status == "confirmation_required", r
    return await spine.router.resolve(r.pending_id or "", "confirm", spine.ctx(turn=2, via="tap"))


def untouched(outside: Path) -> None:
    assert sorted(p.name for p in outside.iterdir()) == ["log.txt", "secret.txt"]
    assert (outside / "secret.txt").read_text() == "the attacker wants this\n"
    assert (outside / "log.txt").read_text() == "old\n"


@pytest.mark.parametrize(
    ("tool", "name", "args"),
    [
        ("file_create", "new.txt", {"content": "planted"}),
        ("file_overwrite", "secret.txt", {"content": "planted"}),
        ("file_append", "log.txt", {"content": "planted"}),
        ("file_edit", "log.txt", {"old": "old", "new": "planted"}),
    ],
)
async def test_a_write_never_follows_a_folder_swapped_in_at_the_last_moment(
    spine: Spine,
    outside: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    name: str,
    args: dict[str, Any],
) -> None:
    sub = spine.granted / "sub"
    sub.mkdir()
    (sub / "secret.txt").write_text("mine\n")
    (sub / "log.txt").write_text("old\n")
    swapped = swap_before_open(monkeypatch, sub, outside)
    res = await approve(spine, tool, {"path": str(sub / name), **args})
    assert swapped() == 1  # the attacker did win the race
    assert res.status == "failed", res
    untouched(outside)


async def test_a_read_never_follows_a_folder_swapped_in_at_the_last_moment(
    spine: Spine, outside: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sub = spine.granted / "sub"
    sub.mkdir()
    (sub / "secret.txt").write_text("mine\n")
    swapped = swap_before_open(monkeypatch, sub, outside)
    r = await spine.call("file_read", {"path": str(sub / "secret.txt")})
    assert swapped() == 1
    assert r.status == "failed" and "attacker" not in r.content


async def test_a_listing_never_follows_a_folder_swapped_in_at_the_last_moment(
    spine: Spine, outside: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sub = spine.granted / "sub"
    (sub / "inner").mkdir(parents=True)
    swapped = swap_before_open(monkeypatch, sub, outside)
    r = await spine.call("file_list", {"path": str(sub / "inner")})
    assert swapped() == 1
    assert r.status == "failed" and "secret.txt" not in r.content


async def test_a_link_the_user_already_had_inside_the_folder_still_works(spine: Spine) -> None:
    (spine.granted / "real").mkdir()
    symlink_or_skip(spine.granted / "link", spine.granted / "real")
    res = await approve(
        spine, "file_create", {"path": str(spine.granted / "link" / "n.txt"), "content": "hi"}
    )
    assert res.status == "succeeded"
    assert (spine.granted / "real" / "n.txt").read_text() == "hi"
    listed = await spine.call("file_list", {"path": str(spine.granted / "link")})
    assert listed.status == "succeeded" and "n.txt" in listed.content


async def test_a_second_name_for_an_outside_file_is_not_read_or_appended(
    spine: Spine, outside: Path
) -> None:
    try:
        os.link(outside / "secret.txt", spine.granted / "innocent.txt")
    except OSError:
        pytest.skip("this file system has no hard links")
    r = await spine.call("file_read", {"path": str(spine.granted / "innocent.txt")})
    assert r.status == "failed" and "attacker" not in r.content
    res = await approve(
        spine, "file_append", {"path": str(spine.granted / "innocent.txt"), "content": "x"}
    )
    assert res.status == "failed"
    # An overwrite replaces the name in the folder, never the file the other name points to.
    res = await approve(
        spine, "file_overwrite", {"path": str(spine.granted / "innocent.txt"), "content": "x"}
    )
    untouched(outside)


async def test_a_file_that_is_itself_a_link_is_not_followed(spine: Spine) -> None:
    (spine.granted / "real.txt").write_text("real\n")
    symlink_or_skip(spine.granted / "alias.txt", spine.granted / "real.txt")
    r = await spine.call("file_read", {"path": str(spine.granted / "alias.txt")})
    assert r.status == "failed" and "give the path of the file it points to" in r.content


# ── Swapped in before the path is placed, to a protected folder in the grant ──


def swap_before_locate(monkeypatch: pytest.MonkeyPatch, sub: Path, to: Path) -> Callable[[], int]:
    """Replace `sub` with a link to `to` after the tool's own check, before `locate`."""
    real = beneath.locate
    swaps: list[int] = []

    def racing(path: Path, roots: Any, **kw: Any) -> Any:
        if not swaps and sub.is_dir() and not sub.is_symlink():
            sub.rename(sub.with_name(sub.name + ".moved"))
            symlink_or_skip(sub, to)
            swaps.append(1)
        return real(path, roots, **kw)

    monkeypatch.setattr(beneath, "locate", racing)
    return lambda: len(swaps)


@pytest.fixture
def git(spine: Spine) -> Path:
    """A protected folder inside the grant, and an ordinary one beside it."""
    d = spine.granted / ".git"
    d.mkdir()
    (d / "config").write_text("[remote] the attacker wants this\n")
    sub = spine.granted / "sub"
    sub.mkdir()
    (sub / "config").write_text("fine\n")
    return d


async def test_a_read_never_lands_in_a_protected_folder_swapped_in(
    spine: Spine, git: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    swapped = swap_before_locate(monkeypatch, spine.granted / "sub", git)
    r = await spine.call("file_read", {"path": str(spine.granted / "sub" / "config")})
    assert swapped() == 1
    assert "attacker" not in r.content and r.status != "succeeded"


async def test_a_write_never_lands_in_a_protected_folder_swapped_in(
    spine: Spine, git: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = str(spine.granted / "sub" / "config")
    r = await spine.call("file_overwrite", {"path": path, "content": "planted"})
    assert r.status == "confirmation_required"
    swapped = swap_before_locate(monkeypatch, spine.granted / "sub", git)
    done = await spine.router.resolve(r.pending_id or "", "confirm", spine.ctx(turn=2, via="tap"))
    assert swapped() == 1 and done.status != "succeeded"
    assert (git / "config").read_text() == "[remote] the attacker wants this\n"
