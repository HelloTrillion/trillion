"""One interface over the OS keychain (SEC-1).

macOS Keychain, Windows Credential Manager, or Secret Service on Linux, through
the `keyring` library. Entries are namespaced `trillion/<connector>/<field>`.
Values are fetched when a request is made and never cached here (SEC-6).

Every value a store reads or writes is noted in a process registry, so the log
scrubber knows it (SEC-5) whichever connector asked for it, and however late.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Protocol

import keyring
import keyring.errors
from keyring.backends import fail

logger = logging.getLogger(__name__)

SERVICE = "trillion"

# JSON entries hold labels (emails, scopes, expiry) beside the secrets, so only
# fields with names like these are noted.
_SECRET_FIELDS = ("token", "secret", "key", "password", "verifier")
_seen: set[str] = set()
_seen_lock = threading.Lock()


def _secret_leaves(data: Any, secret: bool = False) -> list[str]:
    if isinstance(data, dict):
        out: list[str] = []
        for k, v in data.items():
            named = isinstance(k, str) and any(f in k.lower() for f in _SECRET_FIELDS)
            out += _secret_leaves(v, secret or named)
        return out
    if isinstance(data, list):
        return [leaf for v in data for leaf in _secret_leaves(v, secret)]
    return [data] if secret and isinstance(data, str) else []


def remember(value: str | None) -> str | None:
    """Note a value the store handed out or took in. Returns it unchanged."""
    if not value:
        return value
    found = [value]
    if value.lstrip().startswith(("{", "[")):
        try:
            found += _secret_leaves(json.loads(value))
        except ValueError:
            pass
    with _seen_lock:
        _seen.update(found)
    return value


def handed_out() -> list[str]:
    """Every secret value this process has read from or written to a store."""
    with _seen_lock:
        return list(_seen)


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
        try:
            return remember(keyring.get_password(SERVICE, name))
        except keyring.errors.KeyringError as e:
            logger.warning("keychain read of %s failed: %s", name, type(e).__name__)
            return None

    def set(self, name: str, value: str) -> None:
        if not self.available():
            raise SecretStoreError("this machine has no system keychain")
        remember(value)
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


class MemoryStore:
    """For tests and for nothing else: values vanish with the process."""

    def __init__(self, *, usable: bool = True) -> None:
        self._usable = usable
        self._values: dict[str, str] = {}

    def available(self) -> bool:
        return self._usable

    def get(self, name: str) -> str | None:
        return remember(self._values.get(name))

    def set(self, name: str, value: str) -> None:
        if not self._usable:
            raise SecretStoreError("this machine has no system keychain")
        remember(value)
        self._values[name] = value

    def delete(self, name: str) -> None:
        self._values.pop(name, None)

    def values(self) -> list[str]:
        return list(self._values.values())
