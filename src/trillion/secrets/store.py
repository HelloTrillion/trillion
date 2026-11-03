"""One interface over the OS keychain (SEC-1).

macOS Keychain, Windows Credential Manager, or Secret Service on Linux, through
the `keyring` library. Entries are namespaced `trillion/<connector>/<field>`.
Values are fetched when a request is made and never cached here (SEC-6).

The values currently stored are also held in memory, by name, for one purpose:
so the log scrubber can remove them (SEC-5) whichever connector asked for them,
and however late. A value replaced or removed is dropped from that list too.
"""

from __future__ import annotations

import json
import logging
import re
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


class KeychainStore:
    def available(self) -> bool:
        backend = keyring.get_keyring()
        # `fail` is what keyring falls back to on a machine with no keychain at all.
        # A backend that writes plain text to disk would have priority below 1.
        return not isinstance(backend, fail.Keyring) and backend.priority >= 1

    def get(self, name: str) -> str | None:
        began = _version(name)
        try:
            value = keyring.get_password(SERVICE, name)
        except keyring.errors.KeyringError as e:
            logger.warning("keychain read of %s failed: %s", name, type(e).__name__)
            return None
        _note(name, value, read_at=began)
        return value

    def set(self, name: str, value: str) -> None:
        if not self.available():
            raise SecretStoreError("this machine has no system keychain")
        # Noted first, and kept if the write fails: the caller still holds the value.
        _note(name, value)
        try:
            keyring.set_password(SERVICE, name, value)
        except keyring.errors.KeyringError as e:
            raise SecretStoreError(f"the keychain refused to store {name}") from e

    def delete(self, name: str) -> None:
        try:
            keyring.delete_password(SERVICE, name)
        except keyring.errors.PasswordDeleteError:
            pass  # already gone
        except keyring.errors.KeyringError as e:
            raise SecretStoreError(f"the keychain refused to remove {name}") from e
        _note(name, None)


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
