import os
import sys
from pathlib import Path

import pytest

from tests.links import junction, symlink_or_skip
from trillion.spine import deny_floor
from trillion.spine.contract import Risk, ToolDefinition
from trillion.spine.deny_floor import DenyPolicy, check

DEF = ToolDefinition(
    name="w",
    description="d",
    input_schema={},
    risk=Risk.IRREVERSIBLE,
    category="files",
    timeout_s=1.0,
    path_args=("path",),
)


@pytest.fixture
def setup(tmp_path: Path) -> tuple[DenyPolicy, tuple[Path, ...], Path]:
    home = tmp_path / "home"
    home.mkdir()
    granted = tmp_path / "granted"
    granted.mkdir()
    policy = DenyPolicy(protected_roots=(home,))
    return policy, (granted,), granted


@pytest.mark.parametrize(
    "rel",
    [
        ".git/config",
        ".GIT/config",
        "a/.ssh/id_rsa",
        ".aws/credentials",
        ".gnupg/x",
        ".env",
        ".ENV.local",
        "sub/.env.production",
        # Unicode spellings a case-insensitive, normalization-insensitive volume opens as
        # the protected name: long s (U+017F), sharp s, and full-width letters.
        ".\u017fsh/id",
        ".\u00dfh/id",
        ".aw\u017f/credentials",
        ".SSH/id",
        ".\uff45\uff4e\uff56",
        ".envrc",
        ".ENVRC",
        "prod.env",
        "sub/prod.ENV",
    ],
)
def test_protected_names_are_refused(setup, rel):
    policy, roots, granted = setup
    denial = check(DEF, {"path": str(granted / rel)}, policy, roots)
    assert denial is not None


def test_ordinary_file_in_a_granted_folder_is_allowed(setup):
    policy, roots, granted = setup
    assert check(DEF, {"path": str(granted / "note.txt")}, policy, roots) is None


def test_outside_granted_folders_is_refused(setup, tmp_path):
    policy, roots, _ = setup
    denial = check(DEF, {"path": str(tmp_path / "elsewhere.txt")}, policy, roots)
    assert denial is not None and "granted" in denial.reason


def test_protected_root_is_refused_even_if_granted(setup, tmp_path):
    policy, _, _ = setup
    home = policy.protected_roots[0]
    denial = check(DEF, {"path": str(home / "persona" / "SOUL.md")}, policy, (home,))
    assert denial is not None


def test_symlink_into_protected_directory_is_refused(setup, tmp_path):
    policy, roots, granted = setup
    ssh = granted / ".ssh"
    ssh.mkdir()
    symlink_or_skip(granted / "innocent", ssh)
    denial = check(DEF, {"path": str(granted / "innocent" / "config")}, policy, roots)
    assert denial is not None


def test_non_string_path_fails_closed(setup):
    policy, roots, _ = setup
    assert check(DEF, {"path": 42}, policy, roots) is not None
    assert check(DEF, {"path": ""}, policy, roots) is not None


def test_tools_without_path_args_are_not_inspected(setup):
    policy, roots, _ = setup
    plain = ToolDefinition(
        name="p", description="d", input_schema={}, risk=Risk.READ, category="c", timeout_s=1.0
    )
    assert check(plain, {"path": "/etc/passwd"}, policy, roots) is None


def test_default_policy_protects_home_and_package(monkeypatch, tmp_path):
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "th"))
    from trillion import paths

    policy = DenyPolicy.default()
    assert paths.home().resolve() in policy.protected_roots
    assert paths.package_root() in policy.protected_roots


def test_an_unresolvable_path_fails_closed(setup):
    policy, roots, granted = setup
    denial = check(DEF, {"path": str(granted / "bad\x00name")}, policy, roots)
    assert denial is not None and "resolved" in denial.reason


def test_an_absent_optional_path_argument_is_skipped(setup):
    policy, roots, _ = setup
    assert check(DEF, {}, policy, roots) is None


def test_a_folded_spelling_of_a_protected_root_is_refused(tmp_path):
    granted = tmp_path / "granted"
    keychains = granted / "Library" / "Keychains"
    keychains.mkdir(parents=True)
    policy = DenyPolicy(protected_roots=(keychains,))
    for leaf in ("Keychain\u017f", "KEYCHAINS", "Keychain\uff53"):
        target = granted / "Library" / leaf / "login.keychain-db"
        assert check(DEF, {"path": str(target)}, policy, (granted,)) is not None, leaf


def test_a_protected_root_spelled_with_long_s_still_protects(tmp_path):
    granted = tmp_path / "granted"
    granted.mkdir()
    policy = DenyPolicy(protected_roots=(granted / "Secret\u017f",))
    denial = check(DEF, {"path": str(granted / "secrets" / "x")}, policy, (granted,))
    assert denial is not None and "protected" in denial.reason


def test_a_granted_root_spelled_differently_still_grants(tmp_path):
    granted = tmp_path / "Granted"
    granted.mkdir()
    policy = DenyPolicy(protected_roots=())
    alias = tmp_path / "GRANTED"
    assert check(DEF, {"path": str(granted / "a.txt")}, policy, (alias,)) is None


@pytest.mark.skipif(deny_floor._F_GETPATH is None, reason="F_GETPATH is macOS-only")
def test_the_on_disk_spelling_is_what_gets_checked(tmp_path):
    real = tmp_path / "Real"
    real.mkdir()
    if not (tmp_path / "REAL").exists():
        pytest.skip("case-sensitive volume")
    canon = deny_floor.canonical(tmp_path / "REAL" / "missing" / "leaf")
    assert canon.parts[-3:] == ("Real", "missing", "leaf")


def test_canonical_falls_back_without_f_getpath(tmp_path, monkeypatch):
    monkeypatch.setattr(deny_floor, "_F_GETPATH", None)
    p = tmp_path / "x" / "y"
    assert deny_floor.canonical(p) == p


@pytest.mark.skipif(
    os.name == "nt" or os.geteuid() == 0,
    reason="POSIX permissions; root can open anything",
)
def test_canonical_falls_back_when_the_prefix_cannot_be_opened(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        assert deny_floor.canonical(locked) == locked
    finally:
        locked.chmod(0o700)


@pytest.mark.skipif(sys.platform == "win32", reason="the F_GETPATH lookup is macOS-only")
def test_canonical_falls_back_when_the_lookup_fails(tmp_path, monkeypatch):
    def boom(*_a: object) -> bytes:
        raise OSError("no")

    monkeypatch.setattr(deny_floor, "_F_GETPATH", 50)
    monkeypatch.setattr(deny_floor.fcntl, "fcntl", boom)
    assert deny_floor.canonical(tmp_path) == tmp_path


@pytest.mark.skipif(sys.platform != "win32", reason="junctions exist only on Windows")
def test_junction_into_protected_directory_is_refused(setup):
    policy, roots, granted = setup
    ssh = granted / ".ssh"
    ssh.mkdir()
    junction(granted / "innocent", ssh)
    denial = check(DEF, {"path": str(granted / "innocent" / "config")}, policy, roots)
    assert denial is not None
