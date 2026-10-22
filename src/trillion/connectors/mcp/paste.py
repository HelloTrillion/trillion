"""Reading what a person pastes into "Add your own": a server's address, the command
from its README, or a config snippet in the shape Claude Desktop, Cursor or VS Code
use. Any key in it is taken out and offered for the keychain, so the file only ever
names it.

Nothing is saved or started here. The result is shown to the person first.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from trillion.connectors.mcp.catalog import ref
from trillion.connectors.mcp.config import KEYCHAIN_REF, ConfigError, parse_server

_KEYISH = re.compile(r"key|token|secret|pass|auth|cred|bearer", re.IGNORECASE)
# Values a README leaves for the reader to fill in.
_PLACEHOLDER = re.compile(r"^<.*>$|your[_ -]|^x{4,}$|^\.\.\.$|^\$\{.*\}$|^replace", re.IGNORECASE)
MAX_TEXT = 20_000


@dataclass(kw_only=True)
class Secret:
    ref: str  # the keychain name it will be saved under
    label: str  # the env or header name it came from
    value: str  # what was pasted; empty when the paste only had a placeholder


@dataclass(kw_only=True)
class Proposal:
    name: str
    entry: dict[str, Any]
    secrets: list[Secret] = field(default_factory=list)


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s or "server")[:40]


def _name_from_url(url: str) -> str:
    host = urlparse(url).hostname or "server"
    if host in ("localhost", "::1") or re.fullmatch(r"[\d.]+", host):
        return "local-server"
    parts = [p for p in host.split(".") if p not in ("www", "mcp", "api", "com", "app", "dev")]
    return slug(parts[0] if parts else host)


def _name_from_command(args: list[str]) -> str:
    for a in reversed(args):
        if not a.startswith("-"):
            base = a.rsplit("/", 1)[-1].split("@")[0]
            base = re.sub(r"^(mcp-server-|server-)|(-mcp-server|-mcp|-server)$", "", base)
            return slug(base)
    return "server"


def _take_secrets(name: str, entry: dict[str, Any]) -> list[Secret]:
    """Move every key out of `env` and `headers` into keychain references."""
    found: list[Secret] = []
    for section in ("env", "headers"):
        values = entry.get(section)
        if not isinstance(values, dict):
            continue
        for k, v in list(values.items()):
            if not isinstance(v, str) or KEYCHAIN_REF.search(v) or not _KEYISH.search(k):
                continue
            scheme, _, rest = v.partition(" ") if v.lower().startswith("bearer ") else ("", "", v)
            value = rest.strip()
            key_ref = ref(name, slug(k).replace("-", "_"))
            if _PLACEHOLDER.search(value):
                value = ""
            values[k] = f"{scheme} keychain:{key_ref}" if scheme else f"keychain:{key_ref}"
            found.append(Secret(ref=key_ref, label=k, value=value))
    return found


def _entries(raw: Any) -> list[tuple[str, Any]]:
    if not isinstance(raw, dict):
        raise ConfigError("that is not a server's settings")
    for wrapper in ("mcpServers", "servers", "mcp"):
        inner = raw.get(wrapper)
        if isinstance(inner, dict):
            if wrapper == "mcp" and isinstance(inner.get("servers"), dict):
                inner = inner["servers"]
            return list(inner.items())
    if "command" in raw or "url" in raw or "serverUrl" in raw:
        return [("", raw)]
    if raw and all(isinstance(v, dict) for v in raw.values()):
        return list(raw.items())
    raise ConfigError('no server found: expected "command" or "url"')


def _normalise(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ConfigError("an entry must be an object")
    out = dict(entry)
    # Shapes other apps use for the same thing.
    if "serverUrl" in out and "url" not in out:
        out["url"] = out.pop("serverUrl")
    out.pop("type", None)
    out.pop("transport", None)
    keep = ("command", "args", "env", "url", "headers", "title", "tools", "timeout_s")
    return {k: v for k, v in out.items() if k in keep}


def read(text: str) -> list[Proposal]:
    """What the paste describes. Raises ConfigError, in words a person can act on."""
    text = text.strip()
    if not text:
        raise ConfigError("paste a server's address, its command, or its settings")
    if len(text) > MAX_TEXT:
        raise ConfigError("that is too long to be a server's settings")
    pairs: list[tuple[str, dict[str, Any]]]
    if text.startswith(("{", '"')):
        try:
            raw = json.loads(text if text.startswith("{") else "{" + text.rstrip(",") + "}")
        except ValueError:
            raise ConfigError("that looks like settings, but it is not valid JSON") from None
        pairs = [(n, _normalise(e)) for n, e in _entries(raw)]
    elif re.match(r"^https?://", text) and " " not in text:
        pairs = [("", {"url": text})]
    else:
        try:
            words = shlex.split(text)
        except ValueError:
            raise ConfigError("that command could not be read") from None
        env: dict[str, str] = {}
        while words and re.match(r"^[A-Z_][A-Z0-9_]*=", words[0]):
            k, _, v = words.pop(0).partition("=")
            env[k] = v
        if not words:
            raise ConfigError("no command found")
        entry: dict[str, Any] = {"command": words[0], "args": words[1:]}
        if env:
            entry["env"] = env
        pairs = [("", entry)]

    proposals: list[Proposal] = []
    for given, entry in pairs:
        if given:
            name = slug(given)
        elif "url" in entry:
            name = _name_from_url(str(entry["url"]))
        else:
            name = _name_from_command([str(a) for a in entry.get("args") or []])
        secrets = _take_secrets(name, entry)
        parse_server(name, entry)  # refuse now what could not be saved later
        proposals.append(Proposal(name=name, entry=entry, secrets=secrets))
    return proposals
