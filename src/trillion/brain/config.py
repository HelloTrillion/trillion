"""Which model Trillion talks to, and where its key comes from.

Two sources, in this order:

1. **Environment variables**, for development and CI (SEC-3: read, never written).
2. **Settings the user made in the app**: the choice in the database, the key in
   the operating system's keychain.

Keys are read when a request is made, so replacing one takes effect at once (SEC-6).
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, get_args
from urllib.parse import urlsplit

from trillion.brain.providers import anthropic as anthropic_api
from trillion.brain.providers import openai_compat
from trillion.brain.providers.anthropic import DEFAULT_MODEL, AnthropicProvider
from trillion.brain.providers.openai_compat import OpenAICompatProvider
from trillion.brain.types import ModelChoice, ModelProvider
from trillion.secrets.store import SecretStore

ANTHROPIC_KEY = "TRILLION_ANTHROPIC_API_KEY"
OPENAI_BASE_URL = "TRILLION_OPENAI_BASE_URL"
OPENAI_KEY = "TRILLION_OPENAI_API_KEY"
MODEL = "TRILLION_MODEL"

Kind = Literal["anthropic", "openai", "local", "custom"]
KINDS: tuple[Kind, ...] = get_args(Kind)
Source = Literal["env", "settings", "none"]

OPENAI_URL = "https://api.openai.com/v1"
LOCAL_URL = "http://localhost:11434/v1"  # Ollama's default
# Which kinds need a key at all. A custom endpoint may or may not.
NEEDS_KEY: frozenset[Kind] = frozenset({"anthropic", "openai"})
TAKES_KEY: frozenset[Kind] = frozenset({"anthropic", "openai", "custom"})


class ConfigError(ValueError):
    """Settings that cannot work. `code` is safe to show."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True, kw_only=True)
class ModelSettings:
    kind: Kind
    model: str
    base_url: str | None = None
    max_output: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "model": self.model,
            "base_url": self.base_url,
            "max_output": self.max_output,
        }

    @classmethod
    def from_json(cls, raw: Any) -> ModelSettings | None:
        if not isinstance(raw, dict) or raw.get("kind") not in KINDS or not raw.get("model"):
            return None
        cap = raw.get("max_output")
        return cls(
            kind=raw["kind"],
            model=str(raw["model"]),
            base_url=str(raw["base_url"]) if raw.get("base_url") else None,
            max_output=cap if isinstance(cap, int) else None,
        )


def secret_name(kind: Kind) -> str:
    return f"{kind}/api_key"


def _is_loopback(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# Addresses that only exist on the user's own network: home and office ranges, and
# the range private overlay networks hand out. Written as numbers on purpose. A name
# can be made to resolve anywhere, so a name on another machine still needs HTTPS.
_PRIVATE = tuple(
    ipaddress.ip_network(n)
    for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7")
)


def _is_private(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in net for net in _PRIVATE)


def keyless(url: str) -> bool:
    """True for plain HTTP to another machine. Allowed on the user's own network
    only, and a key is never sent there: anyone on that network could read it."""
    parts = urlsplit(url)
    return parts.scheme == "http" and not _is_loopback(parts.hostname or "")


def endpoint(kind: Kind, base_url: str | None) -> str | None:
    """The URL a kind talks to, checked. Anthropic has none: the SDK knows it.

    A key is only ever sent over HTTPS, or to this machine. A "local" model must
    actually be local. Plain HTTP to another machine is accepted only for a numeric
    address on the user's own network, and `keyless` then keeps the key at home."""
    if kind == "anthropic":
        return None
    if kind == "openai":
        return OPENAI_URL
    url = (base_url or (LOCAL_URL if kind == "local" else "")).strip().rstrip("/")
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        _ = parts.port  # raises on a port that is not a number
    except ValueError as e:
        raise ConfigError("bad_url", "the address is not a valid URL") from e
    if not url.isascii() or any(ch.isspace() for ch in url):
        raise ConfigError("bad_url", "the address has characters a URL cannot contain")
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
        raise ConfigError("bad_url", "the address must be an http or https URL")
    if parts.query or parts.fragment:
        raise ConfigError("bad_url", "the address cannot carry a query or fragment")
    local = _is_loopback(host)
    if kind == "local" and not local:
        raise ConfigError("not_local", "a local model must be on this machine")
    if parts.scheme == "http" and not local and not (kind == "custom" and _is_private(host)):
        raise ConfigError("insecure_url", "an address on another machine must use https")
    return url


def build(settings: ModelSettings, secrets: SecretStore) -> ModelProvider:
    name = secret_name(settings.kind)
    if settings.kind == "anthropic":
        return AnthropicProvider(
            api_key=lambda: secrets.get(name) or "",
            model=settings.model,
            max_output=settings.max_output,
        )
    url = endpoint(settings.kind, settings.base_url)
    if url is None:  # pragma: no cover - only Anthropic has no endpoint
        raise ConfigError("bad_url")
    return OpenAICompatProvider(
        base_url=url,
        model=settings.model,
        api_key=(lambda: secrets.get(name))
        if settings.kind in TAKES_KEY and not keyless(url)
        else (lambda: None),
        cap_field="max_completion_tokens" if settings.kind == "openai" else "max_tokens",
        max_output=settings.max_output,
    )


def provider_from_env(env: Mapping[str, str] | None = None) -> ModelProvider | None:
    """The provider the environment configures, or None.

    An OpenAI-compatible endpoint wins when both are set: pointing at a local
    model is the more deliberate choice."""
    env = os.environ if env is None else env
    base_url = env.get(OPENAI_BASE_URL, "").strip()
    if base_url:
        model = env.get(MODEL, "").strip()
        if not model:
            raise ConfigError(
                "no_model", f"{OPENAI_BASE_URL} is set; {MODEL} must name the model to use"
            )
        # The same rule as a saved setting: a key goes over HTTPS or stays on this machine.
        checked = endpoint("custom", base_url)
        if checked is None:  # pragma: no cover - only Anthropic has no endpoint
            raise ConfigError("bad_url")
        if keyless(checked) and env.get(OPENAI_KEY, "").strip():
            raise ConfigError("key_needs_https", f"{OPENAI_KEY} is only sent over https")
        return OpenAICompatProvider(
            base_url=checked, model=model, api_key=lambda: env.get(OPENAI_KEY) or None
        )
    if env.get(ANTHROPIC_KEY, "").strip():
        return AnthropicProvider(
            api_key=lambda: env.get(ANTHROPIC_KEY, ""),
            model=env.get(MODEL, "").strip() or DEFAULT_MODEL,
        )
    return None


def resolve(
    settings: ModelSettings | None,
    secrets: SecretStore,
    env: Mapping[str, str] | None = None,
) -> tuple[ModelProvider | None, Source]:
    from_env = provider_from_env(env)
    if from_env is not None:
        return from_env, "env"
    if settings is None:
        return None, "none"
    if settings.kind in NEEDS_KEY and not secrets.get(secret_name(settings.kind)):
        return None, "none"  # the key was removed from the keychain
    try:
        return build(settings, secrets), "settings"
    except ConfigError:
        return None, "none"


async def list_models(kind: Kind, base_url: str | None, api_key: str | None) -> list[ModelChoice]:
    """Ask the provider what it offers. It costs nothing, and it proves the key works."""
    if kind == "anthropic":
        return await anthropic_api.list_models(api_key or "")
    url = endpoint(kind, base_url)
    if url is None:  # pragma: no cover - only Anthropic has no endpoint
        raise ConfigError("bad_url")
    sends_key = kind in TAKES_KEY and not keyless(url)
    return await openai_compat.list_models(url, api_key if sends_key else None)
