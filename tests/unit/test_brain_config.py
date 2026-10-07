from __future__ import annotations

import pytest

from trillion.brain import config
from trillion.brain.config import ConfigError, ModelSettings, endpoint, provider_from_env, resolve
from trillion.brain.providers.anthropic import DEFAULT_MODEL, AnthropicProvider
from trillion.brain.providers.openai_compat import OpenAICompatProvider
from trillion.secrets.store import MemoryStore


def test_no_configuration_is_rung_zero() -> None:
    assert provider_from_env({}) is None
    assert provider_from_env({"TRILLION_ANTHROPIC_API_KEY": "  "}) is None
    assert resolve(None, MemoryStore(), {}) == (None, "none")


def test_an_anthropic_key_selects_anthropic() -> None:
    p = provider_from_env({"TRILLION_ANTHROPIC_API_KEY": "k"})
    assert isinstance(p, AnthropicProvider) and p.model == DEFAULT_MODEL
    p = provider_from_env({"TRILLION_ANTHROPIC_API_KEY": "k", "TRILLION_MODEL": "other"})
    assert p is not None and p.model == "other"


def test_a_local_endpoint_wins_and_needs_a_model_name() -> None:
    env = {"TRILLION_OPENAI_BASE_URL": "http://localhost:11434/v1", "TRILLION_MODEL": "local"}
    p = provider_from_env({**env, "TRILLION_ANTHROPIC_API_KEY": "k"})
    assert isinstance(p, OpenAICompatProvider) and p.model == "local"
    with pytest.raises(ConfigError, match="TRILLION_MODEL"):
        provider_from_env({"TRILLION_OPENAI_BASE_URL": "http://localhost:11434/v1"})


def test_unrelated_keys_are_ignored() -> None:
    # Only TRILLION_-prefixed variables are read: a key exported for another tool
    # is not silently spent here.
    assert provider_from_env({"ANTHROPIC_API_KEY": "k", "OPENAI_API_KEY": "k"}) is None


def test_saved_settings_are_used_when_the_environment_is_silent() -> None:
    secrets = MemoryStore()
    secrets.set("anthropic/api_key", "k")
    saved = ModelSettings(kind="anthropic", model="claude-opus-5-5")
    provider, source = resolve(saved, secrets, {})
    assert isinstance(provider, AnthropicProvider) and source == "settings"
    # The environment still wins, for developers and CI.
    provider, source = resolve(saved, secrets, {"TRILLION_ANTHROPIC_API_KEY": "e"})
    assert source == "env"


def test_settings_whose_key_is_gone_mean_nobody_answers() -> None:
    saved = ModelSettings(kind="openai", model="gpt")
    assert resolve(saved, MemoryStore(), {}) == (None, "none")


def test_a_local_model_needs_no_key() -> None:
    provider, source = resolve(ModelSettings(kind="local", model="gemma"), MemoryStore(), {})
    assert isinstance(provider, OpenAICompatProvider) and source == "settings"


def test_the_key_is_read_from_the_keychain_at_request_time() -> None:
    secrets = MemoryStore()
    secrets.set("anthropic/api_key", "first")
    provider = config.build(ModelSettings(kind="anthropic", model="m"), secrets)
    assert isinstance(provider, AnthropicProvider)
    secrets.set("anthropic/api_key", "second")
    assert provider._api_key() == "second"  # replaced without a restart (SEC-6)


def test_settings_round_trip_and_reject_nonsense() -> None:
    s = ModelSettings(kind="custom", model="m", base_url="https://x.example/v1", max_output=8000)
    assert ModelSettings.from_json(s.to_json()) == s
    for bad in (None, "x", {}, {"kind": "nope", "model": "m"}, {"kind": "local"}):
        assert ModelSettings.from_json(bad) is None


def test_endpoints() -> None:
    assert endpoint("anthropic", None) is None
    assert endpoint("openai", "http://evil.example") == config.OPENAI_URL  # never overridden
    assert endpoint("local", None) == config.LOCAL_URL
    assert endpoint("local", "http://127.0.0.1:1234/v1/") == "http://127.0.0.1:1234/v1"
    assert endpoint("custom", "https://openrouter.ai/api/v1") == "https://openrouter.ai/api/v1"


@pytest.mark.parametrize(
    ("kind", "url", "code"),
    [
        ("local", "http://192.168.1.5:11434/v1", "not_local"),
        ("local", "https://example.com/v1", "not_local"),
        ("custom", "http://example.com/v1", "insecure_url"),  # a key over plain http
        ("custom", "", "bad_url"),
        ("custom", "ftp://example.com", "bad_url"),
        ("custom", "https://user:pw@example.com/v1", "bad_url"),
        ("custom", "https://example.com/v1?key=1", "bad_url"),
        ("custom", "not a url", "bad_url"),
    ],
)
def test_addresses_that_could_leak_a_key_are_refused(kind: str, url: str, code: str) -> None:
    with pytest.raises(ConfigError) as e:
        endpoint(kind, url)  # type: ignore[arg-type]
    assert e.value.code == code
