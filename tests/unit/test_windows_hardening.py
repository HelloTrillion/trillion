"""SUR-3c: Windows Credential Manager, owner-only access lists, and the microphones
`trillion doctor` lists on Windows.

Most tests run on every system with Windows faked: a stand-in Credential Manager
that keeps Windows' size limit and raises its own kind of error, a recorder in place
of the call that sets an access list, and stand-ins for Core Audio and the registry.
The tests marked for Windows exercise the real thing in the Windows CI job."""

from __future__ import annotations

import os
import stat
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import keyring
import keyring.backend
import keyring.errors
import pytest

from trillion import owner_only
from trillion.cli import doctor, windows_audio
from trillion.secrets import store as store_mod
from trillion.secrets.store import KeychainStore, SecretStoreError

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="the real Windows APIs")
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")

USER = "S-1-5-21-1111111111-2222222222-3333333333-1001"
# Built from pieces: a token's length and shape, not a token.
LONG = "ya29." + "a0Ab" * 700 + "-tail-0042"


# ── Credential Manager ───────────────────────────────────────────────────


class WinError(Exception):
    """What pywintypes.error is: not one of keyring's errors."""


class FakeCredentialManager(keyring.backend.KeyringBackend):
    """Keeps Windows' limit: 2,560 bytes of UTF-16 in one entry."""

    priority = 5.0  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.items.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        if len(password.encode("utf-16-le")) > 2560:
            raise WinError(1783, "CredWrite", "The stub received bad data.")
        self.items[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.items:
            raise keyring.errors.PasswordDeleteError("absent")
        del self.items[(service, username)]


@pytest.fixture
def credentials() -> Iterator[FakeCredentialManager]:
    before = keyring.get_keyring()
    fake = FakeCredentialManager()
    keyring.set_keyring(fake)
    yield fake
    keyring.set_keyring(before)


def windows_store() -> KeychainStore:
    return KeychainStore(piece=store_mod.WINDOWS_PIECE)


def test_the_windows_piece_always_fits_credential_manager() -> None:
    widest = "\U0001f600" * store_mod.WINDOWS_PIECE  # four bytes each in UTF-16
    assert len(widest.encode("utf-16-le")) <= 2560


def test_a_long_value_is_kept_in_pieces_and_read_back_whole(
    credentials: FakeCredentialManager,
) -> None:
    store = windows_store()
    store.set("mcp/tracker/sign-in", LONG)
    assert len(credentials.items) > 2
    assert all(len(v.encode("utf-16-le")) <= 2560 for v in credentials.items.values())
    assert LONG not in credentials.items.values()
    assert store.get("mcp/tracker/sign-in") == LONG
    assert LONG in store_mod.handed_out()  # the scrubber knows the whole value


def test_a_shorter_value_drops_the_old_pieces(credentials: FakeCredentialManager) -> None:
    store = windows_store()
    store.set("google/account", LONG)
    store.set("google/account", "short-again-0007")
    assert credentials.items == {("trillion", "google/account"): "short-again-0007"}
    store.set("google/account", LONG)
    store.set("google/account", LONG[:1500])
    assert len(credentials.items) == 1 + 3
    assert store.get("google/account") == LONG[:1500]


def test_removing_a_long_value_removes_every_piece(credentials: FakeCredentialManager) -> None:
    store = windows_store()
    store.set("mcp/tracker/sign-in", LONG)
    store.delete("mcp/tracker/sign-in")
    assert credentials.items == {}
    assert store.get("mcp/tracker/sign-in") is None
    store.delete("mcp/tracker/sign-in")  # twice is fine


def test_a_missing_piece_reads_as_nothing_not_half_a_key(
    credentials: FakeCredentialManager,
) -> None:
    store = windows_store()
    store.set("mcp/tracker/sign-in", LONG)
    del credentials.items[("trillion", "mcp/tracker/sign-in~piece-2")]
    assert store.get("mcp/tracker/sign-in") is None


def test_a_value_shaped_like_a_list_of_pieces_still_reads_back(
    credentials: FakeCredentialManager,
) -> None:
    for store in (KeychainStore(), windows_store()):
        store.set("odd/value", "trillion-split:v1:3")
        assert store.get("odd/value") == "trillion-split:v1:3"


def test_off_windows_a_long_value_is_one_entry(credentials: FakeCredentialManager) -> None:
    KeychainStore(piece=None).set("google/account", "x" * 1000)
    if sys.platform != "win32":
        assert list(credentials.items) == [("trillion", "google/account")]


def test_credential_managers_own_errors_never_escape_or_show_a_value(
    credentials: FakeCredentialManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = KeychainStore(piece=None)  # no pieces: the raw limit is hit
    with pytest.raises(SecretStoreError) as e:
        store.set("mcp/tracker/sign-in", LONG)
    assert LONG not in str(e.value) and "mcp/tracker/sign-in" in str(e.value)

    def broken(*args: object) -> None:
        raise WinError(5, "CredRead", "Access is denied.")

    monkeypatch.setattr(credentials, "get_password", broken)
    assert store.get("anything") is None
    monkeypatch.setattr(credentials, "delete_password", broken)
    with pytest.raises(SecretStoreError):
        store.delete("anything")


def test_doctor_names_the_backend_keyring_chose() -> None:
    class Backend:
        def __init__(self, name: str) -> None:
            self.name = name

    class Chain:
        name = "chainer ChainerBackend"
        backends = (Backend("Windows WinVaultKeyring"), Backend("other Keyring"))

    assert doctor.backend_name(Backend("Windows WinVaultKeyring")) == "Windows WinVaultKeyring"
    assert doctor.backend_name(Chain()) == "Windows WinVaultKeyring"
    named = doctor.check_keychain(store_mod.MemoryStore(), doctor.backend_name(Chain()))
    assert named.detail == "Windows Credential Manager"


@windows_only
def test_on_windows_keyring_chooses_credential_manager() -> None:
    """What `keyring` picks with nothing configured, without touching what it holds."""
    from keyring.backend import by_priority, get_all_keyring
    from keyring.backends.Windows import WinVaultKeyring

    # keyring's own backends: the tests define stand-ins, which would join a chain.
    own = [
        k
        for k in get_all_keyring()
        if type(k).__module__.startswith("keyring.backends.")
        and not type(k).__module__.endswith("chainer")
    ]
    chosen = max(own, key=by_priority)
    assert isinstance(chosen, WinVaultKeyring)
    assert doctor.KEYCHAIN_NAMES[doctor.backend_name(chosen)] == "Windows Credential Manager"


@windows_only
@pytest.mark.skipif(not os.environ.get("CI"), reason="writes to Credential Manager: CI only")
def test_on_windows_a_long_value_round_trips_through_credential_manager() -> None:
    """The real limit, on a throwaway CI machine, under a service of its own."""
    from keyring.backends.Windows import WinVaultKeyring

    before = keyring.get_keyring()
    keyring.set_keyring(WinVaultKeyring())
    store = KeychainStore(service=f"trillion-test-{uuid.uuid4().hex[:8]}")
    try:
        store.set("mcp/tracker/sign-in", LONG)
        assert store.get("mcp/tracker/sign-in") == LONG
        store.set("mcp/tracker/sign-in", "short-again-0007")
        assert store.get("mcp/tracker/sign-in") == "short-again-0007"
    finally:
        store.delete("mcp/tracker/sign-in")
        keyring.set_keyring(before)
    assert store.get("mcp/tracker/sign-in") is None


# ── Owner-only access lists ──────────────────────────────────────────────


def test_the_access_list_is_the_users_and_systems_with_nothing_inherited() -> None:
    assert owner_only.sddl(USER, folder=True) == f"D:P(A;OICI;FA;;;{USER})(A;OICI;FA;;;SY)"
    assert owner_only.sddl(USER, folder=False) == f"D:P(A;;FA;;;{USER})(A;;FA;;;SY)"


def test_restrict_gives_folders_and_files_their_own_list(tmp_path: Path) -> None:
    applied: list[tuple[Path, str]] = []
    folder, file = tmp_path / "home", tmp_path / "home" / "trillion.db"
    folder.mkdir()
    file.write_text("")
    for path in (folder, file):
        assert owner_only.restrict(path, apply=lambda p, d: applied.append((p, d)), user=USER)
    assert applied == [
        (folder, owner_only.sddl(USER, folder=True)),
        (file, owner_only.sddl(USER, folder=False)),
    ]


def test_restrict_reports_a_refusal_and_never_raises(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def refuse(path: Path, descriptor: str) -> None:
        raise PermissionError(5, "Access is denied")

    assert not owner_only.restrict(tmp_path, apply=refuse, user=USER)
    assert "could not make" in caplog.text


@posix_only
def test_restrict_does_nothing_off_windows(tmp_path: Path) -> None:
    tmp_path.chmod(0o755)
    assert owner_only.restrict(tmp_path)
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o755  # modes stay the callers' job


@pytest.mark.parametrize(
    ("descriptor", "who"),
    [
        (f"D:P(A;OICI;FA;;;{USER})(A;OICI;FA;;;SY)", []),
        (f"D:PAI(A;OICI;FA;;;{USER})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)", []),
        (f"D:AI(A;ID;FA;;;{USER})(A;OICIIOID;GA;;;CO)", []),
        (f"D:AI(A;ID;FA;;;{USER})(A;OICIID;0x1200a9;;;BU)", ["Users"]),
        (f"D:(A;;FA;;;{USER})(A;;FR;;;WD)(A;;FR;;;AU)", ["Everyone", "Authenticated Users"]),
        (f"D:(A;;FA;;;{USER})(A;;FR;;;S-1-5-21-1-2-3-1002)", ["another account"]),
        (f"D:(A;;FA;;;{USER})(D;;FA;;;WD)", []),  # a deny lets nobody in
        ("D:NO_ACCESS_CONTROL", ["Everyone"]),
    ],
)
def test_who_else_an_access_list_lets_in(descriptor: str, who: list[str]) -> None:
    assert owner_only.others(descriptor, USER) == who


def test_the_users_own_account_in_short_form_is_the_user() -> None:
    admin = "S-1-5-21-1111111111-2222222222-3333333333-500"
    descriptor = "D:P(A;OICI;FA;;;LA)(A;OICI;FA;;;SY)"
    full = {"LA": admin}.get
    assert owner_only.others(descriptor, admin, lambda s: full(s) or s) == []
    assert owner_only.others(descriptor, USER, lambda s: full(s) or s) == ["another account"]


def test_exposed_reads_the_list_on_windows(tmp_path: Path) -> None:
    def read(path: Path) -> str:
        return f"D:AI(A;ID;FA;;;{USER})(A;OICIID;0x1200a9;;;BU)"

    assert owner_only.exposed(tmp_path, read=read, user=USER) == ["Users"]

    def unreadable(path: Path) -> str:
        raise OSError("no access lists on this drive")

    assert owner_only.exposed(tmp_path, read=unreadable, user=USER) is None


@posix_only
def test_exposed_reads_the_mode_elsewhere(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    assert owner_only.exposed(tmp_path) == []
    tmp_path.chmod(0o750)
    assert owner_only.exposed(tmp_path) == ["its group"]
    tmp_path.chmod(0o755)
    assert owner_only.exposed(tmp_path) == ["its group", "everyone on this computer"]
    tmp_path.chmod(0o700)


async def test_the_database_and_its_folder_are_restricted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trillion.storage.db import connect

    asked: list[Path] = []
    monkeypatch.setattr(owner_only, "restrict", lambda path: asked.append(path) or True)
    conn = await connect(tmp_path / "home" / "trillion.db")
    await conn.close()
    assert asked == [tmp_path / "home", tmp_path / "home" / "trillion.db"]


def test_the_voiceprint_is_restricted_before_the_voice_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trillion.voice import speaker

    seen: list[tuple[str, str]] = []

    def restrict(path: Path) -> bool:
        seen.append((path.name, path.read_text(encoding="utf-8")))
        return True

    monkeypatch.setattr(owner_only, "restrict", restrict)
    speaker._write_private(tmp_path / "voiceprint.json", '{"voice": 1}')
    assert seen == [("voiceprint.json.tmp", "")]  # empty when restricted
    assert (tmp_path / "voiceprint.json").read_text(encoding="utf-8") == '{"voice": 1}'


@windows_only
def test_on_windows_restrict_leaves_only_the_user_and_system(tmp_path: Path) -> None:
    folder = tmp_path / "home"
    folder.mkdir()
    before = folder / "before.txt"
    before.write_text("made before")
    assert owner_only.exposed(folder) is not None
    assert owner_only.restrict(folder)
    sid = owner_only.user_sid()
    assert sid is not None and sid.startswith("S-1-5-")
    assert owner_only._read(folder).startswith("D:P")
    after = folder / "after.txt"
    after.write_text("made after")
    for path in (folder, before, after):
        assert owner_only.exposed(path) == [], (path, owner_only._read(path), sid)
    assert doctor.check_privacy(folder) == doctor.Check(
        name="Data folder access", status="ok", detail="only you can open it"
    )


# ── doctor: the data folder ──────────────────────────────────────────────


def test_doctor_says_who_else_can_open_the_data_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert doctor.check_privacy(tmp_path / "missing") is None
    monkeypatch.setattr(owner_only, "exposed", lambda path: ["Users"])
    warned = doctor.check_privacy(tmp_path)
    assert warned is not None and warned.status == "warn"
    assert warned.detail == "Users can open it too" and "trillion setup" in warned.fix
    monkeypatch.setattr(owner_only, "exposed", lambda path: None)
    unsure = doctor.check_privacy(tmp_path)
    assert unsure is not None and unsure.status == "info"
    monkeypatch.setattr(owner_only, "exposed", lambda path: [])
    fine = doctor.check_privacy(tmp_path)
    assert fine is not None and fine.status == "ok"


@posix_only
def test_doctor_reads_the_data_folders_mode(tmp_path: Path) -> None:
    tmp_path.chmod(0o755)
    check = doctor.check_privacy(tmp_path)
    assert check is not None and check.status == "warn"
    assert check.detail == "its group and everyone on this computer can open it too"
    tmp_path.chmod(0o700)
    assert doctor.check_privacy(tmp_path).status == "ok"  # type: ignore[union-attr]


# ── doctor: the microphone on Windows ────────────────────────────────────


@pytest.fixture
def on_windows(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setattr(doctor.sys, "platform", "win32")
    monkeypatch.setattr(windows_audio, "access", lambda: "on")
    return monkeypatch


def test_the_default_microphone_is_named(on_windows: pytest.MonkeyPatch) -> None:
    found = windows_audio.Inputs(names=("Headset", "Microphone Array"), default="Headset")
    on_windows.setattr(windows_audio, "inputs", lambda: found)
    check = doctor.check_microphone()
    assert check.status == "ok"
    assert check.detail.startswith("2 microphones; default: Headset;")


@pytest.mark.parametrize(
    ("found", "status", "said"),
    [
        (windows_audio.Inputs(names=(), default=None), "warn", "lists no microphone"),
        (windows_audio.Inputs(names=("Line In",), default=None), "warn", "none is the default"),
        (None, "info", "did not list its devices"),
    ],
)
def test_no_microphone_or_no_default_is_said(
    on_windows: pytest.MonkeyPatch, found: windows_audio.Inputs | None, status: str, said: str
) -> None:
    on_windows.setattr(windows_audio, "inputs", lambda: found)
    check = doctor.check_microphone()
    assert check.status == status and said in check.detail


@pytest.mark.parametrize(
    ("values", "access"),
    [
        ({}, None),
        ({"": "Allow", "NonPackaged": "Allow"}, "on"),
        ({"": "Deny", "NonPackaged": "Allow"}, "off"),
        ({"": "Allow", "NonPackaged": "Deny"}, "desktop_off"),
        ({"": "Allow"}, "on"),
    ],
)
def test_the_privacy_switches_are_read(values: dict[str, str], access: str | None) -> None:
    def read(path: str) -> str | None:
        return values.get(path.removeprefix(windows_audio.CONSENT_KEY).lstrip("\\"))

    assert windows_audio.access(read) == access


def test_a_privacy_switch_that_is_off_comes_first(on_windows: pytest.MonkeyPatch) -> None:
    def never() -> windows_audio.Inputs:
        raise AssertionError("not asked: the switch already explains it")

    on_windows.setattr(windows_audio, "access", lambda: "desktop_off")
    on_windows.setattr(windows_audio, "inputs", never)
    check = doctor.check_microphone()
    assert check.status == "warn" and "Let desktop apps" in check.detail
    assert "Privacy & security" in check.fix


def test_off_windows_nothing_is_asked_of_windows() -> None:
    if sys.platform != "win32":
        assert windows_audio.inputs() is None
        assert windows_audio.access() is None


@windows_only
def test_on_windows_the_devices_are_listed_without_opening_one() -> None:
    """A CI runner may have no sound device: an empty list, or can't tell, never a crash."""
    found = windows_audio.inputs()
    assert found is None or isinstance(found, windows_audio.Inputs)
    if found is not None and found.default is not None:
        assert found.default in found.names
    assert windows_audio.access() in (None, "on", "off", "desktop_off")
    assert doctor.check_microphone().name == "Microphone"
