"""D11 of the shared folders spec: `read` access holds at the deny floor, for every
write tool, and where shares nest the innermost one decides."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.links import symlink_or_skip
from tests.spine.conftest import Spine
from trillion.spine import deny_floor
from trillion.spine.confirm import Refusal
from trillion.spine.contract import Risk, ToolContext, ToolDefinition
from trillion.tools import files
from trillion.tools.files import FileCreate

READ_ONLY = deny_floor.READ_ONLY


def _writes(folder: Path) -> list[tuple[str, dict[str, str]]]:
    existing = folder / "old.txt"
    existing.write_text("old\n")
    return [
        ("file_create", {"path": str(folder / "new.txt"), "content": "x"}),
        ("file_append", {"path": str(existing), "content": "x"}),
        ("file_overwrite", {"path": str(existing), "content": "x"}),
        ("file_edit", {"path": str(existing), "old": "old", "new": "new"}),
    ]


async def test_each_write_tool_is_refused_in_a_read_share(spine: Spine) -> None:
    for name, args in _writes(spine.granted):
        r = await spine.call(name, args, read_only_roots=(spine.granted,))
        assert r.reason is Refusal.DENIED_BY_FLOOR, name
        assert READ_ONLY in r.to_model(), name
        assert r.pending_id is None
    assert (spine.granted / "old.txt").read_text() == "old\n"
    assert not (spine.granted / "new.txt").exists()


async def test_reading_a_read_share_is_allowed(spine: Spine) -> None:
    (spine.granted / "a.txt").write_text("hello")
    r = await spine.call(
        "file_read", {"path": str(spine.granted / "a.txt")}, read_only_roots=(spine.granted,)
    )
    assert r.status == "succeeded"
    assert "hello" in r.to_model()


async def test_a_change_share_inside_a_read_share_may_be_written(spine: Spine) -> None:
    inner = spine.granted / "inner"
    inner.mkdir()
    for name, args in _writes(inner):
        r = await spine.call(
            name, args, granted_roots=(spine.granted, inner), read_only_roots=(spine.granted,)
        )
        assert r.status == "confirmation_required", (name, r.to_model())


async def test_a_read_share_inside_a_change_share_is_refused(spine: Spine) -> None:
    inner = spine.granted / "inner"
    inner.mkdir()
    for name, args in _writes(inner):
        r = await spine.call(
            name, args, granted_roots=(spine.granted, inner), read_only_roots=(inner,)
        )
        assert r.reason is Refusal.DENIED_BY_FLOOR, name
        assert READ_ONLY in r.to_model()
    # The outer, `change` share is still written.
    outer = await spine.call(
        "file_create",
        {"path": str(spine.granted / "top.txt"), "content": "x"},
        granted_roots=(spine.granted, inner),
        read_only_roots=(inner,),
    )
    assert outer.status == "confirmation_required"


async def test_a_card_approved_after_its_folder_became_read_only_is_refused(spine: Spine) -> None:
    target = spine.granted / "x.txt"
    r = await spine.call("file_create", {"path": str(target), "content": "x"})
    assert r.status == "confirmation_required"
    res = await spine.call(
        "confirm_pending_action",
        {"pending_id": r.pending_id},
        turn=2,
        read_only_roots=(spine.granted,),
    )
    assert res.reason is Refusal.DENIED_BY_FLOOR
    assert READ_ONLY in res.to_model()
    assert not target.exists()


async def test_a_tool_that_names_a_path_outside_the_shares_is_judged_too(tmp_path: Path) -> None:
    """A connected server's write tool that names a file in a read share is refused; its
    read is not, and a path in no share is left to the server."""
    folder = tmp_path / "shared"
    folder.mkdir()

    def tool(risk: Risk) -> ToolDefinition:
        return ToolDefinition(
            name="server_tool",
            description="d",
            input_schema={"type": "object"},
            risk=risk,
            category="mcp",
            timeout_s=1.0,
            outside_path_args=("path",),
        )

    policy = deny_floor.DenyPolicy(protected_roots=())
    args = {"path": str(folder / "a.txt")}
    roots = (folder,)
    denial = deny_floor.check(tool(Risk.WRITE), args, policy, roots, read_only_roots=roots)
    assert denial is not None and denial.reason == READ_ONLY
    assert deny_floor.check(tool(Risk.READ), args, policy, roots, read_only_roots=roots) is None
    # A relative path names a place in the server's own world, not under a share.
    relative = {"path": "src/a.txt"}
    assert (
        deny_floor.check(tool(Risk.WRITE), relative, policy, roots, read_only_roots=roots) is None
    )
    elsewhere = {"path": str(tmp_path / "b.txt")}
    assert (
        deny_floor.check(tool(Risk.WRITE), elsewhere, policy, roots, read_only_roots=roots) is None
    )


@pytest.mark.parametrize("risk", [Risk.WRITE, Risk.IRREVERSIBLE, Risk.SPEND])
def test_any_tool_that_is_not_a_read_counts_as_a_write(tmp_path: Path, risk: Risk) -> None:
    defn = ToolDefinition(
        name="t",
        description="d",
        input_schema={"type": "object"},
        risk=risk,
        category="files",
        timeout_s=1.0,
        path_args=("path",),
    )
    policy = deny_floor.DenyPolicy(protected_roots=())
    denial = deny_floor.check(
        defn, {"path": str(tmp_path / "a")}, policy, (tmp_path,), read_only_roots=(tmp_path,)
    )
    assert denial is not None and denial.reason == READ_ONLY


def _ctx(granted: tuple[Path, ...], read_only: tuple[Path, ...]) -> ToolContext:
    return ToolContext(
        owner_id="local",
        session_id="s1",
        turn=1,
        call_id="c1",
        via="text",
        granted_roots=granted,
        read_only_roots=read_only,
    )


async def test_the_write_itself_refuses_a_read_share_without_the_router(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    result = await FileCreate().execute(
        {"path": str(target), "content": "x"}, _ctx((tmp_path,), (tmp_path,))
    )
    assert not result.ok and result.effect == "none"
    assert READ_ONLY in result.content
    assert not target.exists()


async def test_a_folder_swapped_for_a_link_into_a_read_share_is_refused_on_the_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The outer share may be changed, the inner is read only. Between the tool's own
    check and its walk, `outer/sub` becomes a link to the read only folder."""
    outer, locked = tmp_path / "outer", tmp_path / "outer" / "locked"
    sub = outer / "sub"
    locked.mkdir(parents=True)
    sub.mkdir()
    check = deny_floor.check

    def check_then_swap(*args: object, **kw: object) -> deny_floor.Denial | None:
        denial = check(*args, **kw)  # type: ignore[arg-type]
        sub.rmdir()
        symlink_or_skip(sub, locked)
        return denial

    monkeypatch.setattr(files.deny_floor, "check", check_then_swap)
    result = await FileCreate().execute(
        {"path": str(sub / "a.txt"), "content": "x"}, _ctx((outer, locked), (locked,))
    )
    assert not result.ok and READ_ONLY in result.content
    assert not (locked / "a.txt").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="/var is a link to /private/var on macOS")
def test_a_read_share_holds_however_var_is_spelled(tmp_path: Path) -> None:
    real = tmp_path.resolve()
    assert str(real).startswith("/private/var/")
    linked = Path(str(real).removeprefix("/private"))
    inner = real / "inner"
    inner.mkdir()
    defn = FileCreate.definition
    policy = deny_floor.DenyPolicy(protected_roots=())
    for root, locked, target in (
        (linked, linked / "inner", real / "inner" / "a"),
        (real, real / "inner", linked / "inner" / "a"),
    ):
        denial = deny_floor.check(
            defn, {"path": str(target)}, policy, (root,), read_only_roots=(locked,)
        )
        assert denial is not None and denial.reason == READ_ONLY, (root, locked, target)
    # And a change share inside a read share, spelled the other way, may be written.
    assert (
        deny_floor.check(
            defn,
            {"path": str(linked / "inner" / "a")},
            policy,
            (real, inner),
            read_only_roots=(linked,),
        )
        is None
    )


@pytest.mark.skipif(not deny_floor.CASE_BLIND, reason="names differ by case only where disks do")
def test_a_read_share_holds_whatever_the_case(tmp_path: Path) -> None:
    folder = tmp_path.resolve() / "Website"
    folder.mkdir()
    shouted = folder.parent / "WEBSITE"
    defn = FileCreate.definition
    policy = deny_floor.DenyPolicy(protected_roots=())
    for root, locked, target in (
        (folder, shouted, folder / "a"),
        (folder, folder, shouted / "a"),
    ):
        denial = deny_floor.check(
            defn, {"path": str(target)}, policy, (root,), read_only_roots=(locked,)
        )
        assert denial is not None and denial.reason == READ_ONLY
