from __future__ import annotations

import logging
from collections.abc import Iterator

import keyring
import keyring.backend
import pytest
from keyring.backends import fail

from trillion.secrets import redact
from trillion.secrets.store import KeychainStore, MemoryStore, SecretStoreError

# Built from pieces: a key's shape, not a key, and nothing a secret scanner should flag.
SAMPLE = "sk-" + "ant-" + "test" * 6


class FakeKeychain(keyring.backend.KeyringBackend):
    priority = 5.0  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.items.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.items[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.items:
            raise keyring.errors.PasswordDeleteError("absent")
        del self.items[(service, username)]


@pytest.fixture
def keychain() -> Iterator[FakeKeychain]:
    """Never the real keychain: a test must not touch the user's credentials."""
    before = keyring.get_keyring()
    fake = FakeKeychain()
    keyring.set_keyring(fake)
    yield fake
    keyring.set_keyring(before)


def test_keys_are_namespaced_under_the_service(keychain: FakeKeychain) -> None:
    store = KeychainStore()
    assert store.available()
    store.set("anthropic/api_key", SAMPLE)
    assert keychain.items == {("trillion", "anthropic/api_key"): SAMPLE}
    assert store.get("anthropic/api_key") == SAMPLE
    assert store.get("openai/api_key") is None


def test_removing_a_key_removes_it_at_once_and_twice_is_fine(keychain: FakeKeychain) -> None:
    store = KeychainStore()
    store.set("openai/api_key", SAMPLE)
    store.delete("openai/api_key")
    store.delete("openai/api_key")
    assert keychain.items == {}


def test_without_a_keychain_nothing_is_written_anywhere() -> None:
    before = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())
    try:
        store = KeychainStore()
        assert not store.available()
        with pytest.raises(SecretStoreError) as e:
            store.set("anthropic/api_key", SAMPLE)
        assert SAMPLE not in str(e.value)
        assert store.get("anthropic/api_key") is None
    finally:
        keyring.set_keyring(before)


def test_the_memory_store_mirrors_the_interface() -> None:
    store = MemoryStore()
    store.set("a", "1")
    assert store.get("a") == "1" and store.values() == ["1"]
    store.delete("a")
    assert store.get("a") is None
    with pytest.raises(SecretStoreError):
        MemoryStore(usable=False).set("a", "1")


@pytest.mark.parametrize(
    "line",
    [
        f"calling with {SAMPLE}",
        "header Authorization: Bearer abcdefghijklmnop1234567890",
        "an odd credential zq-9!unusual!format!value in a url",
    ],
)
def test_keys_are_scrubbed_by_shape_and_by_known_value(line: str) -> None:
    known = ["zq-9!unusual!format!value"]
    cleaned = redact.scrub(line, known)
    assert redact.REDACTED in cleaned
    for secret in (SAMPLE, "abcdefghijklmnop1234567890", *known):
        assert secret not in cleaned


def test_ordinary_text_and_short_values_are_left_alone() -> None:
    assert redact.scrub("the task is done", ["is"]) == "the task is done"
    assert redact.scrub("sk-short") == "sk-short"


def test_the_filter_catches_a_key_passed_as_a_log_argument(
    caplog: pytest.LogCaptureFixture,
) -> None:
    log = logging.getLogger("trillion.test.redact")
    log.addFilter(redact.RedactingFilter(lambda: ["known-value-123456"]))
    with caplog.at_level(logging.INFO, logger=log.name):
        log.info("key=%s other=%s", SAMPLE, "known-value-123456")
    assert SAMPLE not in caplog.text and "known-value-123456" not in caplog.text
    assert caplog.text.count(redact.REDACTED) == 2


def test_installed_redaction_covers_every_logger_and_handlers_added_later() -> None:
    """A filter on the root logger alone sees none of this; the review caught that."""
    import io

    undo = redact.install(lambda: ["known-value-123456"])
    try:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)  # added after install, like a server's
        child = logging.getLogger("trillion.some.module")
        other = logging.getLogger("uvicorn.error.test")
        for log in (child, other):
            log.addHandler(handler)
            log.setLevel(logging.INFO)
            log.propagate = False
        child.warning("key=%s", SAMPLE)
        other.info("token known-value-123456 seen")
        written = stream.getvalue()
        assert SAMPLE not in written and "known-value-123456" not in written
        assert written.count(redact.REDACTED) == 2
    finally:
        undo()
        for log in (child, other):
            log.removeHandler(handler)


def test_a_failing_lookup_of_known_values_does_not_break_or_recurse() -> None:
    log = logging.getLogger("trillion.test.redact.failing")

    def known() -> list[str]:
        log.warning("keychain read failed")  # logs from inside the lookup
        raise RuntimeError("keychain down")

    undo = redact.install(known)
    try:
        log.warning("still logs: %s", SAMPLE)  # shape-scrubbed, no recursion error
    finally:
        undo()
