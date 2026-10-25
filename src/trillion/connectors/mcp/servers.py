"""The connected MCP servers, kept open while Trillion runs (CON-5).

Each server gets one task that opens its connection, learns its tools, registers
them with the spine, and holds the connection open until Trillion stops. Calls are
made from whoever needs them, bounded by the server's timeout. A server that will
not start, or stops, is reported in Settings and `trillion doctor`; it never stops
Trillion or the other servers.

A local server is a program on the user's machine, started with their privileges.
The spine controls what Trillion asks it to do, not what it does on its own
(CON-6a): only servers the user listed are started, and nothing here adds one.
A server's own messages go to a log file of its own, never to Trillion's output.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from trillion.connectors.mcp import signin
from trillion.connectors.mcp.config import Loaded, MissingKey, ServerConfig, fill
from trillion.connectors.mcp.signin import SIGN_IN_TIMEOUT_S, SignInFailed, SignIns
from trillion.connectors.mcp.tool import McpTool, Offered, Outcome, Unusable, tool_name
from trillion.secrets.store import SecretStore
from trillion.spine.contract import JSON
from trillion.spine.registry import RegistryError, ToolRegistry

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 60.0
Status = Literal["starting", "signing_in", "connected", "failed", "disabled", "stopped"]
DEFAULT_CALLBACK = "http://127.0.0.1:7777/mcp/callback"


@dataclass(kw_only=True)
class ToolInfo:
    name: str  # as the model knows it
    remote: str  # as the server knows it
    description: str
    risk: str
    read_only_hint: bool


@dataclass(kw_only=True)
class ServerState:
    config: ServerConfig
    status: Status = "starting"
    # Why it is not connected, in words that are safe to show. Never a key.
    problem: str = ""
    tools: list[ToolInfo] = field(default_factory=list)
    # Tools it offers that could not be offered safely, with why.
    skipped: dict[str, str] = field(default_factory=dict)
    # Where to sign in, while it waits for the person to (status "signing_in").
    sign_in_url: str = ""
    offered: list[Offered] = field(default_factory=list)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None


class McpServers:
    def __init__(
        self,
        registry: ToolRegistry,
        secrets: SecretStore,
        logs: Path,
        *,
        changed: Callable[[], None] | None = None,
    ) -> None:
        self._registry = registry
        # Told whenever the servers listed change (added, turned on or off, retuned,
        # removed), so what depends on which connectors there are can follow.
        self._changed = changed
        self._secrets = secrets
        self._logs = logs
        self.states: dict[str, ServerState] = {}
        self.problems: dict[str, str] = {}
        self._sessions: dict[str, Any] = {}
        self.sign_ins = SignIns()
        # Where a browser comes back to after signing in; set when the address is known.
        self.callback_url = DEFAULT_CALLBACK

    def start(self, loaded: Loaded) -> None:
        """Begin connecting every server listed. Returns at once."""
        self.problems = dict(loaded.problems)
        for server in loaded.servers:
            self._launch(server)
        self._tell()

    def _tell(self) -> None:
        if self._changed is not None:
            self._changed()

    def _launch(self, server: ServerConfig) -> ServerState:
        state = ServerState(config=server)
        self.states[server.name] = state
        if server.disabled:
            state.status = "disabled"
        else:
            state.task = asyncio.create_task(self._hold(state))
        return state

    async def ready(self, timeout: float = CONNECT_TIMEOUT_S, name: str | None = None) -> None:
        """Wait until every server (or the one named) has connected, failed, or is waiting
        for a person to sign in. For tests, `doctor` and Settings."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while any(
            s.status == "starting" for n, s in self.states.items() if name is None or n == name
        ):
            if loop.time() > deadline:
                return
            await asyncio.sleep(0.05)

    async def connect(self, server: ServerConfig) -> ServerState:
        """Connect a server now, replacing one of the same name. No restart needed."""
        await self.disconnect(server.name)
        self.problems.pop(server.name, None)
        state = self._launch(server)
        self._tell()
        return state

    async def disconnect(self, name: str) -> None:
        """Close a server's connection and take its tools away."""
        state = self.states.get(name)
        if state is None:
            return
        self.sign_ins.cancel(name)
        state.stop.set()
        if state.task is not None:
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(state.task), 5.0)
            state.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await state.task
        self._unregister(state)
        self._tell()

    async def remove(self, name: str) -> None:
        await self.disconnect(name)
        self.states.pop(name, None)
        self.problems.pop(name, None)
        self._tell()

    def retune(self, server: ServerConfig) -> None:
        """Apply new per-tool choices to a connected server without reconnecting."""
        state = self.states.get(server.name)
        if state is None:
            return
        state.config = server
        if state.status == "connected":
            self._unregister(state)
            self._register(state, state.offered)
        self._tell()

    async def stop(self) -> None:
        for name in list(self.states):
            await self.disconnect(name)

    # ── One server ───────────────────────────────────────────────────────

    async def _hold(self, state: ServerState) -> None:
        server = state.config
        # Time for a person to sign in is not time the server took to answer.
        timeout = CONNECT_TIMEOUT_S + (SIGN_IN_TIMEOUT_S if server.signs_in else 0)
        try:
            async with contextlib.AsyncExitStack() as stack:
                session = await asyncio.wait_for(self._open(state, stack), timeout)
                offered = await self._list(session)
                self._sessions[server.name] = session
                state.offered = offered
                self._register(state, offered)
                state.status, state.problem, state.sign_in_url = "connected", "", ""
                logger.info("MCP server %s connected: %d tools", server.name, len(state.tools))
                await state.stop.wait()
        except MissingKey as e:
            state.status, state.problem = "failed", str(e)
        except SignInFailed as e:
            state.status, state.problem = "failed", f"not signed in: {e}"
        except TimeoutError:
            state.status, state.problem = "failed", "it did not answer within a minute"
        except asyncio.CancelledError:
            state.status = "stopped"
            raise
        except BaseException as e:
            state.status = "failed"
            # The kind of failure only: a message can echo a header or an argument.
            state.problem = _why(e)
            logger.warning("MCP server %s failed: %s", server.name, type(e).__name__)
        else:
            state.status = "stopped"
        finally:
            state.sign_in_url = ""
            self._sessions.pop(server.name, None)
            self._unregister(state)

    async def _open(self, state: ServerState, stack: contextlib.AsyncExitStack) -> Any:
        from mcp import ClientSession

        server = state.config

        if server.transport == "stdio":
            from mcp.client.stdio import (
                StdioServerParameters,
                get_default_environment,
                stdio_client,
            )

            env = {**get_default_environment(), **fill(server.env, self._secrets)}
            params = StdioServerParameters(
                command=server.command, args=list(server.args), env=env, cwd=str(Path.home())
            )
            self._logs.mkdir(parents=True, exist_ok=True, mode=0o700)
            log_path = self._logs / f"{server.name}.log"
            errlog = stack.enter_context(open(log_path, "a", encoding="utf-8"))
            with contextlib.suppress(OSError):
                os.chmod(log_path, 0o600)
            read, write = await stack.enter_async_context(stdio_client(params, errlog=errlog))
        else:
            import httpx2
            from mcp.client.streamable_http import streamable_http_client

            # As the MCP transports' own client: long reads for open streams, and no
            # redirects followed, so a key is never sent on to another site.
            def asks_to_sign_in(url: str) -> None:
                state.status, state.sign_in_url = "signing_in", url

            auth = (
                signin.provider(
                    server=server.name,
                    url=server.url,
                    secrets=self._secrets,
                    redirect=self.callback_url,
                    on_open=asks_to_sign_in,
                    sign_ins=self.sign_ins,
                )
                if server.signs_in
                else None
            )
            client = httpx2.AsyncClient(
                headers=fill(server.headers, self._secrets),
                timeout=httpx2.Timeout(30.0, read=300.0),
                auth=auth,
            )
            await stack.enter_async_context(client)
            streams = await stack.enter_async_context(
                streamable_http_client(server.url, http_client=client)
            )
            read, write = streams[0], streams[1]
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return session

    @staticmethod
    async def _list(session: Any) -> list[Offered]:
        offered: list[Offered] = []
        cursor: str | None = None
        for _ in range(50):  # a server that pages forever is not followed forever
            from mcp.types import PaginatedRequestParams

            page = await session.list_tools(
                params=PaginatedRequestParams(cursor=cursor) if cursor else None
            )
            for t in page.tools:
                hints = t.annotations
                offered.append(
                    Offered(
                        name=t.name,
                        description=t.description or "",
                        input_schema=t.input_schema or {},
                        read_only_hint=bool(hints and hints.read_only_hint),
                    )
                )
            cursor = page.next_cursor
            if not cursor:
                break
        return offered

    def _register(self, state: ServerState, offered: list[Offered]) -> None:
        server = state.config
        taken = {d.name for d in self._registry.definitions()}
        for o in offered:
            name = tool_name(server.name, o.name, taken)
            try:
                tool = McpTool(server, o, name, self._caller(server))
                self._registry.register(tool, module="trillion.connectors.mcp")
            except (Unusable, RegistryError) as e:
                state.skipped[o.name] = str(e)
                continue
            taken.add(name)
            state.tools.append(
                ToolInfo(
                    name=name,
                    remote=o.name,
                    description=o.description,
                    risk=tool.definition.risk.value,
                    read_only_hint=o.read_only_hint,
                )
            )

    def _unregister(self, state: ServerState) -> None:
        for t in state.tools:
            self._registry.unregister(t.name)
        state.tools = []
        state.skipped = {}

    def _caller(self, server: ServerConfig) -> Any:
        async def call(tool: str, args: JSON, timeout: float) -> Outcome:
            session = self._sessions.get(server.name)
            if session is None:
                return Outcome(ok=False, text=f"{server.name} is not connected", answered=False)
            try:
                result = await asyncio.wait_for(session.call_tool(tool, dict(args)), timeout)
            except TimeoutError:
                return Outcome(ok=False, text="the server did not answer in time", answered=False)
            except Exception as e:
                logger.warning("MCP call %s.%s failed: %s", server.name, tool, type(e).__name__)
                return Outcome(ok=False, text=_why(e), answered=False)
            return Outcome(ok=not getattr(result, "is_error", False), text=_text_of(result))

        return call


def _text_of(result: Any) -> str:
    """What a tool returned, as text. Pictures and other media are named, not passed."""
    parts: list[str] = []
    for c in getattr(result, "content", None) or []:
        kind = getattr(c, "type", "")
        if kind == "text":
            parts.append(str(getattr(c, "text", "")))
        elif kind in ("image", "audio"):
            parts.append(f"[{kind} omitted]")
        elif kind == "resource":
            resource = getattr(c, "resource", None)
            parts.append(str(getattr(resource, "text", "") or "[a resource, omitted]"))
        else:
            parts.append(f"[{kind or 'content'} omitted]")
    structured = getattr(result, "structured_content", None)
    if not parts and structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False))
    return "\n".join(parts)


def _why(e: BaseException) -> str:
    if isinstance(e, BaseExceptionGroup):
        inner = e.exceptions[0] if e.exceptions else e
        return _why(inner)
    if isinstance(e, SignInFailed):
        return f"not signed in: {e}"
    kind = type(e).__name__
    if kind == "OAuthRegistrationError":
        return "the service would not let Trillion register to sign in"
    if kind in ("OAuthFlowError", "OAuthTokenError"):
        return "signing in did not work; try again"
    status = getattr(getattr(e, "response", None), "status_code", None)
    if status in (401, 403):
        return "the service refused the key: check it, or save a new one"
    if status is not None:
        return f"the service answered with an error ({status})"
    if isinstance(e, FileNotFoundError):
        return "its program was not found: check the command"
    if isinstance(e, PermissionError):
        return "its program could not be run"
    if isinstance(e, ConnectionError | OSError):
        return "it could not be reached"
    return f"it stopped with an error ({type(e).__name__})"
