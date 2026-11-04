"""The local server: the web UI's files and one authenticated WebSocket (SPN-11, SPN-11a).

What an unauthenticated client can do is exactly this: fetch the static UI
shell, which holds no user data, and open a socket that will tell it that it
is unauthenticated and close. No model call, no tool and no stored data is
reachable before a valid `hello`.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from trillion import paths
from trillion.api.attention import create_attention_router
from trillion.api.conversations import create_conversations_router
from trillion.api.folders import create_folders_router
from trillion.api.google import create_google_router
from trillion.api.home import create_home_router
from trillion.api.mcp import create_callback_router, create_mcp_router
from trillion.api.persona import create_persona_router
from trillion.api.routines import create_routines_router
from trillion.api.runtime import Runtime
from trillion.api.schedules import create_schedules_router
from trillion.api.session import PROTOCOL, Session, page_host
from trillion.api.settings import create_router
from trillion.api.skills import create_skills_router
from trillion.api.slack import create_slack_router
from trillion.api.spend import create_spend_router
from trillion.api.usage import create_usage_router

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"  # loopback only; there is deliberately no option to change it
DEFAULT_PORT = 7777
HELLO_TIMEOUT_S = 10.0
MAX_FRAME_BYTES = 1 << 20
CLOSE_UNAUTHENTICATED = 4401
CLOSE_FORBIDDEN = 4403
ENV_WEB_DIR = "TRILLION_WEB_DIR"
# The desktop app asks this before it shows the page: only the server it started can
# answer, because only it holds the token (a squatter on the port cannot).
DESKTOP_HELLO = "/api/desktop/hello"
_NONCE = re.compile(r"[A-Za-z0-9_-]{16,64}")

_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; connect-src 'self'; img-src 'self' data:;"
        " style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
_NOT_BUILT = (
    "Trillion is running, but the web UI has not been built.\n\n"
    "From a source checkout:\n"
    "    pnpm install\n"
    "    pnpm --filter @hellotrillion/web build\n"
    "then reload this page.\n"
)


def local_hosts(port: int) -> frozenset[str]:
    return frozenset({f"127.0.0.1:{port}", f"localhost:{port}"})


def web_dir() -> Path | None:
    """Where the built UI is: an override, the installed package, or a source checkout."""
    override = os.environ.get(ENV_WEB_DIR)
    candidates = [
        Path(override) if override else None,
        paths.package_root() / "api" / "static",
        paths.package_root().parents[1] / "apps" / "web" / "dist",
    ]
    return next((c for c in candidates if c is not None and (c / "index.html").is_file()), None)


def create_app(
    rt: Runtime,
    *,
    hosts: frozenset[str],
    extra_origins: frozenset[str] = frozenset(),
    static: Path | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # The scheduler runs on the server's own event loop, from start to stop.
        rt.start_background()
        try:
            yield
        finally:
            await rt.stop_background()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    origins = frozenset(f"http://{h}" for h in hosts) | extra_origins

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        # The default reply quotes the rejected input back, and that input can be a key.
        fields = sorted({str(e["loc"][-1]) for e in exc.errors() if e.get("loc")})
        return JSONResponse({"error": "bad_request", "fields": fields}, status_code=422)

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Any:
        # A page on another site can point a hostname at 127.0.0.1 (DNS rebinding);
        # it cannot make the browser send our Host header.
        if request.headers.get("host") not in hosts:
            return PlainTextResponse("forbidden", status_code=403)
        # The challenge proves the token without sending it, so it needs none.
        challenge = request.url.path == DESKTOP_HELLO
        if request.url.path.startswith("/api/") and request.method != "OPTIONS" and not challenge:
            refusal = _api_refusal(request, rt.token, origins)
            if refusal is not None:
                return refusal
        response = await call_next(request)
        # A page that needs its own policy (Google's file picker) sets it; every
        # other response gets the strict one.
        for name, value in _HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        origin = socket.headers.get("origin")
        # Browsers always send Origin on a WebSocket; a local CLI client sends none.
        if socket.headers.get("host") not in hosts or (
            origin is not None and origin not in origins
        ):
            await socket.close(code=CLOSE_FORBIDDEN)
            return
        await socket.accept()

        async def send(body: dict[str, Any]) -> None:
            await socket.send_text(json.dumps(body, ensure_ascii=False))

        try:
            hello = await asyncio.wait_for(_receive(socket), HELLO_TIMEOUT_S)
        except (TimeoutError, WebSocketDisconnect):
            hello = None
        if isinstance(hello, bytes):
            hello = None
        if not _authenticated(hello, rt.token):
            await _refuse(socket, send)
            return

        resume = hello.get("resume") if hello else None
        session = await Session.start(
            rt,
            send,
            resume if isinstance(resume, str) else None,
            socket.send_bytes,
            host=page_host(hello.get("host") if hello else None),
        )
        try:
            while True:
                msg = await _receive(socket)
                if isinstance(msg, bytes):
                    await session.audio(msg)
                elif msg is None:
                    await send({"type": "error", "code": "bad_message"})
                elif msg.get("type") == "session.end":
                    break
                else:
                    await session.handle(msg)
        except WebSocketDisconnect:
            pass
        finally:
            await session.close()
            with _quiet():
                await socket.close()

    if rt.token_from_app:

        @app.get(DESKTOP_HELLO)
        async def desktop_hello(request: Request) -> JSONResponse:
            nonce = request.query_params.get("n", "")
            if not _NONCE.fullmatch(nonce):
                return JSONResponse({"error": "bad_request"}, status_code=400)
            mac = hmac.new(rt.token.encode(), nonce.encode(), hashlib.sha256).hexdigest()
            return JSONResponse({"mac": mac})

    app.include_router(create_router(rt))
    app.include_router(create_persona_router(rt))
    app.include_router(create_usage_router(rt))
    app.include_router(create_spend_router(rt))
    app.include_router(create_conversations_router(rt))
    app.include_router(create_mcp_router(rt))
    app.include_router(create_callback_router(rt))
    if rt.mcp is not None:
        # A browser comes back here after signing in to a service.
        loopback = sorted(h for h in hosts if h.startswith("127.0.0.1:"))
        if loopback:
            rt.mcp.callback_url = f"http://{loopback[0]}/mcp/callback"
    app.include_router(create_google_router(rt))
    app.include_router(create_slack_router(rt))
    app.include_router(create_skills_router(rt))
    app.include_router(create_folders_router(rt))
    app.include_router(create_schedules_router(rt))
    app.include_router(create_routines_router(rt))
    app.include_router(create_attention_router(rt))
    app.include_router(create_home_router(rt))
    if rt.google is not None:
        loopback = sorted(h for h in hosts if h.startswith("127.0.0.1:"))
        if loopback:
            rt.google.callback_url = f"http://{loopback[0]}/google/callback"
    if extra_origins:
        # Development only: the page is served by Vite on another port.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=sorted(extra_origins),
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["authorization", "content-type"],
        )

    if static is not None:
        app.mount("/", StaticFiles(directory=static, html=True), name="web")
    else:

        @app.get("/")
        async def not_built() -> PlainTextResponse:
            return PlainTextResponse(_NOT_BUILT)

    return app


def _api_refusal(request: Request, token: str, origins: frozenset[str]) -> Response | None:
    """Every API request needs the session token, and a write must be ours (SPN-11).

    A page on another site cannot read the token, cannot set this header without a
    preflight we refuse, and cannot send JSON as a simple request."""
    origin = request.headers.get("origin")
    if origin is not None and origin not in origins:
        return PlainTextResponse("forbidden", status_code=403)
    scheme, _, offered = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(offered.encode(), token.encode()):
        return PlainTextResponse("unauthenticated", status_code=401)
    if request.method != "GET":
        kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if kind != "application/json":
            return PlainTextResponse("unsupported media type", status_code=415)
    return None


async def _receive(socket: WebSocket) -> dict[str, Any] | bytes | None:
    """One JSON object, a binary frame of microphone audio, or None for anything else."""
    frame = await socket.receive()
    if frame["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(frame.get("code", 1000))
    data = frame.get("bytes")
    if data is not None:
        return bytes(data)
    raw = frame.get("text")
    if not isinstance(raw, str) or len(raw) > MAX_FRAME_BYTES:
        return None
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _authenticated(hello: dict[str, Any] | None, token: str) -> bool:
    if hello is None or hello.get("type") != "hello" or hello.get("protocol", PROTOCOL) != PROTOCOL:
        return False
    offered = hello.get("token")
    return isinstance(offered, str) and hmac.compare_digest(offered.encode(), token.encode())


async def _refuse(socket: WebSocket, send: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
    with _quiet():
        await send({"type": "error", "code": "unauthenticated"})
        await socket.close(code=CLOSE_UNAUTHENTICATED)


class _quiet:
    """Closing a socket the other side already closed is not an error worth raising."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, kind: object, exc: BaseException | None, tb: object) -> bool:
        return isinstance(exc, (RuntimeError, WebSocketDisconnect, OSError))
