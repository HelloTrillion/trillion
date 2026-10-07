"""One interface over the OS keychain (SEC-1).

macOS Keychain, Windows Credential Manager, or Secret Service on Linux, through
the `keyring` library. Entries are namespaced `trillion/<connector>/<field>`.
Values are fetched when a request is made and never cached here (SEC-6).
"""

from __future__ import annotations

import logging
from typing import Protocol

import keyring
import keyring.errors
from keyring.backends import fail

logger = logging.getLogger(__name__)

SERVICE = "trillion"


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
            return keyring.get_password(SERVICE, name)
        except keyring.errors.KeyringError as e:
            logger.warning("keychain read of %s failed: %s", name, type(e).__name__)
            return None

    def set(self, name: str, value: str) -> None:
        if not self.available():
            raise SecretStoreError("this machine has no system keychain")
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
        return self._values.get(name)

    def set(self, name: str, value: str) -> None:
        if not self._usable:
            raise SecretStoreError("this machine has no system keychain")
        self._values[name] = value

    def delete(self, name: str) -> None:
        self._values.pop(name, None)

    def values(self) -> list[str]:
        return list(self._values.values())
