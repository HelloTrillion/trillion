from pathlib import Path

import pytest

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
    (granted / "innocent").symlink_to(ssh)
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
