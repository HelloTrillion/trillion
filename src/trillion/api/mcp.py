"""Settings → Connected tools: the MCP servers the user connected, and adding,
changing and removing them (CON-5).

Behind the session token like every /api/ route; the model has no way to call any
of it, so only the person at the page can connect a server or loosen what one may
do. A key is never sent back: only the names of the keychain entries a server
needs, and whether each is saved.

One route is outside /api/: `/mcp/callback`, where the browser comes back after
signing in to a service. It needs no token, because the service's site sends the
browser there; it can only hand a code to a sign-in that is already waiting for
that exact random state, and the MCP library checks the state again.
"""

from __future__ import annotations

import contextlib
import html
import logging
import secrets
import shutil
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from trillion import paths
from trillion.api.runtime import Runtime
from trillion.connectors.mcp import catalog, paste
from trillion.connectors.mcp.config import (
    KEYCHAIN_REF,
    ConfigError,
    change,
    parse_server,
    plain_secrets,
)
from trillion.connectors.mcp.servers import McpServers, ServerState
from trillion.connectors.mcp.signin import sign_in_key
from trillion.secrets.store import SecretStoreError
from trillion.spine.contract import Risk

logger = logging.getLogger(__name__)

# What a local server needs installed, and where to get it.
_PROGRAMS = {
    "npx": ("Node.js", "https://nodejs.org/en/download"),
    "node": ("Node.js", "https://nodejs.org/en/download"),
    "uvx": ("uv", "https://docs.astral.sh/uv/getting-started/installation/"),
    "uv": ("uv", "https://docs.astral.sh/uv/getting-started/installation/"),
    "docker": ("Docker", "https://docs.docker.com/get-started/get-docker/"),
}
LOG_LINES = 200


def _fail(status: int, error: str, detail: str = "") -> JSONResponse:
    return JSONResponse({"error": error, "detail": detail}, status_code=status)


def missing_program(command: str) -> dict[str, str] | None:
    """What to install when a local server's program is not on this computer."""
    if not command or shutil.which(command):
        return None
    name, link = _PROGRAMS.get(command, (command, ""))
    return {"program": command, "name": name, "link": link}


class Add(BaseModel):
    # From a featured entry: its id and what the person typed into its fields.
    catalog: str = ""
    values: dict[str, str] = Field(default_factory=dict)
    # Or their own: the entry as `parse` proposed it, the name it was proposed under,
    # the name to show, and the keys, by the keychain name in the entry.
    entry: dict[str, Any] | None = None
    proposed: str = ""
    title: str = ""
    secrets: dict[str, str] = Field(default_factory=dict)


class Paste(BaseModel):
    text: str = Field(max_length=paste.MAX_TEXT)


class Change(BaseModel):
    # Tool name (as the server knows it) → "read" (runs without asking) or "ask".
    tools: dict[str, str] | None = None
    disabled: bool | None = None
    title: str | None = None


class Keys(BaseModel):
    secrets: dict[str, str]


def create_mcp_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/mcp")

    def servers() -> McpServers:
        if rt.mcp is None:
            raise RuntimeError("MCP is not running")
        return rt.mcp

    def row(state: ServerState) -> dict[str, Any]:
        c = state.config
        return {
            "name": c.name,
            "title": c.shown,
            "catalog": c.catalog,
            "transport": c.transport,
            # What runs: the program and its arguments, or the address without its
            # query. Arguments are the person's own and shown to them so they can see
            # what runs; keys are never in them, only keychain names.
            "runs": (
                " ".join((c.command, *c.args)) if c.transport == "stdio" else c.url.split("?", 1)[0]
            ),
            "status": state.status,
            "problem": state.problem,
            "sign_in_url": state.sign_in_url,
            "signs_in": c.signs_in,
            "signed_in": c.signs_in and bool(rt.secrets.get(sign_in_key(c.name))),
            "missing": missing_program(c.command) if c.transport == "stdio" else None,
            "keys": [{"name": k, "saved": bool(rt.secrets.get(k))} for k in c.key_names()],
            "keys_in_file": plain_secrets(c),
            "disabled": c.disabled,
            "tools": [
                {
                    "name": t.remote,
                    "description": t.description[:300],
                    "asks_first": t.risk != "read",
                    "risk": t.risk,
                    "says_read_only": t.read_only_hint,
                }
                for t in state.tools
            ],
            "skipped": state.skipped,
        }

    def config_path() -> Any:
        return rt.mcp_config or paths.mcp_config_path()

    def unique(base: str) -> str:
        taken = set(servers().states) | set(servers().problems)
        name, n = base, 2
        while name in taken:
            name = f"{base[:36]}-{n}"
            n += 1
        return name

    def save_secrets(secrets: dict[str, str]) -> JSONResponse | None:
        if not secrets:
            return None
        if not rt.secrets.available():
            return _fail(409, "no_keychain", "this computer has no keychain to keep keys in")
        try:
            for name, value in secrets.items():
                rt.secrets.set(name, value)
        except SecretStoreError:
            return _fail(500, "keychain_refused", "the keychain would not save the key")
        return None

    async def settle(name: str) -> dict[str, Any]:
        """Wait briefly for the server to connect, fail, or ask to sign in."""
        await servers().ready(timeout=20, name=name)
        return row(servers().states[name])

    @router.get("")
    async def read() -> dict[str, Any]:
        mcp = rt.mcp
        return {
            "config_path": str(config_path()),
            "servers": [row(s) for s in mcp.states.values()] if mcp is not None else [],
            "problems": mcp.problems if mcp is not None else {},
            "catalog": [catalog.to_json(e) for e in catalog.ENTRIES],
            "programs": {
                p: missing_program(p) is None for p in {e.command for e in catalog.ENTRIES} if p
            },
        }

    @router.post("/parse")
    async def parse(body: Paste) -> Any:
        try:
            found = paste.read(body.text)
        except ConfigError as e:
            return _fail(400, "unreadable", str(e))
        out = []
        for p in found:
            c = parse_server(p.name, p.entry)
            out.append(
                {
                    "name": p.name,
                    "title": p.entry.get("title") or p.name,
                    "entry": p.entry,
                    "connects": c.url if c.transport == "http" else " ".join((c.command, *c.args)),
                    "local": c.transport == "stdio",
                    "signs_in": c.signs_in,
                    "missing": missing_program(c.command) if c.transport == "stdio" else None,
                    # The values come back to the page that just sent them, so the
                    # fields are filled in; they are saved only to the keychain.
                    "secrets": [
                        {"ref": s.ref, "label": s.label, "value": s.value} for s in p.secrets
                    ],
                }
            )
        return {"servers": out}

    @router.post("/servers")
    async def add(body: Add) -> Any:
        mcp = servers()
        if body.catalog:
            entry = catalog.BY_ID.get(body.catalog)
            if entry is None:
                return _fail(404, "unknown")
            if entry.id in mcp.states:
                return _fail(409, "already_connected", f"{entry.title} is already connected")
            name = entry.id
            try:
                raw, secrets = catalog.build(entry, name, body.values)
            except catalog.CatalogError as e:
                return _fail(400, "incomplete", str(e))
        elif body.entry is not None:
            name = unique(paste.slug(body.title or body.proposed or "server"))
            raw, secrets = rekey(body.entry, body.secrets, body.proposed, name)
            if body.title.strip():
                raw["title"] = body.title.strip()[:60]
            if any(not v.strip() for v in secrets.values()):
                return _fail(400, "incomplete", "a key is needed")
        else:
            return _fail(400, "bad_request")
        try:
            config = parse_server(name, raw)
        except ConfigError as e:
            return _fail(400, "invalid", str(e))
        refused = save_secrets(secrets)
        if refused is not None:
            return refused
        try:
            change(config_path(), lambda servers: servers.__setitem__(name, raw))
        except (ConfigError, OSError) as e:
            return _fail(409, "file", str(e))
        logger.info("MCP server added: %s", name)
        await mcp.connect(config)
        rt.helpers_changed()  # Stripe's tools are what the payments watcher reads
        return await settle(name)

    @router.patch("/servers/{name}")
    async def update(name: str, body: Change) -> Any:
        mcp = servers()
        state = mcp.states.get(name)
        if state is None:
            return _fail(404, "unknown")
        if body.tools is not None and any(v not in ("read", "ask") for v in body.tools.values()):
            return _fail(400, "bad_request")

        def edit(entries: dict[str, Any]) -> None:
            entry = entries.get(name)
            if not isinstance(entry, dict):
                raise ConfigError("it is no longer in mcp.json")
            if body.tools is not None:
                found = entry.get("tools")
                tools: dict[str, Any] = found if isinstance(found, dict) else {}
                for tool, choice in body.tools.items():
                    if choice == "read":
                        tools[tool] = {"risk": Risk.READ.value}
                    else:
                        tools.pop(tool, None)
                if tools:
                    entry["tools"] = tools
                else:
                    entry.pop("tools", None)
            if body.disabled is not None:
                if body.disabled:
                    entry["disabled"] = True
                else:
                    entry.pop("disabled", None)
            if body.title is not None and body.title.strip():
                entry["title"] = body.title.strip()[:60]
            edited[0] = entry

        edited: list[Any] = [None]
        try:
            change(config_path(), edit)
            config = parse_server(name, edited[0])
        except (ConfigError, OSError) as e:
            return _fail(409, "file", str(e))
        if body.disabled is not None and body.disabled != state.config.disabled:
            await mcp.connect(config)
            return await settle(name)
        mcp.retune(config)
        return row(mcp.states[name])

    @router.post("/servers/{name}/reconnect")
    async def reconnect(name: str) -> Any:
        mcp = servers()
        state = mcp.states.get(name)
        if state is None:
            return _fail(404, "unknown")
        await mcp.connect(state.config)
        return await settle(name)

    @router.post("/servers/{name}/keys")
    async def keys(name: str, body: Keys) -> Any:
        mcp = servers()
        state = mcp.states.get(name)
        if state is None:
            return _fail(404, "unknown")
        wanted = set(state.config.key_names())
        if not set(body.secrets) <= wanted or any(not v.strip() for v in body.secrets.values()):
            return _fail(400, "bad_request")
        refused = save_secrets({k: v.strip() for k, v in body.secrets.items()})
        if refused is not None:
            return refused
        await mcp.connect(state.config)
        return await settle(name)

    @router.post("/servers/{name}/sign-out")
    async def sign_out(name: str) -> Any:
        mcp = servers()
        state = mcp.states.get(name)
        if state is None:
            return _fail(404, "unknown")
        await mcp.disconnect(name)
        with contextlib.suppress(SecretStoreError):
            rt.secrets.delete(sign_in_key(name))
        await mcp.connect(state.config)
        rt.helpers_changed()  # Stripe's tools are what the payments watcher reads
        return await settle(name)

    @router.delete("/servers/{name}")
    async def remove(name: str) -> Any:
        mcp = servers()
        state = mcp.states.get(name)
        if state is None and name not in mcp.problems:
            return _fail(404, "unknown")
        try:
            change(config_path(), lambda servers: servers.pop(name, None))
        except (ConfigError, OSError) as e:
            return _fail(409, "file", str(e))
        await mcp.remove(name)
        # Its keys go with it: the ones saved for it, and its sign-in. A key another
        # server also names is kept.
        mine = set(state.config.key_names()) if state else set()
        others = {k for s in mcp.states.values() for k in s.config.key_names()}
        for key in (mine - others) | {sign_in_key(name)}:
            with contextlib.suppress(SecretStoreError):
                rt.secrets.delete(key)
        logger.info("MCP server removed: %s", name)
        rt.helpers_changed()  # Stripe's tools are what the payments watcher reads
        return {"removed": name}

    @router.get("/servers/{name}/log")
    async def log(name: str) -> Any:
        if name not in servers().states:
            return _fail(404, "unknown")
        path = paths.home() / "mcp-logs" / f"{name}.log"
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            lines = []
        return PlainTextResponse("\n".join(lines[-LOG_LINES:]) or "Nothing logged yet.")

    return router


def rekey(
    entry: dict[str, Any], secrets: dict[str, str], proposed: str, name: str
) -> tuple[dict[str, Any], dict[str, str]]:
    """Move the keychain names `parse` proposed under one server name to the name it
    is saved under, so a key is never filed under another server's name."""
    old = f"mcp/{proposed}/" if proposed else ""

    def move(text: str) -> str:
        def one(m: Any) -> str:
            key = m.group(1)
            if old and key.startswith(old):
                return f"keychain:mcp/{name}/{key[len(old) :]}"
            return str(m.group(0))

        return KEYCHAIN_REF.sub(one, text)

    raw = {k: v for k, v in entry.items() if k not in ("catalog", "disabled")}
    for section in ("env", "headers"):
        if isinstance(raw.get(section), dict):
            raw[section] = {
                k: move(v) if isinstance(v, str) else v for k, v in raw[section].items()
            }
    moved: dict[str, str] = {}
    for key, value in secrets.items():
        new = f"mcp/{name}/{key[len(old) :]}" if old and key.startswith(old) else key
        if not new.startswith(f"mcp/{name}/"):
            continue  # a key is saved only under this server's own name
        moved[new] = value.strip()
    return raw, moved


def create_callback_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    @router.get("/mcp/callback")
    async def callback(request: Request) -> HTMLResponse:
        params = {k: v for k, v in request.query_params.items() if isinstance(v, str)}
        server = rt.mcp.sign_ins.arrive(params) if rt.mcp is not None else None
        if server is None:
            return _page(
                "This sign-in has expired",
                "Go back to Trillion and choose Sign in again.",
                status=400,
            )
        title = rt.mcp.states[server].config.shown if rt.mcp and server in rt.mcp.states else server
        back = show_link(server)
        if params.get("code"):
            return _page(
                f"Signed in to {title}",
                "It is connected. This tab closes by itself in a moment.",
                back=back,
                close=True,
            )
        return _page(f"{title} was not connected", "You can try again from Settings.", back=back)

    return router


def _page(
    heading: str, text: str, status: int = 200, *, back: str = "/", close: bool = False
) -> HTMLResponse:
    """A small page for the browser after signing in. `back` is where "Back to Trillion"
    goes: Settings, at the connection just made. With `close`, the tab closes itself
    after a moment, so the person lands on the Trillion tab they started from."""
    nonce = secrets.token_urlsafe(16)
    script = (
        f'<script nonce="{nonce}">setTimeout(() => window.close(), 1500);</script>' if close else ""
    )
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Trillion</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;display:grid;place-items:center;min-height:90vh;
margin:0;background:#0b0d12;color:#e8eaf0}}main{{text-align:center;padding:24px}}
h1{{font-size:22px;font-weight:600;margin:0 0 8px}}p{{margin:0 0 20px;color:#9aa1b2}}
a{{color:#2dd4a8;font-weight:600;text-decoration:none}}
@media (prefers-color-scheme: light){{body{{background:#f6f7f9;color:#14161c}}p{{color:#5b6170}}}}
</style></head><body><main><h1>{html.escape(heading)}</h1><p>{html.escape(text)}</p>
<a href="{html.escape(back)}">Back to Trillion</a></main>{script}
</body></html>"""
    csp = (
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
        f"form-action 'none'; frame-ancestors 'none'; script-src 'nonce-{nonce}'"
    )
    return HTMLResponse(body, status_code=status, headers={"Content-Security-Policy": csp})


def show_link(key: str) -> str:
    """Trillion's Settings, at one connection's card."""
    return "/#show=" + quote(key, safe=":")
