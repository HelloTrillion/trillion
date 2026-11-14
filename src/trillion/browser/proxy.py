"""Her browser's one way out: a proxy on loopback, one for each task (CMP-5, SPN-11b).

Chromium is started with this proxy for every request it makes, loopback included
(`<-loopback>` takes away the bypass Chromium would otherwise give it). So the rule
`web_fetch` keeps holds for every request a page makes too, redirects and
subresources included, which a page's request routing never sees:

- **Never this machine or its network.** Every address a name resolves to must be
  public (`web.public_addresses`, the same function `web_fetch` uses), checked here,
  after DNS, and the connection is opened to the address that was checked. A name
  that answers differently a second time can't move it.
- **Only the standard web ports**, as `web_fetch`.
- **Only where the task may go.** A task using a saved sign-in may reach only that
  site (decision 11a): `may_visit` says no to anything else, at the connection, so a
  redirect or an image can't carry the account's data elsewhere either.
- **Only Chromium.** The proxy asks for a password made fresh for each task, so
  nothing else on this computer can use it as a way out.

HTTPS goes through as a tunnel (`CONNECT`): the proxy sees the host and port, never
what is inside, and Chromium checks the certificate against the name as always.

The one exception is for tests: `FixtureForTests` lets exactly one loopback address
and port through, where a test's fixture pages are served. Nothing in `src/` ever
makes one (a structure test checks), so a running Trillion can't.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import logging
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from trillion.tools import web

logger = logging.getLogger(__name__)

LOOPBACK = "127.0.0.1"
STANDARD_PORTS = frozenset({80, 443})
MAX_HEAD = 64 * 1024
CONNECT_TIMEOUT_S = 15.0
READ_HEAD_TIMEOUT_S = 30.0
CLOSE_TIMEOUT_S = 3.0
_CHUNK = 64 * 1024
# Hop-by-hop headers a proxy never passes on.
_HOP = frozenset({b"proxy-authorization", b"proxy-connection", b"connection", b"keep-alive"})

Refused = Callable[[str, str, int], None]


@dataclass(frozen=True)
class FixtureForTests:
    """Test-only: made-up host names (`fixture.test`) that lead to a test's fixture
    servers on loopback, each name to one port. Pages are opened by those names on the
    standard ports, so every other rule is the one a running Trillion keeps, and every
    other private address is still refused, loopback by any other name or number
    included. Never made in `src/` (tests/browser/test_structure.py)."""

    hosts: Mapping[str, int]


@dataclass(frozen=True)
class ProxySettings:
    server: str
    username: str
    password: str

    def for_playwright(self) -> dict[str, str]:
        # `<-loopback>` takes away Chromium's own bypass for localhost, so loopback
        # goes through the proxy (and is refused there) like any other address.
        return {
            "server": self.server,
            "bypass": "<-loopback>",
            "username": self.username,
            "password": self.password,
        }


class Egress:
    """One task's way out. `may_visit(host)` is asked for each connection; `refused`
    is told the kind, the host and the port of each one refused (for the task's log)."""

    def __init__(
        self,
        *,
        may_visit: Callable[[str], bool] = lambda host: True,
        refused: Refused = lambda kind, host, port: None,
        resolver: web.Resolve = web.resolve,
        fixture_for_tests: FixtureForTests | None = None,
    ) -> None:
        self._may_visit = may_visit
        self._refused = refused
        self._resolve = resolver
        self._fixture = fixture_for_tests
        self._user = "trillion"
        self._password = secrets.token_urlsafe(24)
        self._expected = b"Basic " + base64.b64encode(f"{self._user}:{self._password}".encode())
        self._server: asyncio.Server | None = None
        self._open: set[asyncio.Task[None]] = set()
        # Every connection's writer, Chromium's side and the far side, to cut on close.
        self._writers: set[asyncio.StreamWriter] = set()

    async def start(self) -> ProxySettings:
        self._server = await asyncio.start_server(self._serve, LOOPBACK, 0)
        port = self._server.sockets[0].getsockname()[1]
        return ProxySettings(
            server=f"http://{LOOPBACK}:{port}", username=self._user, password=self._password
        )

    async def close(self) -> None:
        """Stop at once: every connection is cut, not waited on, and nothing here can
        hold up the task that is ending (a tunnel Chromium keeps open stays open)."""
        server, self._server = self._server, None
        if server is None:
            return
        server.close()
        for writer in list(self._writers):
            with contextlib.suppress(Exception):
                writer.transport.abort()
        tasks = list(self._open)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=CLOSE_TIMEOUT_S)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(server.wait_closed(), CLOSE_TIMEOUT_S)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._open.add(task)
        self._writers.add(writer)
        try:
            await self._one(reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError, TimeoutError, OSError):
            pass
        except asyncio.LimitOverrunError:
            pass
        finally:
            if task is not None:
                self._open.discard(task)
            self._writers.discard(writer)
            writer.close()

    async def _one(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), READ_HEAD_TIMEOUT_S)
        if len(head) > MAX_HEAD:
            await _answer(writer, 431, "Request Header Fields Too Large")
            return
        lines = head.split(b"\r\n")
        try:
            method, target, version = lines[0].split(b" ", 2)
        except ValueError:
            await _answer(writer, 400, "Bad Request")
            return
        headers = [h for h in lines[1:] if h]
        auth = b""
        for h in headers:
            name, _, value = h.partition(b":")
            if name.strip().lower() == b"proxy-authorization":
                auth = value.strip()
        if not hmac.compare_digest(auth, self._expected):
            await _answer(
                writer, 407, "Proxy Authentication Required",
                extra=b'Proxy-Authenticate: Basic realm="trillion"\r\n',
            )  # fmt: skip
            return
        if method == b"CONNECT":
            host, port = _host_port(target.decode("latin-1"), 443)
        else:
            parsed = _absolute(target.decode("latin-1"))
            if parsed is None:
                await _answer(writer, 400, "Bad Request")
                return
            host, port, path = parsed
        if host is None:
            await _answer(writer, 400, "Bad Request")
            return
        address = await self._check(host, port)
        if address is None:
            await _answer(writer, 403, "Forbidden")
            return
        if self._fixture is not None and host in self._fixture.hosts:
            port = self._fixture.hosts[host]
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(address, port), CONNECT_TIMEOUT_S
            )
        except (OSError, TimeoutError):
            await _answer(writer, 502, "Bad Gateway")
            return
        self._writers.add(up_writer)
        try:
            if method == b"CONNECT":
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
            else:
                kept = [h for h in headers if h.split(b":", 1)[0].strip().lower() not in _HOP]
                kept.append(b"Connection: close")
                up_writer.write(b" ".join((method, path.encode("latin-1"), version)) + b"\r\n")
                up_writer.write(b"\r\n".join(kept) + b"\r\n\r\n")
                await up_writer.drain()
            await _pipe_both(reader, writer, up_reader, up_writer)
        finally:
            self._writers.discard(up_writer)
            up_writer.close()

    async def _check(self, host: str, port: int) -> str | None:
        """The address to connect to for `host`, or None when it is refused."""
        if port not in STANDARD_PORTS:
            self._refused("bad_port", host, port)
            return None
        if not self._may_visit(host):
            self._refused("off_site", host, port)
            return None
        fixture = self._fixture
        if fixture is not None and host in fixture.hosts:
            return LOOPBACK
        try:
            addresses = await web.public_addresses(host, port, self._resolve)
        except web.Refused as e:
            self._refused(e.kind, host, port)
            return None
        return addresses[0]


def clean_host(raw: str) -> str | None:
    """A host as one canonical spelling: no brackets, no trailing dot, IDNA, lower case.
    None for anything that isn't a host."""
    host = raw.strip().strip("[]").rstrip(".")
    if not host or any(c in host for c in "/@\\ \t"):
        return None
    try:
        return host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None


def _host_port(authority: str, default: int) -> tuple[str | None, int]:
    if authority.startswith("["):
        end = authority.find("]")
        host, rest = authority[1:end], authority[end + 1 :]
        port_text = rest[1:] if rest.startswith(":") else ""
    else:
        host, _, port_text = authority.rpartition(":") if ":" in authority else (authority, "", "")
    try:
        port = int(port_text) if port_text else default
    except ValueError:
        return None, default
    return clean_host(host), port


def _absolute(target: str) -> tuple[str | None, int, str] | None:
    if not target.lower().startswith("http://"):
        return None
    rest = target[len("http://") :]
    authority, slash, path = rest.partition("/")
    host, port = _host_port(authority, 80)
    return host, port, slash + path if slash else "/"


async def _answer(
    writer: asyncio.StreamWriter, status: int, reason: str, *, extra: bytes = b""
) -> None:
    writer.write(
        f"HTTP/1.1 {status} {reason}\r\n".encode()
        + extra
        + b"Content-Length: 0\r\nConnection: close\r\n\r\n"
    )
    with contextlib.suppress(ConnectionError, OSError):
        await writer.drain()


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(_CHUNK):
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        with contextlib.suppress(Exception):
            if writer.can_write_eof():
                writer.write_eof()


async def _pipe_both(
    a_reader: asyncio.StreamReader,
    a_writer: asyncio.StreamWriter,
    b_reader: asyncio.StreamReader,
    b_writer: asyncio.StreamWriter,
) -> None:
    one = asyncio.create_task(_pipe(a_reader, b_writer))
    two = asyncio.create_task(_pipe(b_reader, a_writer))
    try:
        done, _ = await asyncio.wait({one, two}, return_when=asyncio.FIRST_COMPLETED)
        if one in done and two not in done:
            # Chromium finished sending: the far side gets a moment to answer. When the
            # far side is done first, both close at once, so nothing Chromium sends next
            # on this connection can reach a host it wasn't checked for.
            await asyncio.wait({two}, timeout=5.0)
    finally:
        for t in (one, two):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
