import os
import sys
from pathlib import Path, PureWindowsPath

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


def test_a_granted_root_spelled_differently_grants_only_where_case_is_ignored(tmp_path):
    # On macOS and Windows "GRANTED" is the same folder as "Granted"; on Linux it is
    # another one, and a grant of it must not reach in here.
    granted = tmp_path / "Granted"
    granted.mkdir()
    policy = DenyPolicy(protected_roots=())
    alias = tmp_path / "GRANTED"
    denial = check(DEF, {"path": str(granted / "a.txt")}, policy, (alias,))
    assert (denial is None) is deny_floor.CASE_BLIND


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


# ── Arguments that are not confined to the granted folders (CON-5, SEC-10) ──

OUTSIDE = ToolDefinition(
    name="connected_tool",
    description="A connected server's tool (test).",
    input_schema={
        "type": "object",
        "properties": {"file_path": {"type": "string"}, "file_id": {"type": "string"}},
        "additionalProperties": False,
    },
    risk=Risk.IRREVERSIBLE,
    category="mcp",
    timeout_s=1.0,
    outside_path_args=("file_path", "file_id"),
)


def test_an_outside_path_may_be_anywhere_but_somewhere_protected(
    tmp_path: Path, setup: tuple[DenyPolicy, tuple[Path, ...], Path]
) -> None:
    policy, roots, _ = setup
    home = policy.protected_roots[0]  # Trillion's own folder
    elsewhere = tmp_path / "not-granted" / "notes.md"
    assert check(OUTSIDE, {"file_path": str(elsewhere)}, policy, roots) is None
    for protected in (
        "~/.ssh/authorized_keys",
        str(tmp_path / "proj" / ".git" / "config"),
        str(tmp_path / "proj" / ".env"),
        str(home / "secrets.age"),
    ):
        denial = check(OUTSIDE, {"file_path": protected}, policy, roots)
        assert denial is not None, protected


def test_an_id_or_an_address_is_not_a_path() -> None:
    policy = DenyPolicy(protected_roots=())
    for value in ("1aBcD-xyz_09", "https://example.com/a/b.env", "report.pdf"):
        assert check(OUTSIDE, {"file_id": value}, policy, ()) is None, value


def test_what_reads_as_a_path() -> None:
    assert deny_floor.reads_as_path("/etc/hosts") and deny_floor.reads_as_path("~/x")
    assert deny_floor.reads_as_path("notes/today.md") and deny_floor.reads_as_path("C:\\x")
    assert not deny_floor.reads_as_path("1aBcD") and not deny_floor.reads_as_path("https://x/y")


# ── A colon after the drive (Windows streams) ────────────────────────────


@pytest.mark.parametrize(
    "rel", [".env::$DATA", ".env:x", ".git::$INDEX_ALLOCATION/config", "notes:hidden.txt"]
)
def test_a_name_with_a_colon_is_refused(setup, rel):
    # On Windows `.env::$DATA` is `.env` itself: no name rule could see it.
    policy, roots, granted = setup
    denial = check(DEF, {"path": str(granted / rel)}, policy, roots)
    assert denial is not None and "colon" in denial.reason


def test_the_drive_colon_is_not_a_name(setup):
    policy, roots, granted = setup
    assert check(DEF, {"path": str(granted / "note.txt")}, policy, roots) is None
    assert not deny_floor._has_stream(PureWindowsPath("C:/Users/note.txt"))
    assert deny_floor._has_stream(PureWindowsPath("C:/Users/.env::$DATA"))


# ── Granted folders match the way the disk does ──────────────────────────


def test_a_case_sensitive_disk_keeps_docs_and_Docs_apart(monkeypatch):
    monkeypatch.setattr(deny_floor, "CASE_BLIND", False)
    assert deny_floor.inside(Path("/home/sam/docs/a"), Path("/home/sam/docs"))
    assert not deny_floor.inside(Path("/home/sam/Docs/a"), Path("/home/sam/docs"))


def test_a_case_blind_disk_treats_them_as_one(monkeypatch):
    monkeypatch.setattr(deny_floor, "CASE_BLIND", True)
    assert deny_floor.inside(Path("/Users/sam/Docs/a"), Path("/Users/sam/docs"))


@pytest.mark.skipif(sys.platform != "linux", reason="Linux disks tell Docs from docs")
def test_on_linux_a_grant_of_docs_does_not_reach_Docs(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (tmp_path / "Docs").mkdir()
    policy = DenyPolicy(protected_roots=())
    denial = check(DEF, {"path": str(tmp_path / "Docs" / "a")}, policy, (docs,))
    assert denial is not None and "granted" in denial.reason


def test_protected_names_stay_refused_in_any_case_everywhere(setup, monkeypatch):
    # Refusing too much is safe: `.GIT` is refused even where it is another folder.
    monkeypatch.setattr(deny_floor, "CASE_BLIND", False)
    policy, roots, granted = setup
    assert check(DEF, {"path": str(granted / ".GIT" / "config")}, policy, roots) is not None


_CREDS = PureWindowsPath(r"C:\Users\sam\AppData\Roaming\Microsoft\Credentials")


@pytest.mark.parametrize(
    "raw",
    [
        r"C:\Users\sam\AppData\Roaming\Microsoft\Credentials\ABC",
        r"c:\users\SAM\appdata\roaming\microsoft\credentials\ABC",
        r"\\?\C:\Users\sam\AppData\Roaming\Microsoft\Credentials\ABC",
        r"//?/c:/Users/sam/AppData/Roaming/Microsoft/Credentials/ABC",
        r"\\?\UNC\localhost\c$\Users\sam\AppData\Roaming\Microsoft\Credentials\ABC",
        r"\\?\UNC\fileserver\c$\Users\sam\AppData\Roaming\Microsoft\Credentials\ABC",
        r"\\localhost\C$\Users\sam\AppData\Roaming\Microsoft\Credentials\ABC",
        r"\\127.0.0.1\c$\Users\sam\AppData\Roaming\Microsoft\Credentials\ABC",
        r"\\127.0.0.1\Users\sam\AppData\Roaming\Microsoft\Credentials\ABC",
        r"\\[::1]\Users\sam\notes.txt",
        r"\\.\C:\Users\sam\AppData\Roaming\Microsoft\Credentials\ABC",
        r"\\.\PhysicalDrive0",
        r"\\?\Volume{0a1b2c3d-0000-0000-0000-000000000000}\Users\sam\x",
        r"\\?\GLOBALROOT\Device\HarddiskVolume1\Users\sam\x",
    ],
)
def test_every_windows_spelling_of_a_credential_store_is_refused(raw):
    # PureWindowsPath, so this runs on every OS.
    reason = deny_floor.protected_reason(PureWindowsPath(raw), (_CREDS,))
    assert reason is not None, raw


@pytest.mark.parametrize(
    "raw",
    [
        r"C:\Users\sam\Documents\notes.txt",
        r"\\?\C:\Users\sam\Documents\notes.txt",
        r"\\fileserver\team\notes.txt",
        r"\\?\UNC\fileserver\team\notes.txt",
    ],
)
def test_ordinary_windows_paths_stay_allowed(raw):
    assert deny_floor.protected_reason(PureWindowsPath(raw), (_CREDS,)) is None, raw


def test_a_long_path_prefix_is_read_as_the_plain_drive():
    plain = deny_floor.plain_windows(PureWindowsPath(r"\\?\C:\a\b"))
    assert plain == PureWindowsPath(r"C:\a\b")
    unc = deny_floor.plain_windows(PureWindowsPath(r"\\?\UNC\server\share\a"))
    assert unc == PureWindowsPath(r"\\server\share\a")


_CRED_TAIL = r"\Users\sam\AppData\Roaming\Microsoft\Credentials\ABC"


@pytest.fixture
def this_machine(monkeypatch):
    """A fake name and addresses for this computer, so the test does not depend on it."""
    sock = deny_floor.socket
    monkeypatch.setattr(sock, "gethostname", lambda: "trill-box-workstation")
    monkeypatch.setattr(sock, "getfqdn", lambda name="": "trill-box-workstation.corp.example")
    monkeypatch.setattr(
        sock,
        "getaddrinfo",
        lambda *a, **k: [(0, 0, 0, "", ("192.168.1.50", 0)), (0, 0, 0, "", ("fe80::1%en0", 0))],
    )
    deny_floor._own_names.cache_clear()
    yield
    deny_floor._own_names.cache_clear()


@pytest.mark.parametrize(
    "server",
    [
        "localhost.",
        "LOCALHOST",
        "localhost.localdomain",
        "localhost6",
        "2130706433",
        "0x7f000001",
        "127.1",
        "0177.0.0.1",
        "127.0.0.2",
        "0.0.0.0",  # noqa: S104 - a path spelling, not a bind
        "[::1]",
        "0--1.ipv6-literal.net",
        "trill-box-workstation",
        "TRILL-BOX-WORKSTATION.",
        "trill-box-workstation.corp.example",
        "TRILL-BOX-WORKS",  # the NetBIOS name, cut to 15 characters
        "192.168.1.50",
        "fe80--1sen0.ipv6-literal.net",
    ],
)
def test_every_name_for_this_computer_in_a_unc_path_is_refused(this_machine, server):
    for share in ("Users", "c$"):
        for raw in (rf"\\{server}\{share}{_CRED_TAIL}", rf"\\?\UNC\{server}\{share}\notes.txt"):
            assert deny_floor.protected_reason(PureWindowsPath(raw), (_CREDS,)), raw


def test_a_mapped_loopback_address_is_this_computer(this_machine):
    assert deny_floor._this_computer("::ffff:127.0.0.1")
    assert not deny_floor._this_computer("fileserver")
    assert not deny_floor._this_computer("192.168.1.51")


def test_another_computer_on_the_network_stays_reachable(this_machine):
    for raw in (r"\\fileserver\team\notes.txt", r"\\192.168.1.51\team\notes.txt"):
        assert deny_floor.protected_reason(PureWindowsPath(raw), (_CREDS,)) is None, raw


@pytest.mark.parametrize(
    "raw",
    [
        r"C:\Users\sam\AppData\Roaming\Microsoft\Credentials.\ABC",
        r"C:\Users\sam\AppData\Roaming\Microsoft\Credentials \ABC",
        r"C:\Users\sam\AppData\Roaming\Microsoft\Credentials. .\ABC",
        r"C:\Users\sam\AppData\Roaming\Microsoft.\Credentials\ABC",
        r"C:\Users\sam\.ssh.\id_rsa",
        r"C:\Users\sam\project\.env.",
    ],
)
def test_windows_ignores_trailing_dots_and_spaces_and_so_does_the_floor(raw):
    assert deny_floor.protected_reason(PureWindowsPath(raw), (_CREDS,)) is not None, raw
