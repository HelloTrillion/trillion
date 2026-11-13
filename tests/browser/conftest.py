"""Her browser's tests: a headless Chromium against fixture pages on this computer.

The fixture servers are the one place a private address is reached, through the
test-only `FixtureForTests` switch: made-up names (`fixture.test`, `other.test`,
`badtls.test`) lead to them on loopback, opened on the standard ports as any site
is. Every other private address is still refused, loopback by any other name
included. Nothing else here touches the network: other names resolve through a
stand-in that never asks DNS.

Tests that need Chromium skip cleanly without it. CI's browser job fetches the
pinned one into `$TRILLION_TEST_BROWSER/chromium` (as `Install.fetch` would) and
points this at it.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import hashlib
import json
import os
import ssl
import threading
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from trillion.browser.proxy import FixtureForTests
from trillion.browser.runtime import PIN_FILE, PINNED, Install, playwright_available
from trillion.tools import web

PAGES = Path(__file__).parent / "pages"
# Names the stand-in resolver answers, each with a private address: refused.
PRIVATE_NAMES = {
    "lan.test": ["192.168.1.20"],
    "metadata.test": ["169.254.169.254"],
    "localhost": ["127.0.0.1"],
}


def _base() -> Path | None:
    raw = os.environ.get("TRILLION_TEST_BROWSER", "").strip()
    return Path(raw) if raw else None


def chromium_ready() -> bool:
    base = _base()
    return playwright_available() and base is not None and Install(base)._executable() is not None


needs_chromium = pytest.mark.skipif(
    not chromium_ready(),
    reason="her browser's Chromium isn't here: set TRILLION_TEST_BROWSER (see conftest)",
)


@dataclass
class Seen:
    """What the fixture servers received: the proof of what left the browser."""

    requests: list[tuple[str, str, str, bytes]] = field(default_factory=list)

    def posts(self) -> list[tuple[str, str, bytes]]:
        return [(h, p, b) for m, h, p, b in self.requests if m != "GET"]

    def paths(self) -> list[str]:
        return [p for _, _, p, _ in self.requests]


def _handler(seen: Seen) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _record(self, body: bytes = b"") -> None:
            seen.requests.append((self.command, self.headers.get("Host", ""), self.path, body))

        def do_GET(self) -> None:
            self._record()
            path = self.path.split("?", 1)[0]
            if path == "/redirect-private":
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1/admin")
                self.end_headers()
                return
            if path == "/signed-in":
                body = b"<html><title>Signed in</title><body>Welcome back</body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Set-Cookie", "session=yes; Path=/; HttpOnly")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/file.bin":
                body = b"#!/bin/sh\necho hello\n"
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", 'attachment; filename="../../evil.sh"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path in ("/", ""):
                path = "/index.html"
            file = PAGES / path.lstrip("/")
            if path == "/search" or not file.is_file():
                body = (
                    b"<html><head><title>Results</title></head><body><h1>Results</h1></body></html>"
                )
            else:
                body = file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            self._record(self.rfile.read(length))
            body = (
                b"<html><head><title>Thanks</title></head><body><h1>Thanks, sent</h1></body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    return Handler


def _self_signed(tmp: Path) -> ssl.SSLContext:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "badtls.test")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("badtls.test")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = tmp / "cert.pem", tmp / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_file, key_file)
    return ctx


@dataclass
class Fixtures:
    seen: Seen
    fixture: FixtureForTests


@pytest.fixture(scope="session")
def fixtures(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Fixtures]:
    seen = Seen()
    plain = ThreadingHTTPServer(("127.0.0.1", 0), _handler(seen))
    tls = ThreadingHTTPServer(("127.0.0.1", 0), _handler(seen))
    tls.socket = _self_signed(tmp_path_factory.mktemp("tls")).wrap_socket(
        tls.socket, server_side=True
    )
    for server in (plain, tls):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    port, tls_port = plain.server_address[1], tls.server_address[1]
    yield Fixtures(
        seen=seen,
        fixture=FixtureForTests(
            hosts={"fixture.test": port, "other.test": port, "badtls.test": tls_port}
        ),
    )
    for server in (plain, tls):
        server.shutdown()


async def stand_in_resolver(host: str, port: int) -> list[str]:
    """Never DNS: a private address for the names above, nothing for any other."""
    if host in PRIVATE_NAMES:
        return PRIVATE_NAMES[host]
    with contextlib.suppress(ValueError):
        import ipaddress

        return [str(ipaddress.ip_address(host))]
    raise web.Refused("unreachable", f"{host} could not be found")


def ready_install(tmp_path: Path) -> Install:
    """The test Chromium, with the pin a fetch writes beside it."""
    base = _base()
    assert base is not None
    install = Install(base)
    exe = install._executable()
    assert exe is not None
    pin = install.browsers / PIN_FILE
    if not pin.exists():
        digest = hashlib.sha256(exe.read_bytes()).hexdigest()
        pin.write_text(json.dumps({"revision": PINNED, "version": "test", "sha256": digest}))
    return install


@pytest.fixture
async def browser_rt(
    fixtures: Fixtures, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Any]:
    from trillion.browser.runtime import BrowserRuntime

    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    rt = BrowserRuntime(
        ready_install(tmp_path),
        resolver=stand_in_resolver,
        fixture_for_tests=fixtures.fixture,
    )
    yield rt
    await asyncio.wait_for(rt.stop(), 30)
