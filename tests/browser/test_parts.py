"""Her browser's parts on their own, with no Chromium: the card, the rules for where it
may go, the proxy, the log and the quarantine."""

from __future__ import annotations

import asyncio
import base64
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from tests.browser.conftest import stand_in_resolver
from trillion import paths
from trillion.browser.broker import (
    Broker,
    Held,
    Refused,
    card_for,
    fields_of,
    held_key,
    payment_host,
    same_site,
    shown_url,
)
from trillion.browser.log import DETAILS, TaskLog, origin_of
from trillion.browser.proxy import Egress, FixtureForTests, clean_host
from trillion.browser.quarantine import MAX_BYTES, Quarantine, safe_name
from trillion.storage.db import connect
from trillion.storage.migrate import migrate
from trillion.storage.sqlite.browser_log_store import KEPT_MS, SqliteBrowserLog


def held(method: str, url: str, ctype: str = "", body: bytes = b"") -> Held:
    return Held(route=None, method=method, url=url, content_type=ctype, body=body)


class Rows:
    def __init__(self) -> None:
        self.rows: list[tuple[Any, ...]] = []

    async def append(self, *row: Any) -> None:
        self.rows.append(row)


def broker(**kw: Any) -> Broker:
    return Broker(
        mode=kw.pop("mode", "research"),
        named_hosts=kw.pop("named", ("library.org",)),
        log=TaskLog(Rows(), "local", "t" * 16, lambda: 0),
        **kw,
    )


# ── The card ─────────────────────────────────────────────────────────────────


def test_a_card_shows_every_field_of_a_form_exactly():
    card = card_for(
        [
            held(
                "POST",
                "https://WWW.Example.COM./enquiry?x=1",
                "application/x-www-form-urlencoded",
                b"name=Kim&message=two%0Alines&empty=",
            )
        ]
    )
    assert card.headline == "Send to www.example.com"
    assert card.detail.splitlines() == [
        "POST https://www.example.com/enquiry?x=1",
        "  name: Kim",
        "  message: two",
        "    lines",
        "  empty: ",
    ]


def test_a_card_for_json_and_for_plain_text():
    j = card_for(
        [held("PUT", "https://api.example.com/v1", "application/json", b'{"a": {"b": [1, "x"]}}')]
    )
    assert "  a.b[0]: 1" in j.detail and "  a.b[1]: x" in j.detail
    t = card_for([held("POST", "https://example.com/", "text/plain", b"hello there")])
    assert "  body:\n    hello there" in t.detail


def test_a_card_names_an_international_lookalike_in_ascii_and_says_so():
    card = card_for([held("POST", "https://exаmple.com/login", "", b"")])  # noqa: RUF001 -- a Cyrillic a
    assert "xn--" in card.headline
    assert "check it is the site you mean" in card.headline
    assert "exаmple" not in card.detail  # noqa: RUF001


def test_a_card_lists_every_destination_when_a_press_sends_to_two():
    card = card_for(
        [
            held("POST", "https://shop.example/order", "application/x-www-form-urlencoded", b"n=1"),
            held("POST", "https://tracker.example/t", "application/x-www-form-urlencoded", b"id=9"),
        ]
    )
    assert card.headline == "Send to shop.example, tracker.example"
    assert "POST https://tracker.example/t\n  id: 9" in card.detail


def test_binary_that_cant_be_shown_is_refused_not_hidden():
    with pytest.raises(Refused):
        fields_of(held("POST", "https://example.com/", "application/octet-stream", b"\xff\xfe\x00"))
    with pytest.raises(Refused):
        card_for([])


def test_a_multipart_body_shows_its_fields():
    body = (
        b'--XX\r\nContent-Disposition: form-data; name="a"\r\n\r\none\r\n'
        b'--XX\r\nContent-Disposition: form-data; name="b"\r\n\r\ntwo\r\n--XX--\r\n'
    )
    found = fields_of(held("POST", "https://e.example/", "multipart/form-data; boundary=XX", body))
    assert found == [("a", "one"), ("b", "two")]


def test_the_fingerprint_is_of_exactly_what_is_held():
    a = [held("POST", "https://e.example/", "text/plain", b"one")]
    b = [held("POST", "https://e.example/", "text/plain", b"two")]
    assert held_key(a) != held_key(b)
    assert held_key(a) == held_key([held("POST", "https://e.example/", "text/plain", b"one")])


def test_shown_url_normalises_the_host_only():
    assert shown_url("http://Example.COM.:80/A?b=C") == "http://example.com/A?b=C"
    assert shown_url("https://example.com:8443/") == "https://example.com:8443/"


# ── Where it may go ──────────────────────────────────────────────────────────


def test_named_reached_linked_and_allowed_open_anything_else_is_a_chip():
    b = broker()
    assert b.opening("https://library.org/catalog") == "open"
    assert b.opening("https://www.library.org/") == "open"
    assert b.opening("https://evil.example/") == "chip"
    b.saw_links(["https://evil.example/page?x=1"])
    assert b.opening("https://evil.example/page?x=1") == "open"
    # A link is that link, not anything on its site the model might make up.
    assert b.opening("https://evil.example/page?x=secret") == "chip"
    b.reached_page("https://news.example/today")
    assert b.opening("https://news.example/other") == "open"
    b.allow("https://chosen.example/")
    assert b.opening("https://chosen.example/x") == "open"


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://a.localhost/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://10.1.2.3/",
        "http://169.254.169.254/",
    ],
)
def test_a_private_address_written_out_is_refused_without_asking(url):
    with pytest.raises(Refused) as e:
        broker().opening(url)
    assert e.value.kind == "private_address"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://a:b@x.example/",
        "http://x.example:8080/",
    ],
)
def test_anything_but_the_web_on_its_ports_is_refused(url):
    with pytest.raises(Refused):
        broker().opening(url)


def test_a_signed_in_task_stays_on_its_site():
    b = broker(site="library.org")
    assert (
        b.may_visit("library.org")
        and b.may_visit("www.library.org")
        and b.may_visit("catalog.library.org")
    )
    assert not b.may_visit("library.org.evil.example") and not b.may_visit("evillibrary.org")
    with pytest.raises(Refused) as e:
        b.opening("https://elsewhere.example/")
    assert e.value.kind == "off_site"


def test_same_site_and_payment_hosts():
    assert same_site("www.example.com", "example.com")
    assert not same_site("example.com.evil", "example.com")
    assert payment_host("js.stripe.com") and payment_host("www.paypal.com")
    assert not payment_host("paypal.com.example")


async def test_a_press_needs_act_mode():
    with pytest.raises(Refused):
        async with broker().pressing():
            pass


# ── The proxy: the one way out ───────────────────────────────────────────────


async def _echo_server() -> tuple[asyncio.Server, int, list[bytes]]:
    got: list[bytes] = []

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        got.append(await reader.read(4096))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], got


async def _ask(proxy_url: str, request: bytes) -> bytes:
    host, port = proxy_url.removeprefix("http://").split(":")
    reader, writer = await asyncio.open_connection(host, int(port))
    writer.write(request)
    await writer.drain()
    answer = await asyncio.wait_for(reader.read(4096), 5)
    writer.close()
    return answer


def _auth(user: str, password: str) -> bytes:
    return (
        b"Proxy-Authorization: Basic " + base64.b64encode(f"{user}:{password}".encode()) + b"\r\n"
    )


async def test_the_proxy_lets_through_only_what_is_public_standard_on_site_and_its_own():
    server, port, got = await _echo_server()
    refused: list[tuple[str, str, int]] = []
    egress = Egress(
        may_visit=lambda host: host != "elsewhere.test",
        refused=lambda kind, host, p: refused.append((kind, host, p)),
        resolver=stand_in_resolver,
        fixture_for_tests=FixtureForTests(hosts={"fixture.test": port}),
    )
    settings = await egress.start()
    auth = _auth(settings.username, settings.password)
    try:
        # Without the task's password: nothing.
        no = await _ask(
            settings.server, b"GET http://fixture.test/ HTTP/1.1\r\nHost: fixture.test\r\n\r\n"
        )
        assert no.startswith(b"HTTP/1.1 407")
        ok = await _ask(
            settings.server,
            b"GET http://fixture.test/x HTTP/1.1\r\nHost: fixture.test\r\n" + auth + b"\r\n",
        )
        assert ok.startswith(b"HTTP/1.1 200") and ok.endswith(b"ok")
        # The upstream got the origin form, and never the proxy password.
        assert got[-1].startswith(b"GET /x HTTP/1.1") and b"Proxy-Authorization" not in got[-1]
        for request, kind in (
            (b"CONNECT localhost:443 HTTP/1.1\r\n", "private_address"),
            (b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n", "private_address"),
            (b"CONNECT [::1]:443 HTTP/1.1\r\n", "private_address"),
            (b"CONNECT lan.test:443 HTTP/1.1\r\n", "private_address"),
            (b"GET http://metadata.test/latest HTTP/1.1\r\n", "private_address"),
            (b"CONNECT fixture.test:22 HTTP/1.1\r\n", "bad_port"),
            (b"CONNECT elsewhere.test:443 HTTP/1.1\r\n", "off_site"),
            (b"CONNECT nowhere.test:443 HTTP/1.1\r\n", "unreachable"),
        ):
            answer = await _ask(settings.server, request + auth + b"\r\n")
            assert answer.startswith(b"HTTP/1.1 403"), request
            assert refused[-1][0] == kind, request
        # Loopback by its own number, on the fixture's port, is still refused.
        answer = await _ask(
            settings.server, f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\n".encode() + auth + b"\r\n"
        )
        assert answer.startswith(b"HTTP/1.1 403")
    finally:
        await egress.close()
        server.close()


def test_clean_host():
    assert clean_host("Example.COM.") == "example.com"
    assert clean_host("[::1]") == "::1"
    assert clean_host("a/b") is None and clean_host("") is None


# ── The log: origins and actions only ────────────────────────────────────────


async def test_the_log_keeps_origins_and_host_words_only():
    rows = Rows()
    log = TaskLog(rows, "local", "t" * 16, lambda: 5)
    await log.event("opened", url="https://example.com/private/path?q=secret#frag")
    await log.event("refused", url="http://x.example:8080/a", code="sends")
    with pytest.raises(ValueError):
        await log.event("refused", url="https://example.com/", code="the page said hello")
    await log.searched("https://search.example/?q=a", "  dentists   open saturday " + "x" * 400)
    assert rows.rows[0][4] == "https://example.com" and rows.rows[0][5] is None
    assert rows.rows[1][4] == "http://x.example:8080"
    assert rows.rows[2][5].startswith("dentists open saturday") and len(rows.rows[2][5]) == 200
    assert "the page said hello" not in DETAILS
    assert origin_of("javascript:alert(1)") is None and origin_of("not a url") is None


async def test_the_log_is_append_only_and_cleared_at_30_days(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path))
    conn = await connect(paths.db_path())
    await migrate(conn)
    store = SqliteBrowserLog(conn)
    import sqlite3
    import time

    import aiosqlite

    errors = (sqlite3.IntegrityError, aiosqlite.IntegrityError, sqlite3.DatabaseError)
    now = int(time.time() * 1000)
    old, new = "a" * 16, "b" * 16
    await store.append("local", old, now - KEPT_MS - 60_000, "started", "https://a.example", None)
    await store.append("local", new, now, "opened", "https://b.example", None)
    await store.append("local", new, now, "started", None, None)
    with pytest.raises(errors):
        await conn.execute("UPDATE browser_log SET origin = NULL WHERE task_id = ?", (new,))
    with pytest.raises(errors):
        await conn.execute("UPDATE browser_log SET origin = 'https://x.example'")
    with pytest.raises(errors, match="append-only"):
        await conn.execute("DELETE FROM browser_log")
    assert await store.clear_old(now) == 1
    assert [r.task_id for r in await store.tasks("local", now)] == [new]
    assert await store.for_task("local", old, now) == []
    assert [r.origin for r in await store.for_task("local", new, now)] == [
        "https://b.example",
        None,
    ]
    with pytest.raises(errors):
        await store.append("local", "c" * 16, now, "said hello", None, None)
    await conn.close()


# ── The quarantine ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("suggested", "safe"),
    [
        ("../../etc/passwd", "passwd"),
        ("..\\..\\boot.ini", "boot.ini"),
        (".bashrc", "bashrc"),
        ("in‮voice.exe", "invoice.exe"),
        ("a:b*c?.txt", "a_b_c_.txt"),
        ("", "download"),
        ("....", "download"),
    ],
)
def test_safe_names(suggested, safe):
    assert safe_name(suggested) == safe


def test_a_long_name_keeps_its_ending():
    name = safe_name("x" * 500 + ".pdf")
    assert len(name) <= 120 and name.endswith(".pdf")


def test_a_kept_file_is_owner_only_and_never_runnable(tmp_path: Path):
    q = Quarantine(tmp_path / "q")
    _, path = q.place("a" * 16, "run.sh")
    path.write_text("#!/bin/sh\n")
    os.chmod(path, 0o700)
    assert q.settle(path) == len("#!/bin/sh\n")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(ValueError):
        q.folder("../escape")


def test_a_file_too_big_is_deleted(tmp_path: Path, monkeypatch):
    import trillion.browser.quarantine as quarantine

    monkeypatch.setattr(quarantine, "MAX_BYTES", 3)
    q = Quarantine(tmp_path / "q")
    _, path = q.place("a" * 16, "big.bin")
    path.write_bytes(b"12345")
    assert q.settle(path) is None and not path.exists()
    assert MAX_BYTES > 3


# ── What it shares with web_fetch ────────────────────────────────────────────


def test_web_fetch_and_her_browser_share_one_fence():
    from trillion.browser import proxy
    from trillion.tools import web

    # The proxy uses web_fetch's own check, not a copy of it.
    assert "web.public_addresses" in Path(proxy.__file__).read_text(encoding="utf-8")
    assert web.normalize("http://[::1]/") == "http://[::1]/"
    ledger = web.Ledger()
    ledger.said("c", "check library.org and https://www.example.com/x")
    ledger.linked("c", ["https://linked.example/"])
    assert ledger.hosts_said("c") == {"library.org", "www.example.com"}


async def test_public_addresses_refuses_any_private_answer():
    from trillion.tools import web

    async def mixed(host: str, port: int) -> list[str]:
        return ["93.184.216.34", "10.0.0.5"]

    async def none(host: str, port: int) -> list[str]:
        return []

    with pytest.raises(web.Refused) as e:
        await web.public_addresses("x.example", 443, mixed)
    assert e.value.kind == "private_address"
    with pytest.raises(web.Refused):
        await web.public_addresses("x.example", 443, none)


def test_a_snapshot_drops_bare_boxes_and_keeps_what_can_be_read_or_pressed():
    from trillion.browser.snapshot import shape

    raw = "\n".join(
        [
            "- generic [ref=e1]:",
            "  - generic [ref=e2]:",
            '    - link "Hours" [ref=e3]:',
            "      - /url: /hours",
            "  - text: Open 9 to 6",
        ]
    )
    snap = shape("https://m.example/", "M", raw)
    assert "generic" not in snap.text
    assert 'link "Hours" [ref=e3]' in snap.text and "Open 9 to 6" in snap.text
    assert snap.links == ("https://m.example/hours",)


def test_older_pages_are_cut_short_for_the_model_and_calls_stay_paired():
    from trillion.brain.types import Msg, ToolResultBlock, ToolUseBlock
    from trillion.browser.service import EARLIER, _older_pages_short

    def page(n: int) -> list[Msg]:
        return [
            Msg(role="assistant", blocks=(ToolUseBlock(id=f"c{n}", name="browser_read", args={}),)),
            Msg(role="user", blocks=(ToolResultBlock(tool_use_id=f"c{n}", content="x" * 2000),)),
        ]

    msgs = [m for n in range(4) for m in page(n)]
    out = _older_pages_short(msgs)
    results = [b for m in out for b in m.blocks if isinstance(b, ToolResultBlock)]
    assert [r.tool_use_id for r in results] == ["c0", "c1", "c2", "c3"]
    assert [r.content == EARLIER for r in results] == [True, True, False, False]


def test_the_main_part_of_a_page_comes_first():
    from trillion.browser.snapshot import compact

    raw = "\n".join(
        [
            "- banner [ref=e1]:",
            '  - link "Menu" [ref=e2]',
            "- main [ref=e3]:",
            '  - heading "Hours" [ref=e4]',
            "  - text: Open 9 to 6",
            "- contentinfo [ref=e5]:",
            "  - text: Footer",
        ]
    )
    assert compact(raw).splitlines()[:3] == [
        "- main [ref=e3]:",
        '  - heading "Hours" [ref=e4]',
        "  - text: Open 9 to 6",
    ]
