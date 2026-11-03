"""SPN-4, SEC-10: the operating system's credential stores are refused on every OS.

Each store is protected wherever Trillion runs, so a copied home folder or a mounted
disk from another machine is held the same. The home folder is a fake one here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.spine.conftest import Spine
from trillion.spine.confirm import Refusal
from trillion.spine.deny_floor import DenyPolicy, credential_stores, refused

STORES = [
    "Library/Keychains/login.keychain-db",  # macOS
    ".local/share/keyrings/login.keyring",  # Linux Secret Service (GNOME Keyring)
    ".local/share/kwalletd/kdewallet.kwl",  # Linux KWallet 5 and later
    ".kde/share/apps/kwallet/kdewallet.kwl",  # Linux KWallet 4
    ".kde4/share/apps/kwallet/kdewallet.kwl",
    "AppData/Roaming/Microsoft/Credentials/ABCDEF",  # Windows Credential Manager
    "AppData/Local/Microsoft/Credentials/ABCDEF",
    "AppData/Roaming/Microsoft/Protect/S-1-5-21/key",  # Windows DPAPI master keys
    "AppData/Local/Microsoft/Vault/policy.vpol",  # Windows Vault
    "AppData/Roaming/Microsoft/Vault/policy.vpol",
]


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Runs before the spine fixture, so the policy it builds sees this home."""
    fake = tmp_path / "user"
    fake.mkdir()
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setenv("USERPROFILE", str(fake))
    for name in ("APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)
    return fake


def _plant(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("PRIVATE")


@pytest.mark.parametrize("rel", STORES)
async def test_a_credential_store_is_refused_even_inside_a_granted_folder(
    spine: Spine, home: Path, rel: str
) -> None:
    target = home / rel
    _plant(target)
    r = await spine.call("file_read", {"path": str(target)}, granted_roots=(home,))
    assert r.reason is Refusal.DENIED_BY_FLOOR, rel
    assert "PRIVATE" not in r.to_model()
    w = await spine.call(
        "file_create", {"path": str(target.parent / "new"), "content": "x"}, granted_roots=(home,)
    )
    assert w.reason is Refusal.DENIED_BY_FLOOR and w.pending_id is None, rel
    folder = await spine.call("file_list", {"path": str(target.parent)}, granted_roots=(home,))
    assert folder.reason is Refusal.DENIED_BY_FLOOR, rel


async def test_the_rest_of_the_home_folder_stays_usable(spine: Spine, home: Path) -> None:
    for rel in ("notes.txt", ".local/share/notes.txt", "AppData/Roaming/Microsoft/Word/a.txt"):
        _plant(home / rel)
        r = await spine.call("file_read", {"path": str(home / rel)}, granted_roots=(home,))
        assert r.reason is None, rel


@pytest.mark.parametrize(
    "rel",
    [
        "LIBRARY/KEYCHAINS/login.keychain-db",
        ".local/share/KEYRINGS/login.keyring",
        ".local/share/keyring\u017f/login.keyring",  # long s
        "appdata/roaming/microsoft/CREDENTIALS/ABCDEF",
        "AppData/Roaming/Microsoft/Prot\uff45ct/key",  # full-width e
    ],
)
async def test_another_spelling_of_a_store_is_refused(spine: Spine, home: Path, rel: str) -> None:
    r = await spine.call("file_read", {"path": str(home / rel)}, granted_roots=(home,))
    assert r.reason is Refusal.DENIED_BY_FLOOR, rel


def test_moved_data_folders_are_followed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A redirected %APPDATA% or $XDG_DATA_HOME holds the stores wherever it points."""
    roaming, local, xdg = tmp_path / "roam", tmp_path / "loc", tmp_path / "xdg"
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    stores = credential_stores()
    for p in (
        roaming / "Microsoft" / "Credentials",
        roaming / "Microsoft" / "Protect",
        local / "Microsoft" / "Credentials",
        xdg / "keyrings",
        xdg / "kwalletd",
    ):
        assert p.resolve() in stores, p
    # The usual spots are still held: a variable can be changed after the fact.
    assert (Path.home() / ".local" / "share" / "keyrings").resolve() in stores
    assert set(stores) <= set(DenyPolicy.default().protected_roots)
    for p in (roaming / "Microsoft" / "Credentials" / "x", xdg / "keyrings" / "login.keyring"):
        _plant(p)
        assert refused(p.resolve(), (tmp_path.resolve(),)) is not None, p
    assert refused((xdg / "notes.txt").resolve(), (tmp_path.resolve(),)) is None
