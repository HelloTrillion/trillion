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


# Every fake below is assembled from pieces, so no secret scanner mistakes it for a
# real key, but each one has the shape the provider issues.
_A36 = "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3z5A"
SHAPED = {
    "slack bot": "xox" + "b-1234567890-1234567890123-" + _A36[:24],
    "slack user": "xox" + "p-1234567890-1234567890-1234567890123-" + "ab12" * 8,
    "slack app": "xa" + "pp-1-A0123456789-1234567890123-" + "ab12" * 16,
    "slack rotating": "xox" + "e.xoxp-1-" + _A36 + _A36,
    "google access": "ya" + "29." + "a0AfB_" + _A36 + _A36,
    "google refresh": "1/" + "/0g" + _A36 + "-" + _A36,
    "google api key": "AI" + "za" + "Sy" + _A36[:33],
    "stripe live": "sk" + "_live_" + "51H" + _A36 + _A36[:20],
    "stripe test": "sk" + "_test_" + "51H" + _A36 + _A36[:20],
    "stripe restricted": "rk" + "_live_" + "51H" + _A36 + _A36[:20],
    "stripe webhook": "wh" + "sec_" + _A36[:32],
    "telegram": "1234567890" + ":" + "AAH" + _A36[:32],
    "github classic": "gh" + "p_" + _A36,
    "github fine grained": "github" + "_pat_" + "11ABCDEFG0" + _A36[:12] + "_" + _A36 * 2,
    "elevenlabs": "sk" + "_" + "0123456789abcdef" * 3,
    "google client secret": "GOC" + "SPX-" + _A36[:28],
    "jwt": "ey" + "JhbGciOiJIUzI1NiJ9" + "." + "ey" + "JzdWIiOiIxMjM0In0" + "." + _A36[:30],
    "aws access key": "AK" + "IA" + "IOSFODNN7EXAMPLE",
    "aws session key": "AS" + "IA" + "Y3Z5ABCDEFGH1234",
}


@pytest.mark.parametrize("kind", sorted(SHAPED))
def test_every_known_key_shape_is_scrubbed(kind: str) -> None:
    key = SHAPED[kind]
    cleaned = redact.scrub(f"sending {key} to the provider, then '{key}'.")
    assert key not in cleaned, kind
    assert cleaned == f"sending {redact.REDACTED} to the provider, then '{redact.REDACTED}'."


def test_the_telegram_shape_needs_exactly_its_own_form() -> None:
    assert redact.scrub("ran 12:30:00 then 2024:01") == "ran 12:30:00 then 2024:01"


def test_a_telegram_token_inside_its_api_address_is_scrubbed() -> None:
    url = "https://api.telegram.org/bot" + SHAPED["telegram"] + "/sendMessage"
    assert redact.scrub(f"POST {url}") == (
        f"POST https://api.telegram.org/bot{redact.REDACTED}/sendMessage"
    )


@pytest.mark.parametrize(
    "line",
    [
        "the task is done",
        "commit 3487279c8b60be1b8c560ee78121ab8acdd0f1e2 merged",  # a git sha
        "an id 0123456789abcdef0123456789abcdef01234567 checked",  # 40 hex, no prefix
        "see https://example.com/v1//path and 1//2 of the pie",
        "xoxo and hugs, the sk_ prefix, task_live_feed, ghp is short",
        "meeting at 10:30, ticket 123456:abc",
        "AIza is a word here",
        "uuid 123e4567-e89b-12d3-a456-426614174000 is fine",
        "github_pat_ alone",
        "my_ya29.notes and whsec_ alone",
        "Bearer authorization_header_missing in the reply",
        "eyJ alone and eyJabc.notjwt here",
        "the AKIAMARKETING plan and ASIA trip",
        "GOCSPX- alone",
    ],
)
def test_ordinary_lines_are_not_mistaken_for_keys(line: str) -> None:
    assert redact.scrub(line) == line


def test_the_store_remembers_every_value_it_hands_out(keychain: FakeKeychain) -> None:
    from trillion.secrets import store as store_mod

    keychain.items[("trillion", "stripe/api_key")] = "lazily-read-value-0001"
    kc = KeychainStore()
    assert "lazily-read-value-0001" not in store_mod.handed_out()  # not read yet
    assert kc.get("stripe/api_key") == "lazily-read-value-0001"
    kc.set("telegram/bot", "set-from-the-page-0002")
    mem = MemoryStore()
    mem.set("slack/bot", "memory-store-value-0003")
    seen = store_mod.handed_out()
    for v in ("lazily-read-value-0001", "set-from-the-page-0002", "memory-store-value-0003"):
        assert v in seen


def test_json_entries_expose_their_secret_fields_not_their_labels() -> None:
    import json

    from trillion.secrets import store as store_mod

    mem = MemoryStore()
    mem.set(
        "google/account/b@example.com",
        json.dumps(
            {
                "refresh_token": "nested-refresh-value-0004",
                "access_token": "nested-access-value-0005",
                "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
                "email": "someone@example.com",
                "token_type": "Bearer",
                "id_token": "nested-id-token-0015",
                # Built from pieces so secret scanners skip the fake value.
                "apiKey": "camel-" + "api-key-0016",
                "api_key": "snake-api-key-0017",
            }
        ),
    )
    mem.set(
        "mcp/notion/sign-in",
        json.dumps(
            {
                "tokens": {"access_token": "deep-access-value-0006"},
                "client_info": {
                    "client_id": "public-client-id-0012",
                    "client_secret": "cs-0013-x",
                    "token_endpoint_auth_method": "client_secret_post",
                    "scope": "read write everything",
                },
                "secret_holder": {"label": "label-under-secret-0021"},
            }
        ),
    )
    seen = store_mod.handed_out()
    for v in (
        "nested-refresh-value-0004",
        "nested-access-value-0005",
        "deep-access-value-0006",
        "cs-0013-x",
        "nested-id-token-0015",
        "camel-api-key-0016",
        "snake-api-key-0017",
    ):
        assert v in seen
    for v in ("client_secret_post", "read write everything", "label-under-secret-0021"):
        assert v not in seen
    assert "someone@example.com" not in seen
    assert "https://www.googleapis.com/auth/gmail.readonly" not in seen
    assert "public-client-id-0012" not in seen


def test_secret_looking_environment_variables_are_known() -> None:
    env = {
        "ANTHROPIC_API_KEY": "env-anthropic-value-0007",
        "TRILLION_GOOGLE_CLIENT_SECRET": "env-google-secret-0008",
        "SOME_ACCESS_TOKEN": "env-token-value-0009",
        "HOME": "/Users/someone/a-long-home-path",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    assert set(redact.env_secrets(env)) == {
        "env-anthropic-value-0007",
        "env-google-secret-0008",
        "env-token-value-0009",
    }


def test_a_value_read_after_logging_started_is_still_scrubbed(
    keychain: FakeKeychain, caplog: pytest.LogCaptureFixture
) -> None:
    from trillion.cli.main import _secrets_in_use

    session = "session-" + "token-value-0010"
    log = logging.getLogger("trillion.test.redact.lazy")
    log.addFilter(redact.RedactingFilter(lambda: _secrets_in_use(session)))
    keychain.items[("trillion", "slack/user")] = "lazy-slack-value-0011"
    token = KeychainStore().get("slack/user")  # fetched after the scrub was in place
    with caplog.at_level(logging.INFO, logger=log.name):
        log.info("got %s with %s", token, session)
    assert "lazy-slack-value-0011" not in caplog.text
    assert "session-token-value-0010" not in caplog.text


def test_a_known_value_holding_another_is_removed_whole() -> None:
    inner, outer = "inner-value-0014", "prefix-inner-value-0014-suffix"
    assert redact.scrub(f"x {outer} y", [inner, outer]) == f"x {redact.REDACTED} y"


def test_only_what_is_stored_now_is_held(keychain: FakeKeychain) -> None:
    import json

    from trillion.secrets import store as store_mod

    kc = KeychainStore()
    kc.set("google/account/a@example.com", json.dumps({"access_token": "first-access-0018"}))
    kc.set("google/account/a@example.com", json.dumps({"access_token": "fresh-access-0019"}))
    held = store_mod.handed_out()
    assert "fresh-access-0019" in held
    assert not any("first-access-0018" in v for v in held)  # a refreshed token replaces
    kc.delete("google/account/a@example.com")
    assert store_mod.handed_out() == ()
    mem = MemoryStore()
    mem.set("telegram/bot", "memory-value-0022")
    mem.delete("telegram/bot")
    assert store_mod.handed_out() == ()
    keychain.items[("trillion", "slack/app")] = "removed-elsewhere-0023"
    assert kc.get("slack/app") == "removed-elsewhere-0023"
    del keychain.items[("trillion", "slack/app")]
    assert kc.get("slack/app") is None  # gone from the keychain: forgotten here too
    assert store_mod.handed_out() == ()


def test_held_values_come_longest_first() -> None:
    from trillion.secrets import store as store_mod

    mem = MemoryStore()
    mem.set("a", "short-value-01")
    mem.set("b", "a-much-longer-value-0002")
    assert store_mod.handed_out() == ("a-much-longer-value-0002", "short-value-01")


def test_a_token_in_a_logged_exception_is_scrubbed() -> None:
    import io

    undo = redact.install(lambda: ["known-value-123456"])
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    log = logging.getLogger("trillion.test.redact.exc")
    log.addHandler(handler)
    log.propagate = False
    try:
        try:
            raise RuntimeError(f"provider refused {SAMPLE} and known-value-123456")
        except RuntimeError:
            log.exception("the call failed")
        log.warning("here", stack_info=True, extra={"unused": SAMPLE})
        written = stream.getvalue()
    finally:
        undo()
        log.removeHandler(handler)
    assert "Traceback" in written and "RuntimeError: provider refused" in written
    assert SAMPLE not in written and "known-value-123456" not in written
    assert "Stack (most recent call last)" in written


def test_a_read_overtaken_by_a_write_does_not_bring_back_the_old_value(
    keychain: FakeKeychain, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trillion.secrets import store as store_mod

    kc = KeychainStore()
    keychain.items[("trillion", "slack/bot")] = "old-bot-value-0030"
    real_get = keychain.get_password

    def slow_get(service: str, username: str) -> str | None:
        value = real_get(service, username)  # the old value is read...
        kc.set("slack/bot", "new-bot-value-0031")  # ...and a new one lands meanwhile
        return value

    monkeypatch.setattr(keychain, "get_password", slow_get)
    assert kc.get("slack/bot") == "old-bot-value-0030"
    assert store_mod.handed_out() == ("new-bot-value-0031",)


def test_a_value_is_held_even_when_the_keychain_refuses_it(
    keychain: FakeKeychain, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trillion.secrets import store as store_mod

    def refuse(service: str, username: str, password: str) -> None:
        raise keyring.errors.PasswordSetError("locked")

    monkeypatch.setattr(keychain, "set_password", refuse)
    with pytest.raises(SecretStoreError):
        KeychainStore().set("stripe/api_key", "refused-but-held-0032")
    assert "refused-but-held-0032" in store_mod.handed_out()  # the caller still has it


def test_fields_passed_as_extra_are_scrubbed() -> None:
    import io

    undo = redact.install(lambda: ["known-value-123456"])
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s key=%(key)s other=%(other)s"))
    log = logging.getLogger("trillion.test.redact.extra")
    log.addHandler(handler)
    log.propagate = False
    try:
        log.warning("sending", extra={"key": SAMPLE, "other": "known-value-123456"})
    finally:
        undo()
        log.removeHandler(handler)
    written = stream.getvalue()
    assert SAMPLE not in written and "known-value-123456" not in written
    assert written.count(redact.REDACTED) == 2


def test_undo_leaves_what_was_installed_since_in_place() -> None:
    before = logging.getLogRecordFactory(), logging.Logger.makeRecord
    first = redact.install()
    second = redact.install()
    later = logging.getLogRecordFactory(), logging.Logger.makeRecord
    try:
        first()  # not the newest: undoing it must not drop the second one
        assert (logging.getLogRecordFactory(), logging.Logger.makeRecord) == later
    finally:
        second()
        first()
    assert (logging.getLogRecordFactory(), logging.Logger.makeRecord) == before
