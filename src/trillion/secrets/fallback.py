"""The encrypted key file, for a machine with no usable keychain (SEC-2).

One file, `secrets.age` in Trillion's own folder, used when it exists and only then
(the spec's D1): `choose` returns it, or the keychain as before. It is made only by
`trillion setup`, so keys never move between stores without the user knowing.

The bytes are our own documented container, not the age format (D6):

    header   b"TRLSEC" | version (1) | unlock (1 keychain, 2 passphrase)
             [passphrase only: salt (16) | passes | memory KiB | lanes, each >I]
    wrapped  nonce (12) | AES-256-GCM(kek, data key) (48), header as associated data
    body     nonce (12) | AES-256-GCM(data key, JSON {name: value}),
             header + wrapped as associated data

The key-encryption key is random and kept in the keychain, or derived from the
user's passphrase with Argon2id. All crypto is the `cryptography` library's.

A value is read from the file each time it is asked for, never cached (SEC-6); only
the data key is held, for this process's life, once the file is unlocked.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import importlib
import json
import logging
import os
import stat
import struct
import sys
import tempfile
import threading
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from trillion import paths
from trillion.secrets import store as _store
from trillion.secrets.store import SecretStore, SecretStoreError

logger = logging.getLogger(__name__)

PASSPHRASE_ENV = "TRILLION_SECRETS_PASSPHRASE"  # noqa: S105 - a variable name, not a value
# The keychain entry holding the key that unlocks the file (`trillion/secrets-file/key`).
WRAP_NAME = "secrets-file/key"

MAGIC = b"TRLSEC"
VERSION = 1
BY_KEYCHAIN = 1
BY_PASSPHRASE = 2
KEY_BYTES = 32
NONCE_BYTES = 12
SALT_BYTES = 16
_TAG_BYTES = 16
_PARAMS = struct.Struct(">III")
_WRAPPED_BYTES = NONCE_BYTES + KEY_BYTES + _TAG_BYTES
TRIES = 3

Unlock = Literal["keychain", "passphrase"]
Kind = Literal["locked", "wrong_passphrase", "damaged", "exposed", "no_key", "exists"]


@dataclass(frozen=True)
class Argon2Params:
    """RFC 9106's second recommended option: 64 MiB, 3 passes, 4 lanes."""

    passes: int = 3
    memory_kib: int = 64 * 1024
    lanes: int = 4

    def sane(self) -> bool:
        # A header asking for more is damaged, not a reason to take the machine's memory.
        return (
            1 <= self.passes <= 64
            and 1 <= self.lanes <= 64
            and 8 * self.lanes <= self.memory_kib <= 4 * 1024 * 1024
        )


DEFAULT_PARAMS = Argon2Params()


class FileStoreError(SecretStoreError):
    """The key file could not be used. The message never holds a value or a passphrase."""

    def __init__(self, kind: Kind, message: str, fix: str = "") -> None:
        super().__init__(message)
        self.kind: Kind = kind
        self.fix = fix


def _shown(path: Path) -> str:
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


def _damaged(path: Path, why: str) -> FileStoreError:
    return FileStoreError(
        "damaged",
        f"{_shown(path)} is damaged or was changed ({why})",
        "Restore it from a backup. It was left as it is. To start again, move it "
        "aside and run: trillion setup --secrets-file (the keys in it are lost).",
    )


# ── The container ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Sealed:
    header: bytes
    unlock: Unlock
    salt: bytes
    params: Argon2Params
    wrapped: bytes  # nonce + wrapped data key
    body: bytes  # nonce + ciphertext


def _header(unlock: Unlock, salt: bytes = b"", params: Argon2Params = DEFAULT_PARAMS) -> bytes:
    head = MAGIC + bytes([VERSION, BY_KEYCHAIN if unlock == "keychain" else BY_PASSPHRASE])
    if unlock == "passphrase":
        head += salt + _PARAMS.pack(params.passes, params.memory_kib, params.lanes)
    return head


def _parse(blob: bytes, path: Path) -> _Sealed:
    if not blob.startswith(MAGIC):
        raise _damaged(path, "not a Trillion key file")
    at = len(MAGIC)
    if len(blob) < at + 2:
        raise _damaged(path, "too short")
    if blob[at] != VERSION:
        raise FileStoreError(
            "damaged",
            f"{_shown(path)} was made by a newer Trillion (format {blob[at]})",
            "Update Trillion. The file was left as it is.",
        )
    how = blob[at + 1]
    at += 2
    salt, params = b"", DEFAULT_PARAMS
    if how == BY_PASSPHRASE:
        unlock: Unlock = "passphrase"
        if len(blob) < at + SALT_BYTES + _PARAMS.size:
            raise _damaged(path, "too short")
        salt = blob[at : at + SALT_BYTES]
        params = Argon2Params(*_PARAMS.unpack_from(blob, at + SALT_BYTES))
        at += SALT_BYTES + _PARAMS.size
        if not params.sane():
            raise _damaged(path, "its key settings are out of range")
    elif how == BY_KEYCHAIN:
        unlock = "keychain"
    else:
        raise _damaged(path, "unknown unlock method")
    if len(blob) < at + _WRAPPED_BYTES + NONCE_BYTES + _TAG_BYTES:
        raise _damaged(path, "too short")
    return _Sealed(
        header=blob[:at],
        unlock=unlock,
        salt=salt,
        params=params,
        wrapped=blob[at : at + _WRAPPED_BYTES],
        body=blob[at + _WRAPPED_BYTES :],
    )


def derive(passphrase: str, salt: bytes, params: Argon2Params) -> bytes:
    """The key-encryption key from a passphrase (Argon2id)."""
    kdf = Argon2id(
        salt=salt,
        length=KEY_BYTES,
        iterations=params.passes,
        lanes=params.lanes,
        memory_cost=params.memory_kib,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def _wrap(kek: bytes, data_key: bytes, header: bytes) -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    return nonce + AESGCM(kek).encrypt(nonce, data_key, header)


def _unwrap(kek: bytes, sealed: _Sealed) -> bytes:
    nonce, wrapped = sealed.wrapped[:NONCE_BYTES], sealed.wrapped[NONCE_BYTES:]
    return AESGCM(kek).decrypt(nonce, wrapped, sealed.header)


def _seal_body(data_key: bytes, values: dict[str, str], header: bytes, wrapped: bytes) -> bytes:
    nonce = os.urandom(NONCE_BYTES)  # fresh on every write
    plain = json.dumps(values, sort_keys=True).encode("utf-8")
    return nonce + AESGCM(data_key).encrypt(nonce, plain, header + wrapped)


def _open_body(data_key: bytes, sealed: _Sealed, path: Path) -> dict[str, str]:
    nonce, body = sealed.body[:NONCE_BYTES], sealed.body[NONCE_BYTES:]
    try:
        plain = AESGCM(data_key).decrypt(nonce, body, sealed.header + sealed.wrapped)
    except InvalidTag:
        raise _damaged(path, "it does not pass its integrity check") from None
    try:
        values = json.loads(plain)
    except ValueError:
        raise _damaged(path, "its contents are not readable") from None
    if not isinstance(values, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in values.items()
    ):
        raise _damaged(path, "its contents are not readable")
    return values


# ── The file on disk ─────────────────────────────────────────────────────


def _owner_only_windows(path: Path) -> None:
    """An owner-only access list on Windows (spec D10), from `trillion.owner_only`.

    HOOK: that module arrives with `windows/hardening` (SUR-3c). Until it is on main
    this finds nothing and Windows relies on the home folder's own permissions."""
    if os.name != "nt":
        return
    try:
        owner_only = importlib.import_module("trillion.owner_only")
    except ImportError:
        return
    if not owner_only.restrict(path):
        logger.warning("the key file could not be made owner-only on Windows")


def read_private(path: Path) -> bytes:
    """The file's bytes, refused if anyone but its owner could read it (SEC-2).

    Opened without following a link, and judged on the open handle, so nothing can
    be swapped in between the check and the read."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as e:
        raise FileStoreError(
            "exposed",
            f"{_shown(path)} could not be opened ({e.strerror or 'refused'})",
            f"It must be a plain file you own. Check: ls -l {_shown(path)}",
        ) from None
    with os.fdopen(fd, "rb") as f:
        if sys.platform != "win32":
            info = os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise FileStoreError(
                    "exposed", f"{_shown(path)} is not a plain file", "Move it aside."
                )
            if info.st_uid != os.geteuid():
                raise FileStoreError(
                    "exposed",
                    f"{_shown(path)} belongs to another user",
                    f"Make it yours: chown $(id -u) {_shown(path)}",
                )
            if info.st_mode & 0o077:
                raise FileStoreError(
                    "exposed",
                    f"{_shown(path)} can be read by other users "
                    f"(mode {stat.S_IMODE(info.st_mode):o}), so it was not opened",
                    f"Fix it with: chmod 600 {_shown(path)}",
                )
        return f.read()


def write_private(path: Path, data: bytes) -> None:
    """Replace the file whole (OPS-2): a temporary file beside it, flushed, renamed over.

    A failure at any point leaves the old file as it was."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            if os.name != "nt":
                os.fchmod(f.fileno(), 0o600)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        _owner_only_windows(Path(tmp))
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    if os.name != "nt":
        # The rename itself, flushed, so a power cut can't bring back the old file.
        with contextlib.suppress(OSError):
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)


# ── The store ────────────────────────────────────────────────────────────


class FileStore:
    """`SecretStore` over the encrypted file. Locked until `unlock` succeeds."""

    def __init__(self, path: Path, *, keychain: SecretStore | None = None) -> None:
        self.path = path
        self._keychain = keychain
        self._data_key: bytes | None = None
        self._unlock: Unlock | None = None
        self.problem: FileStoreError | None = FileStoreError("locked", f"{_shown(path)} is locked")
        self._lock = threading.Lock()

    # What doctor and `trillion run` say.

    @property
    def unlocked_by(self) -> Unlock | None:
        return self._unlock if self._data_key is not None else None

    def unlock_method(self) -> Unlock:
        """How this file is unlocked, read from its header. Raises if unreadable."""
        return _parse(read_private(self.path), self.path).unlock

    def unlock(self, passphrase: str | None = None) -> None:
        """Unwrap the data key, and prove it by opening the contents. Raises
        `FileStoreError`; the file is never changed by a failure."""
        try:
            sealed = _parse(read_private(self.path), self.path)
            if sealed.unlock == "keychain":
                kek = self._keychain_key()
            elif passphrase is None:
                raise FileStoreError(
                    "locked",
                    f"{_shown(self.path)} needs its passphrase",
                    f"Start Trillion in a terminal to type it, or set {PASSPHRASE_ENV}.",
                )
            else:
                kek = derive(passphrase, sealed.salt, sealed.params)
            try:
                data_key = _unwrap(kek, sealed)
            except InvalidTag:
                if sealed.unlock == "passphrase":
                    raise FileStoreError(
                        "wrong_passphrase",
                        f"the passphrase does not open {_shown(self.path)}",
                        "Try again with the passphrase you chose in setup. "
                        "The file was left as it is.",
                    ) from None
                raise _damaged(self.path, "the keychain's key does not open it") from None
            _open_body(data_key, sealed, self.path)
        except FileNotFoundError:
            self.problem = FileStoreError("damaged", f"{_shown(self.path)} is gone")
            raise self.problem from None
        except FileStoreError as e:
            self.problem = e
            raise
        self._data_key, self._unlock, self.problem = data_key, sealed.unlock, None

    def _keychain_key(self) -> bytes:
        raw = self._keychain.get(WRAP_NAME) if self._keychain is not None else None
        missing = FileStoreError(
            "no_key",
            f"the system keychain no longer holds the key that opens {_shown(self.path)}",
            "Unlock or restore the keychain entry trillion/secrets-file/key. "
            "The file was left as it is.",
        )
        if not raw:
            raise missing
        try:
            kek = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            raise missing from None
        if len(kek) != KEY_BYTES:
            raise missing
        return kek

    def try_unlock(
        self,
        passphrase: str | None = None,
        ask: Callable[[str], str | None] | None = None,
    ) -> None:
        """Unlock if it can, and never raise: a locked file starts the app at the rung
        it would have without keys (ONB-4). `ask` prompts for a passphrase on a
        terminal, up to three times."""
        try:
            if passphrase is not None or ask is None:
                self.unlock(passphrase)
                return
            if self.unlock_method() == "keychain":
                self.unlock()
                return
            for _ in range(TRIES):
                typed = ask(f"Passphrase for {_shown(self.path)}: ")
                if not typed:
                    return
                try:
                    self.unlock(typed)
                    return
                except FileStoreError as e:
                    if e.kind != "wrong_passphrase":
                        raise
                    sys.stderr.write(f"trillion: {e}\n")
        except FileStoreError as e:
            self.problem = e
        except FileNotFoundError:
            self.problem = FileStoreError("damaged", f"{_shown(self.path)} is gone")

    # SecretStore

    def available(self) -> bool:
        return self._data_key is not None

    def _load(self) -> tuple[bytes, _Sealed, dict[str, str]]:
        if self._data_key is None:
            raise self.problem or FileStoreError("locked", f"{_shown(self.path)} is locked")
        try:
            sealed = _parse(read_private(self.path), self.path)
        except FileNotFoundError:
            raise FileStoreError("damaged", f"{_shown(self.path)} is gone") from None
        return self._data_key, sealed, _open_body(self._data_key, sealed, self.path)

    def _read(self) -> dict[str, str]:
        return self._load()[2]

    def get(self, name: str) -> str | None:
        began = _store._version(name)
        try:
            value = self._read().get(name)
        except FileStoreError as e:
            if self._data_key is not None:  # a locked file has already said why
                logger.warning("key file read of %s failed: %s", name, e.kind)
            return None
        _store._note(name, value, read_at=began)
        return value

    def _change(self, name: str, value: str | None) -> None:
        with self._lock:
            # A file that can't be opened is never overwritten (D11).
            data_key, sealed, values = self._load()
            if value is None:
                if name not in values:
                    return
                del values[name]
            else:
                values[name] = value
            body = _seal_body(data_key, values, sealed.header, sealed.wrapped)
            try:
                write_private(self.path, sealed.header + sealed.wrapped + body)
            except OSError as e:
                what = "store" if value is not None else "remove"
                raise SecretStoreError(f"the key file refused to {what} {name}") from e

    def set(self, name: str, value: str) -> None:
        if self._data_key is not None:
            _store._note(name, value)  # kept if the write fails: the caller holds it
        self._change(name, value)

    def delete(self, name: str) -> None:
        self._change(name, None)
        _store._note(name, None)

    def names(self) -> list[str]:
        """What it holds, by name only. Empty when locked."""
        try:
            return sorted(self._read())
        except FileStoreError:
            return []


def create(
    path: Path,
    *,
    keychain: SecretStore | None = None,
    passphrase: Callable[[], str | None] | None = None,
    params: Argon2Params = DEFAULT_PARAMS,
) -> FileStore:
    """Make a new, empty key file and return it unlocked.

    The data key is wrapped by a random key kept in `keychain` when it is usable and
    takes the write; otherwise by the passphrase `passphrase()` gives. Refuses to
    replace a file that is already there."""
    if os.path.lexists(path):
        raise FileStoreError("exists", f"{_shown(path)} already exists", "It was left as it is.")
    data_key = AESGCM.generate_key(bit_length=256)
    header: bytes | None = None
    wrapped = b""
    unlock: Unlock = "keychain"
    if keychain is not None and keychain.available():
        kek = AESGCM.generate_key(bit_length=256)
        try:
            keychain.set(WRAP_NAME, base64.b64encode(kek).decode("ascii"))
        except SecretStoreError:
            logger.info("the keychain refused the key file's key; using a passphrase")
        else:
            header = _header("keychain")
            wrapped = _wrap(kek, data_key, header)
    if header is None:
        given = passphrase() if passphrase is not None else None
        if not given:
            raise FileStoreError(
                "locked",
                "a passphrase is needed to make the key file",
                f"Run trillion setup --secrets-file in a terminal, or set {PASSPHRASE_ENV}.",
            )
        salt = os.urandom(SALT_BYTES)
        unlock = "passphrase"
        header = _header("passphrase", salt, params)
        wrapped = _wrap(derive(given, salt, params), data_key, header)
    write_private(path, header + wrapped + _seal_body(data_key, {}, header, wrapped))
    made = FileStore(path, keychain=keychain)
    made._data_key, made._unlock, made.problem = data_key, unlock, None
    return made


def choose(
    *,
    passphrase: str | None = None,
    ask: Callable[[str], str | None] | None = None,
    path: Path | None = None,
) -> SecretStore:
    """The store this run uses (spec D1): the key file when it exists, otherwise the
    system keychain exactly as before. The file is unlocked here if it can be; a file
    that stays locked is still returned, so nothing silently falls back to another store."""
    keychain = _store.KeychainStore()  # looked up now, so tests can stand in for it
    target = path or paths.secrets_path()
    if not os.path.lexists(target):
        return keychain
    found = FileStore(target, keychain=keychain)
    found.try_unlock(passphrase, ask)
    return found


def take_passphrase(environ: MutableMapping[str, str] | None = None) -> str | None:
    """The passphrase from the environment, removed from it at once so no tool, MCP
    server or child process inherits it (D8)."""
    env = os.environ if environ is None else environ
    return env.pop(PASSPHRASE_ENV, None) or None
