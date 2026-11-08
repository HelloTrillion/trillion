"""SEC-2: the encrypted key file, for a machine with no usable keychain.

Never the real keychain: a `MemoryStore` stands in for it, and the suite runs with
keyring's null backend."""

from __future__ import annotations

import asyncio
import logging
import os
import stat
from pathlib import Path

import pytest

from trillion.brain import config
from trillion.cli import doctor
from trillion.secrets import fallback
from trillion.secrets import store as secret_store
from trillion.secrets.fallback import (
    BY_KEYCHAIN,
    BY_PASSPHRASE,
    MAGIC,
    PASSPHRASE_ENV,
    Argon2Params,
    FileStore,
    FileStoreError,
    choose,
    create,
)
from trillion.secrets.store import KeychainStore, MemoryStore, SecretStoreError

# Small Argon2id settings so the suite stays quick; the defaults are tested once.
FAST = Argon2Params(passes=1, memory_kib=64, lanes=1)
PHRASE = "correct horse battery"  # a test passphrase, not anyone's
WRONG = "not " + PHRASE
KEY = "sk-" + "test-" * 6 + "51a2"  # built from pieces: never a real credential
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX modes")


def made(path: Path, phrase: str = PHRASE) -> FileStore:
    return create(path, passphrase=lambda: phrase, params=FAST)


@pytest.fixture(autouse=True)
def no_keychain(monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    """The system keychain, as `choose` and `trillion setup` find it: none, whatever the
    machine running the tests has, so nothing here can reach a real one."""
    none = MemoryStore(usable=False)
    monkeypatch.setattr("trillion.secrets.store.KeychainStore", lambda: none)
    return none


@pytest.fixture
def key_file(tmp_path: Path) -> Path:
    return tmp_path / "home" / "secrets.age"


class RefusingKeychain(MemoryStore):
    """A keychain that says it works and refuses every write (a locked-down one)."""

    def set(self, name: str, value: str) -> None:
        raise SecretStoreError("refused")


# ── The crypto round trip ────────────────────────────────────────────────


def test_a_key_saved_is_read_back_after_a_fresh_unlock(key_file: Path) -> None:
    first = made(key_file)
    assert first.available() and first.unlocked_by == "passphrase"
    first.set("anthropic/api_key", KEY)
    first.set("slack/bot", "test bot value 0001")
    again = FileStore(key_file)
    assert not again.available() and again.get("anthropic/api_key") is None
    again.unlock(PHRASE)
    assert again.get("anthropic/api_key") == KEY
    assert again.names() == ["anthropic/api_key", "slack/bot"]
    again.delete("slack/bot")
    again.delete("never/there")  # quietly nothing
    assert FileStore(key_file).names() == []  # locked: names nothing
    assert again.names() == ["anthropic/api_key"]


def test_the_file_holds_no_value_and_no_passphrase(key_file: Path) -> None:
    made(key_file).set("anthropic/api_key", KEY)
    raw = key_file.read_bytes()
    assert raw.startswith(MAGIC + bytes([1, BY_PASSPHRASE]))
    assert KEY.encode() not in raw and PHRASE.encode() not in raw
    assert b"anthropic" not in raw  # names are inside the encryption too


def test_every_write_uses_a_fresh_nonce(key_file: Path) -> None:
    store = made(key_file)
    store.set("a/key", KEY)
    one = key_file.read_bytes()
    store.set("a/key", KEY)
    two = key_file.read_bytes()
    kept = len(MAGIC) + 2 + 16 + 12 + 60  # header, then the wrapped data key
    assert one[:kept] == two[:kept]
    assert one[kept : kept + 12] != two[kept : kept + 12]  # the contents' nonce


def test_the_default_argon2id_settings_round_trip(key_file: Path) -> None:
    create(key_file, passphrase=lambda: PHRASE).set("a/key", KEY)
    store = FileStore(key_file)
    store.unlock(PHRASE)
    assert store.get("a/key") == KEY


def test_a_value_read_is_known_to_the_log_scrubber(key_file: Path) -> None:
    made(key_file).set("a/key", KEY)
    secret_store.forget_all()
    store = FileStore(key_file)
    store.unlock(PHRASE)
    assert KEY not in secret_store.handed_out()
    store.get("a/key")
    assert KEY in secret_store.handed_out()
    store.delete("a/key")
    assert KEY not in secret_store.handed_out()


def test_an_existing_file_is_never_replaced_by_create(key_file: Path) -> None:
    made(key_file).set("a/key", KEY)
    before = key_file.read_bytes()
    with pytest.raises(FileStoreError) as e:
        made(key_file, "another passphrase")
    assert e.value.kind == "exists" and key_file.read_bytes() == before


def test_no_passphrase_means_no_file(key_file: Path) -> None:
    with pytest.raises(FileStoreError) as e:
        create(key_file, passphrase=lambda: None, params=FAST)
    assert e.value.kind == "locked" and PASSPHRASE_ENV in e.value.fix
    assert not key_file.exists()


# ── Wrong passphrase, changed bytes ──────────────────────────────────────


def test_a_wrong_passphrase_is_a_clear_error_and_the_file_is_kept(key_file: Path) -> None:
    made(key_file).set("a/key", KEY)
    before = key_file.read_bytes()
    store = FileStore(key_file)
    with pytest.raises(FileStoreError) as e:
        store.unlock("not the passphrase")
    assert e.value.kind == "wrong_passphrase" and "does not open" in str(e.value)
    assert not store.available() and store.problem is e.value
    with pytest.raises(SecretStoreError):
        store.set("a/key", "replacement value 0000")
    assert key_file.read_bytes() == before


@pytest.mark.parametrize(
    "where",
    [
        pytest.param(-1, id="contents tag"),
        pytest.param(-30, id="contents"),
        pytest.param(len(MAGIC) + 2 + 16, id="argon2 passes in the header"),
        pytest.param(len(MAGIC) + 2, id="salt"),
        pytest.param(len(MAGIC) + 2 + 16 + 12 + 20, id="wrapped key"),
    ],
)
def test_a_changed_byte_is_refused_and_the_file_is_kept(key_file: Path, where: int) -> None:
    made(key_file).set("a/key", KEY)
    raw = bytearray(key_file.read_bytes())
    raw[where] ^= 0x01
    key_file.write_bytes(bytes(raw))
    store = FileStore(key_file)
    with pytest.raises(FileStoreError) as e:
        store.unlock(PHRASE)
    # A changed header or wrapped key reads as the wrong passphrase; GCM can't tell them apart.
    assert e.value.kind in {"damaged", "wrong_passphrase"}
    assert key_file.read_bytes() == bytes(raw)


def test_a_file_changed_after_unlock_is_never_overwritten(key_file: Path) -> None:
    store = made(key_file)
    store.set("a/key", KEY)
    raw = bytearray(key_file.read_bytes())
    raw[-1] ^= 0x01  # the GCM tag of the contents
    key_file.write_bytes(bytes(raw))
    assert store.get("a/key") is None
    with pytest.raises(FileStoreError) as e:
        store.set("b/key", "another value 00000000")
    assert e.value.kind == "damaged" and "integrity" in str(e.value)
    assert key_file.read_bytes() == bytes(raw)


@pytest.mark.parametrize(
    "blob",
    [
        b"",
        b"age-encryption.org/v1\n",
        MAGIC,
        MAGIC + bytes([1, BY_PASSPHRASE]) + b"\0" * 10,
        MAGIC + bytes([1, 9]) + b"\0" * 200,
        MAGIC + bytes([1, BY_KEYCHAIN]) + b"\0" * 20,
    ],
)
def test_truncated_or_foreign_bytes_are_damaged(key_file: Path, blob: bytes) -> None:
    key_file.parent.mkdir(parents=True)
    key_file.write_bytes(blob)
    key_file.chmod(0o600)
    with pytest.raises(FileStoreError) as e:
        FileStore(key_file).unlock(PHRASE)
    assert e.value.kind == "damaged"
    assert key_file.read_bytes() == blob


def test_a_newer_format_says_to_update(key_file: Path) -> None:
    made(key_file)
    raw = bytearray(key_file.read_bytes())
    raw[len(MAGIC)] = 2
    key_file.write_bytes(bytes(raw))
    with pytest.raises(FileStoreError, match="newer Trillion"):
        FileStore(key_file).unlock(PHRASE)


def test_argon2_settings_out_of_range_are_refused_before_deriving(key_file: Path) -> None:
    made(key_file)
    raw = bytearray(key_file.read_bytes())
    at = len(MAGIC) + 2 + 16 + 4  # the memory field
    raw[at : at + 4] = (64 * 1024 * 1024).to_bytes(4, "big")  # 64 GiB
    key_file.write_bytes(bytes(raw))
    with pytest.raises(FileStoreError, match="out of range"):
        FileStore(key_file).unlock(PHRASE)


# ── Permissions ──────────────────────────────────────────────────────────


@posix_only
def test_the_file_and_each_rewrite_are_owner_only(key_file: Path) -> None:
    store = made(key_file)
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    store.set("a/key", KEY)
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600


@posix_only
@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644, 0o660])
def test_a_file_others_can_read_is_refused_with_the_fix(key_file: Path, mode: int) -> None:
    made(key_file).set("a/key", KEY)
    key_file.chmod(mode)
    store = FileStore(key_file)
    with pytest.raises(FileStoreError) as e:
        store.unlock(PHRASE)
    assert e.value.kind == "exposed" and f"{mode:o}" in str(e.value)
    assert "chmod 600" in e.value.fix
    assert stat.S_IMODE(key_file.stat().st_mode) == mode  # never changed for the user


@posix_only
def test_a_file_that_went_wide_after_unlock_is_no_longer_read(key_file: Path) -> None:
    store = made(key_file)
    store.set("a/key", KEY)
    key_file.chmod(0o644)
    assert store.get("a/key") is None
    with pytest.raises(FileStoreError):
        store.set("a/key", KEY)


@posix_only
def test_a_link_in_place_of_the_file_is_not_followed(key_file: Path, tmp_path: Path) -> None:
    real = tmp_path / "elsewhere.age"
    made(real)
    key_file.parent.mkdir(parents=True)
    key_file.symlink_to(real)
    with pytest.raises(FileStoreError) as e:
        FileStore(key_file).unlock(PHRASE)
    assert e.value.kind == "exposed"


def test_the_windows_hook_is_quiet_where_it_does_not_apply(key_file: Path) -> None:
    made(key_file)
    fallback._owner_only_windows(key_file)  # off Windows, or before owner_only lands


# ── Atomic writes ────────────────────────────────────────────────────────


def test_a_write_that_fails_leaves_the_old_file_and_no_temporary_file(
    key_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = made(key_file)
    store.set("a/key", KEY)
    before = key_file.read_bytes()

    def broken(src: object, dst: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(fallback.os, "replace", broken)
    with pytest.raises(SecretStoreError, match="refused to store b/key"):
        store.set("b/key", "another value 00000000")
    assert key_file.read_bytes() == before
    assert sorted(p.name for p in key_file.parent.iterdir()) == ["secrets.age"]


def test_a_write_goes_through_a_temporary_file_beside_it(
    key_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = made(key_file)
    seen: list[tuple[str, str]] = []
    real = os.replace

    def watch(src: str, dst: str) -> None:
        seen.append((Path(src).name, Path(dst).name))
        if os.name != "nt":
            assert stat.S_IMODE(os.stat(src).st_mode) == 0o600
        real(src, dst)

    monkeypatch.setattr(fallback.os, "replace", watch)
    store.set("a/key", KEY)
    ((temp, final),) = seen
    assert temp.startswith(".secrets.age.") and temp.endswith(".tmp") and final == "secrets.age"


# ── Unlocked by the keychain ─────────────────────────────────────────────


def test_a_usable_keychain_wraps_the_data_key(key_file: Path) -> None:
    keychain = MemoryStore()
    store = create(key_file, keychain=keychain, passphrase=lambda: pytest.fail("not asked"))
    assert store.unlocked_by == "keychain"
    assert key_file.read_bytes()[len(MAGIC) + 1] == BY_KEYCHAIN
    wrapping = keychain.get(fallback.WRAP_NAME)
    assert wrapping and wrapping.encode() not in key_file.read_bytes()
    store.set("a/key", KEY)
    again = FileStore(key_file, keychain=keychain)
    again.try_unlock()  # no passphrase, no prompt
    assert again.get("a/key") == KEY


def test_a_keychain_that_lost_the_key_is_a_clear_error(key_file: Path) -> None:
    keychain = MemoryStore()
    create(key_file, keychain=keychain).set("a/key", KEY)
    before = key_file.read_bytes()
    keychain.delete(fallback.WRAP_NAME)
    store = FileStore(key_file, keychain=keychain)
    store.try_unlock()
    assert not store.available()
    assert store.problem is not None and store.problem.kind == "no_key"
    keychain.set(fallback.WRAP_NAME, "bm90IGEga2V5")  # base64, but the wrong length
    with pytest.raises(FileStoreError):
        store.unlock()
    keychain.set(fallback.WRAP_NAME, "A" * 43 + "=")  # a key, but not this file's
    with pytest.raises(FileStoreError) as e:
        store.unlock()
    assert e.value.kind == "damaged"
    assert key_file.read_bytes() == before


def test_a_keychain_that_refuses_the_write_falls_back_to_the_passphrase(key_file: Path) -> None:
    store = create(key_file, keychain=RefusingKeychain(), passphrase=lambda: PHRASE, params=FAST)
    assert store.unlocked_by == "passphrase"
    assert key_file.read_bytes()[len(MAGIC) + 1] == BY_PASSPHRASE


def test_an_unusable_keychain_is_not_written_to(key_file: Path) -> None:
    keychain = MemoryStore(usable=False)
    store = create(key_file, keychain=keychain, passphrase=lambda: PHRASE, params=FAST)
    assert store.unlocked_by == "passphrase" and keychain.values() == []


# ── Choosing the store, and unlocking at start ───────────────────────────


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_with_no_file_the_keychain_is_used_exactly_as_before(
    home: Path, no_keychain: MemoryStore
) -> None:
    chosen = choose(passphrase=PHRASE)
    assert chosen is no_keychain
    assert not chosen.available()  # no keychain: keys refused, as before
    assert not (home / "secrets.age").exists()


def test_with_the_file_it_is_the_store_and_never_the_keychain(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keychain = MemoryStore()
    keychain.set("a/key", "from the keychain 0000")
    monkeypatch.setattr("trillion.secrets.store.KeychainStore", lambda: keychain)
    made(home / "secrets.age").set("a/key", KEY)
    chosen = choose(passphrase=PHRASE)
    assert isinstance(chosen, FileStore) and chosen.get("a/key") == KEY
    locked = choose()  # no passphrase and nobody to ask: locked, not the keychain
    assert isinstance(locked, FileStore) and not locked.available()
    assert locked.get("a/key") is None
    with pytest.raises(SecretStoreError, match="needs its passphrase"):
        locked.set("a/key", "something new 000000")


def test_a_terminal_is_asked_up_to_three_times(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    made(home / "secrets.age").set("a/key", KEY)
    answers = iter(["wrong one", "wrong two", PHRASE])
    chosen = choose(ask=lambda prompt: next(answers))
    assert chosen.available() and chosen.get("a/key") == KEY
    assert capsys.readouterr().err.count("does not open") == 2
    always_wrong = choose(ask=lambda prompt: "wrong")
    assert not always_wrong.available()
    assert isinstance(always_wrong, FileStore) and always_wrong.problem is not None
    assert always_wrong.problem.kind == "wrong_passphrase"
    gave_up = choose(ask=lambda prompt: None)
    assert not gave_up.available()


def test_the_passphrase_is_taken_out_of_the_environment() -> None:
    env = {PASSPHRASE_ENV: PHRASE, "OTHER": "x"}
    assert fallback.take_passphrase(env) == PHRASE
    assert env == {"OTHER": "x"}
    assert fallback.take_passphrase(env) is None
    assert fallback.take_passphrase({PASSPHRASE_ENV: ""}) is None


def test_a_passphrase_variable_counts_as_a_credential_for_the_scrubber() -> None:
    from trillion.secrets.redact import env_secrets

    assert env_secrets({PASSPHRASE_ENV: PHRASE}) == [PHRASE]


def test_environment_keys_still_win_over_the_file(key_file: Path) -> None:
    store = made(key_file)
    store.set(config.secret_name("anthropic"), KEY)
    settings = config.ModelSettings(kind="anthropic", model="m")
    _, source = config.resolve(settings, store, {config.ANTHROPIC_KEY: "sk-" + "from env " * 3})
    assert source == "env"
    _, source = config.resolve(settings, store, {})
    assert source == "settings"


def test_nothing_logged_holds_a_value_or_the_passphrase(
    key_file: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    store = create(key_file, keychain=RefusingKeychain(), passphrase=lambda: PHRASE, params=FAST)
    store.set("a/key", KEY)
    FileStore(key_file).try_unlock("wrong")
    if os.name != "nt":
        key_file.chmod(0o644)
    store.get("a/key")
    text = caplog.text
    assert KEY not in text and PHRASE not in text


# ── trillion doctor ──────────────────────────────────────────────────────


def test_doctor_names_the_unlocked_file_and_never_a_value(key_file: Path) -> None:
    store = made(key_file)
    store.set("anthropic/api_key", KEY)
    check = doctor.check_keychain(store, "macOS Keyring")
    assert check.name == "Key file" and check.status == "ok"
    assert str(key_file) in check.detail and "with a passphrase" in check.detail
    keychain = MemoryStore()
    other = create(key_file.parent / "k2.age", keychain=keychain)
    assert "by the system keychain" in doctor.check_keychain(other).detail
    assert KEY not in doctor.render([check])


def test_doctor_says_a_locked_file_turns_keyed_features_off(key_file: Path) -> None:
    made(key_file)
    locked = choose(path=key_file)
    check = doctor.check_keychain(locked)
    assert check.status == "warn" and "features that need a key are off" in check.detail
    assert PASSPHRASE_ENV in check.fix
    wrong = choose(path=key_file, passphrase=WRONG)
    check = doctor.check_keychain(wrong)
    assert check.status == "warn" and "does not open" in check.detail


@posix_only
def test_doctor_fails_a_file_others_can_read_with_the_fix(key_file: Path) -> None:
    made(key_file)
    key_file.chmod(0o644)
    check = doctor.check_keychain(choose(path=key_file, passphrase=PHRASE))
    assert check.status == "fail" and "chmod 600" in check.fix


def test_doctor_secrets_lists_the_key_from_the_file_without_its_value(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake(kind: str, base_url: str | None, api_key: str | None) -> list:
        return []

    monkeypatch.setattr(config, "list_models", fake)
    store = made(home / "secrets.age")
    store.set(config.secret_name("anthropic"), KEY)
    found = asyncio.run(
        doctor.examine(
            doctor.Options(port=1, host="127.0.0.1", offline=True, secrets=True),
            secrets=store,
            env={},
            page=Path("/built"),
        )
    )
    shown = doctor.render(found)
    assert "Key file" in shown and "Keychain" not in shown
    assert KEY not in shown


def test_doctor_points_a_machine_with_no_keychain_at_the_file() -> None:
    check = doctor.check_keychain(MemoryStore(usable=False))
    assert "trillion setup --secrets-file" in check.fix


# ── trillion setup ───────────────────────────────────────────────────────


def test_setup_makes_the_file_from_the_variable_and_removes_it(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trillion.cli.main import main

    monkeypatch.setitem(fallback.create.__kwdefaults__, "params", FAST)  # quick Argon2id
    monkeypatch.setenv(PASSPHRASE_ENV, PHRASE)
    main(["setup", "--no-run", "--offline", "--secrets-file", "--port", "1"])
    assert PASSPHRASE_ENV not in os.environ
    out = capsys.readouterr().out
    assert "unlocked by your passphrase" in out and PHRASE not in out
    path = home / "secrets.age"
    store = FileStore(path)
    store.unlock(PHRASE)
    assert store.names() == []
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # Again: the file is kept as it is and named.
    before = path.read_bytes()
    main(["setup", "--no-run", "--offline", "--port", "1"])
    assert "keys are kept in the encrypted file" in capsys.readouterr().out
    assert path.read_bytes() == before


def test_setup_without_the_flag_or_a_terminal_makes_nothing(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from trillion.cli.main import main

    main(["setup", "--no-run", "--offline", "--port", "1"])
    assert not (home / "secrets.age").exists()
    assert "keys will be kept" not in capsys.readouterr().out


def test_setup_with_the_flag_and_no_passphrase_says_how(
    home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from trillion.cli.main import main

    monkeypatch.delenv(PASSPHRASE_ENV, raising=False)
    main(["setup", "--no-run", "--offline", "--secrets-file", "--port", "1"])
    assert not (home / "secrets.age").exists()
    assert "no encrypted key file made" in capsys.readouterr().out


def test_run_names_the_key_file_state(key_file: Path) -> None:
    from trillion.cli.main import _keys_line

    assert _keys_line(KeychainStore()) == ""
    store = made(key_file)
    assert "encrypted file, unlocked" in _keys_line(store)
    assert "locked (wrong passphrase)" in _keys_line(choose(path=key_file, passphrase=WRONG))


def test_a_locked_file_still_opens_the_runtime(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ONB-4: a key file that can't be opened never stops the app from starting."""
    from trillion.api.runtime import open_runtime

    made(home / "secrets.age").set(config.secret_name("anthropic"), KEY)

    async def go() -> None:
        rt = await open_runtime(persona=tmp_path / "persona", env={})
        try:
            assert isinstance(rt.secrets, FileStore) and not rt.secrets.available()
        finally:
            await rt.close()

    asyncio.run(go())
