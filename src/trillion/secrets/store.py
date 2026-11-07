"""One interface over the OS keychain (SEC-1).

macOS Keychain, Windows Credential Manager, or Secret Service on Linux, through
the `keyring` library. Entries are namespaced `trillion/<connector>/<field>`.
Values are fetched when a request is made and never cached here (SEC-6).

Windows Credential Manager keeps at most 2,560 bytes in one entry, and `keyring`
writes text there as UTF-16. A sign-in's tokens can be longer, so on Windows a long
value is kept in numbered pieces beside its own entry, which says how many there are.

The values currently stored are also held in memory, by name, for one purpose:
so the log scrubber can remove them (SEC-5) whichever connector asked for them,
and however late. A value replaced or removed is dropped from that list too.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from typing import Any, Protocol

import keyring
import keyring.errors
from keyring.backends import fail

logger = logging.getLogger(__name__)

SERVICE = "trillion"

# JSON entries hold labels (emails, scopes, expiry) beside the secrets, so only
# values whose own field is named like a secret are noted.
_SECRET_FIELD = re.compile(r"(?:^|_)(?:token|secret|key|password|verifier)$")
_current: dict[str, tuple[str, ...]] = {}
_versions: dict[str, int] = {}
_ordered: tuple[str, ...] = ()
_lock = threading.Lock()


def _field_name(k: str) -> str:
    # apiKey and api_key read the same.
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", k).lower()


def _secret_leaves(data: Any) -> list[str]:
    out: list[str] = []
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, str):
                if isinstance(k, str) and _SECRET_FIELD.search(_field_name(k)):
                    out.append(v)
            else:
                out += _secret_leaves(v)
    elif isinstance(data, list):
        for v in data:
            out += _secret_leaves(v)
    return out


def _held(value: str) -> tuple[str, ...]:
    found = [value]
    if value.lstrip().startswith(("{", "[")):
        try:
            found += _secret_leaves(json.loads(value))
        except ValueError:
            pass
    return tuple(dict.fromkeys(found))


def _version(name: str) -> int:
    """Taken before a read, so the read can tell whether a write overtook it."""
    with _lock:
        return _versions.get(name, 0)


def _note(name: str, value: str | None, *, read_at: int | None = None) -> None:
    """What is stored under `name` now. None means nothing is.

    A write passes no `read_at` and always wins. A read passes the version it began
    at, and is dropped when a write or delete came in since: its value is older.
    """
    global _ordered
    with _lock:
        if read_at is None:
            _versions[name] = _versions.get(name, 0) + 1
        elif _versions.get(name, 0) != read_at:
            return
        if value:
            _current[name] = _held(value)
        else:
            _current.pop(name, None)
        # Longest first, so a value holding a shorter one is scrubbed whole.
        everything = {v for vs in _current.values() for v in vs}
        _ordered = tuple(sorted(everything, key=len, reverse=True))


def handed_out() -> tuple[str, ...]:
    """The secret values currently stored that this process has read or written."""
    return _ordered


def forget_all() -> None:
    """Tests only: start from an empty list."""
    global _ordered
    with _lock:
        _current.clear()
        _versions.clear()
        _ordered = ()


class SecretStoreError(RuntimeError):
    """The keychain refused. The message never contains a value."""


class SecretStore(Protocol):
    def available(self) -> bool:
        """Whether there is a real keychain to write to."""
        ...

    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


# CRED_MAX_CREDENTIAL_BLOB_SIZE is 2,560 bytes. In UTF-16 a character takes two or
# four, so 640 characters always fit.
WINDOWS_PIECE = 640
# A value kept in pieces: its own entry holds this and the number of pieces.
_SPLIT = "trillion-split:v1:"


def _piece_name(name: str, i: int) -> str:
    return f"{name}~piece-{i + 1}"


def _pieces_in(head: str | None) -> int:
    if head is None or not head.startswith(_SPLIT):
        return 0
    count = head.removeprefix(_SPLIT)
    return int(count) if count.isdigit() else 0


class KeychainStore:
    """The system keychain, through `keyring`.

    A backend raises errors of its own beside keyring's (pywintypes.error from
    Credential Manager, D-Bus errors from Secret Service), so every failure is caught
    here; only its class name is logged, never a value."""

    def __init__(self, service: str = SERVICE, *, piece: int | None = None) -> None:
        self._service = service
        # Longest value kept in one entry. None: no limit, except on Windows.
        self._piece = (
            piece if piece is not None else (WINDOWS_PIECE if sys.platform == "win32" else None)
        )

    def available(self) -> bool:
        backend = keyring.get_keyring()
        # `fail` is what keyring falls back to on a machine with no keychain at all.
        # A backend that writes plain text to disk would have priority below 1.
        return not isinstance(backend, fail.Keyring) and backend.priority >= 1

    def get(self, name: str) -> str | None:
        began = _version(name)
        try:
            value = keyring.get_password(self._service, name)
            count = _pieces_in(value)
            if count:
                pieces = [
                    keyring.get_password(self._service, _piece_name(name, i)) for i in range(count)
                ]
                if any(p is None for p in pieces):
                    logger.warning("keychain entry %s is missing a piece", name)
                    return None
                value = "".join(p for p in pieces if p is not None)
        except Exception as e:
            logger.warning("keychain read of %s failed: %s", name, type(e).__name__)
            return None
        _note(name, value, read_at=began)
        return value

    def set(self, name: str, value: str) -> None:
        if not self.available():
            raise SecretStoreError("this machine has no system keychain")
        # Noted first, and kept if the write fails: the caller still holds the value.
        _note(name, value)
        # A value that looks like a list of pieces is stored as one, so it reads back whole.
        split = (self._piece is not None and len(value) > self._piece) or value.startswith(_SPLIT)
        try:
            before = 0
            if self._piece is not None or split:
                before = _pieces_in(keyring.get_password(self._service, name))
            if split:
                size = self._piece or WINDOWS_PIECE
                pieces = [value[i : i + size] for i in range(0, len(value), size)]
                for i, piece in enumerate(pieces):
                    keyring.set_password(self._service, _piece_name(name, i), piece)
                keyring.set_password(self._service, name, f"{_SPLIT}{len(pieces)}")
            else:
                pieces = []
                keyring.set_password(self._service, name, value)
            for i in range(len(pieces), before):
                self._remove(_piece_name(name, i))
        except Exception as e:
            raise SecretStoreError(f"the keychain refused to store {name}") from e

    def delete(self, name: str) -> None:
        try:
            count = _pieces_in(keyring.get_password(self._service, name))
            self._remove(name)
            for i in range(count):
                self._remove(_piece_name(name, i))
        except Exception as e:
            raise SecretStoreError(f"the keychain refused to remove {name}") from e
        _note(name, None)

    def _remove(self, entry: str) -> None:
        try:
            keyring.delete_password(self._service, entry)
        except keyring.errors.PasswordDeleteError:
            pass  # already gone


class MemoryStore:
    """For tests and for nothing else: values vanish with the process."""

    def __init__(self, *, usable: bool = True) -> None:
        self._usable = usable
        self._values: dict[str, str] = {}

    def available(self) -> bool:
        return self._usable

    def get(self, name: str) -> str | None:
        began = _version(name)
        value = self._values.get(name)
        _note(name, value, read_at=began)
        return value

    def set(self, name: str, value: str) -> None:
        if not self._usable:
            raise SecretStoreError("this machine has no system keychain")
        self._values[name] = value
        _note(name, value)

    def delete(self, name: str) -> None:
        self._values.pop(name, None)
        _note(name, None)

    def values(self) -> list[str]:
        return list(self._values.values())
