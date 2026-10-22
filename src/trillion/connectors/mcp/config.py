"""Which MCP servers the user connected, read from `mcp.json` in Trillion's folder (CON-5).

The file uses the shape Claude Desktop and Cursor use, so an entry can be copied
across, with three additions:

    {
      "mcpServers": {
        "time":   { "command": "uvx", "args": ["mcp-server-time"] },
        "github": {
          "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
          "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "keychain:mcp/github" },
          "tools": { "search_repositories": { "risk": "read" } }
        },
        "notes":  { "url": "https://example.com/mcp",
                    "headers": { "Authorization": "Bearer keychain:mcp/notes" } }
      }
    }

- `tools`: how each tool is treated. Every tool asks first unless its `risk` here
  says otherwise (`read` runs without asking). Only the user writes this file.
- `keychain:NAME` anywhere in an `env` or `headers` value is replaced, when the
  server starts, by the key saved in the system keychain under that name. A key
  never needs to sit in this file.
- `timeout_s` bounds one call (60 by default); `disabled: true` keeps an entry
  without starting it.
- `title` is the name Settings shows, and `catalog` the featured entry it was added
  from, if any. Neither changes what runs.

A server at an address with no `Authorization` header signs in through the browser
when it asks for that (OAuth); the sign-in is kept in the keychain.

Settings → Connected tools writes this file when the user adds, changes or removes
a server, and it can still be edited by hand. A server whose entry is wrong is
reported and skipped; the rest still connect.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from trillion.secrets.store import SecretStore
from trillion.spine.contract import Risk

DEFAULT_TIMEOUT_S = 60.0
MAX_TIMEOUT_S = 600.0
_SERVER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
KEYCHAIN_REF = re.compile(r"keychain:([A-Za-z0-9_./-]{1,100})")
# Risks a tool can be given here. A tool can be made to run without asking, or to
# need a tap; never anything the spine does not know.
_RISKS = {r.value: r for r in Risk}


class ConfigError(ValueError):
    """The file, or one entry in it, cannot be used. The message is safe to show."""


@dataclass(frozen=True, kw_only=True)
class ServerConfig:
    name: str
    transport: Literal["stdio", "http"]
    command: str = ""
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    url: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    # Tool name → the risk the user gave it. Anything absent asks first.
    risks: Mapping[str, Risk] = field(default_factory=dict)
    timeout_s: float = DEFAULT_TIMEOUT_S
    disabled: bool = False
    title: str = ""
    catalog: str = ""

    @property
    def shown(self) -> str:
        """The name a person sees."""
        return self.title or self.name

    @property
    def signs_in(self) -> bool:
        """Whether it may ask the person to sign in (OAuth): an address with no key."""
        return self.transport == "http" and not any(
            k.lower() == "authorization" for k in self.headers
        )

    def risk_of(self, tool: str) -> Risk:
        """What the user said about a tool, or irreversible: asks first (CON-5)."""
        return self.risks.get(tool, Risk.IRREVERSIBLE)

    def key_names(self) -> list[str]:
        """The keychain names this server needs."""
        values = [*self.env.values(), *self.headers.values()]
        return sorted({m.group(1) for v in values for m in KEYCHAIN_REF.finditer(v)})


@dataclass(frozen=True, kw_only=True)
class Loaded:
    servers: tuple[ServerConfig, ...]
    # Entries that could not be used, by name, with why. Shown, never fatal.
    problems: Mapping[str, str]


def _strings(raw: Any, what: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in raw.items()
    ):
        raise ConfigError(f"{what} must map names to text")
    return dict(raw)


def parse_server(name: str, raw: Any) -> ServerConfig:
    if not _SERVER_NAME.match(name):
        raise ConfigError("a server's name is letters, digits, dots, dashes or underscores")
    if not isinstance(raw, dict):
        raise ConfigError("an entry must be an object")
    command, url = raw.get("command"), raw.get("url")
    if bool(command) == bool(url):
        raise ConfigError('give either "command" (a program to run) or "url", not both')
    risks: dict[str, Risk] = {}
    tools = raw.get("tools") or {}
    if not isinstance(tools, dict):
        raise ConfigError('"tools" must map tool names to settings')
    for tool, settings in tools.items():
        risk = settings.get("risk") if isinstance(settings, dict) else None
        if risk not in _RISKS:
            raise ConfigError(f'tool {tool!r}: "risk" must be one of {", ".join(_RISKS)}')
        risks[str(tool)] = _RISKS[risk]
    timeout = raw.get("timeout_s", DEFAULT_TIMEOUT_S)
    if isinstance(timeout, bool) or not isinstance(timeout, int | float) or not 0 < timeout:
        raise ConfigError('"timeout_s" must be a number of seconds')
    title, catalog = raw.get("title", ""), raw.get("catalog", "")
    if not isinstance(title, str) or not isinstance(catalog, str):
        raise ConfigError('"title" and "catalog" must be text')
    common: dict[str, Any] = {
        "name": name,
        "title": title.strip()[:60],
        "catalog": catalog,
        "risks": risks,
        "timeout_s": min(float(timeout), MAX_TIMEOUT_S),
        "disabled": raw.get("disabled") is True,
    }
    if command:
        args = raw.get("args") or []
        if not isinstance(command, str) or not isinstance(args, list):
            raise ConfigError('"command" must be text and "args" a list')
        if not all(isinstance(a, str) for a in args):
            raise ConfigError('every entry in "args" must be text')
        env = _strings(raw.get("env"), '"env"')
        return ServerConfig(transport="stdio", command=command, args=tuple(args), env=env, **common)
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        raise ConfigError('"url" must start with https://')
    if url.startswith("http://") and not re.match(
        r"^http://(127\.0\.0\.1|localhost|\[::1\])[:/]", url
    ):
        # A key sent in the clear to another machine is a key given away.
        raise ConfigError("a server on another machine must use https://")
    headers = _strings(raw.get("headers"), '"headers"')
    return ServerConfig(transport="http", url=url, headers=headers, **common)


def load(path: Path) -> Loaded:
    """The servers in the file. No file means none connected; a broken file says why."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Loaded(servers=(), problems={})
    except OSError as e:
        return Loaded(servers=(), problems={"mcp.json": f"could not be read ({e.strerror})"})
    try:
        raw = json.loads(text)
    except ValueError as e:
        return Loaded(servers=(), problems={"mcp.json": f"is not valid JSON ({e})"})
    entries = raw.get("mcpServers") if isinstance(raw, dict) else None
    if not isinstance(entries, dict):
        return Loaded(servers=(), problems={"mcp.json": 'needs an "mcpServers" object'})
    servers: list[ServerConfig] = []
    problems: dict[str, str] = {}
    for name, entry in entries.items():
        try:
            servers.append(parse_server(str(name), entry))
        except ConfigError as e:
            problems[str(name)] = str(e)
    return Loaded(servers=tuple(servers), problems=problems)


def read_file(path: Path) -> dict[str, Any]:
    """The whole file, for changing one entry. A file that cannot be read as JSON is
    never overwritten: the user may be part way through editing it."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"mcpServers": {}}
    except OSError as e:
        raise ConfigError(f"mcp.json could not be read ({e.strerror})") from None
    try:
        raw = json.loads(text)
    except ValueError:
        raise ConfigError("mcp.json is not valid JSON; fix or remove it first") from None
    if not isinstance(raw, dict):
        raise ConfigError('mcp.json needs an "mcpServers" object')
    entries = raw.setdefault("mcpServers", {})
    if not isinstance(entries, dict):
        raise ConfigError('mcp.json needs an "mcpServers" object')
    return raw


def change(path: Path, edit: Callable[[dict[str, Any]], None]) -> None:
    """Read the file, let `edit` change its `mcpServers`, and write it back whole: to a
    new file first, readable by the user only, then moved into place."""
    raw = read_file(path)
    edit(raw["mcpServers"])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".mcp-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


class MissingKey(ConfigError):
    """A `keychain:` reference whose key has not been saved."""


def fill(values: Mapping[str, str], secrets: SecretStore) -> dict[str, str]:
    """Replace every `keychain:NAME` with its key. Raises MissingKey, naming it."""
    out: dict[str, str] = {}
    for k, v in values.items():

        def key(m: re.Match[str]) -> str:
            found = secrets.get(m.group(1))
            if not found:
                raise MissingKey(f"no key saved in the keychain as {m.group(1)!r}")
            return found

        out[k] = KEYCHAIN_REF.sub(key, v)
    return out


def plain_secrets(server: ServerConfig) -> list[str]:
    """Names of env or header values that look like a key written into the file: named
    like one (a token, a key, a secret, a password, an authorization) and not a
    `keychain:` reference."""
    named = re.compile(r"key|token|secret|pass|auth|cred|bearer", re.IGNORECASE)
    return sorted(
        k
        for k, v in [*server.env.items(), *server.headers.items()]
        if named.search(k) and not KEYCHAIN_REF.search(v) and len(v.strip()) >= 8
    )
