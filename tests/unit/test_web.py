"""web_fetch (SPN-11b): what she may open, the fence around this machine, and reading a page.

No real network: names resolve through a stand-in, and sockets are httpcore's mock,
which replays canned responses. The fence itself is the real one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import httpcore
import pytest

from trillion.spine.contract import ToolContext
from trillion.tools import web
from trillion.tools.web import Ledger, Refused, WebFetch, addresses_in, normalize, public


def response(
    body: str | bytes, *, status: int = 200, kind: str = "text/html", **head: str
) -> list[bytes]:
    raw = body.encode() if isinstance(body, str) else body
    lines = [f"HTTP/1.1 {status} X", f"Content-Type: {kind}", f"Content-Length: {len(raw)}"]
    lines.append("Connection: close")
    lines += [f"{k.replace('_', '-')}: {v}" for k, v in head.items()]
    return [("\r\n".join(lines) + "\r\n\r\n").encode(), raw]


class Wire(httpcore.AsyncMockBackend):
    """The mock network: each connection answers with the next canned response, and
    every address a socket was opened to is remembered."""

    def __init__(self, *responses: list[bytes]) -> None:
        super().__init__([])
        self._responses = list(responses)
        self.opened: list[str] = []

    async def connect_tcp(self, host: str, port: int, *args: Any, **kw: Any) -> Any:
        self.opened.append(host)
        return httpcore.AsyncMockStream(self._responses.pop(0) if self._responses else [])


def names(table: dict[str, Sequence[str]]) -> Any:
    async def resolver(host: str, port: int) -> list[str]:
        return list(table[host])

    return resolver


PUBLIC = names({"example.com": ["93.184.216.34"], "other.example": ["93.184.216.35"]})


def get(url: str, wire: Wire, resolver: Any = PUBLIC) -> web.Page:
    return asyncio.run(web.fetch(url, resolver=resolver, backend=wire))


# ── Addresses ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("example.com", "https://example.com/"),
        ("HTTP://Example.COM/a?b=1#frag", "http://example.com/a?b=1"),
        ("https://example.com:443/x", "https://example.com/x"),
        ("https://bücher.example/", "https://xn--bcher-kva.example/"),
    ],
)
def test_an_address_has_one_spelling(given: str, expected: str) -> None:
    assert normalize(given) == expected


@pytest.mark.parametrize(
    ("given", "kind"),
    [
        ("ftp://example.com/f", "bad_address"),
        ("file:///etc/passwd", "bad_address"),
        ("https://user:pass@example.com/", "bad_address"),
        ("https://example.com:8080/", "bad_port"),
        ("https://example.com/" + "a" * 3000, "bad_address"),
        ("", "bad_address"),
    ],
)
def test_addresses_outside_the_fence_are_refused(given: str, kind: str) -> None:
    with pytest.raises(Refused) as refused:
        normalize(given)
    assert refused.value.kind == kind


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",
        "100.64.0.1", "0.0.0.0", "224.0.0.1",  # noqa: S104
        "::1", "fc00::1", "fe80::1",
        "::ffff:127.0.0.1", "::ffff:192.168.0.1",
    ],
)  # fmt: skip
def test_this_machine_and_its_networks_are_not_public(address: str) -> None:
    assert not public(address)


@pytest.mark.parametrize("address", ["93.184.216.34", "8.8.8.8", "2606:4700:4700::1111"])
def test_internet_addresses_are_public(address: str) -> None:
    assert public(address)


def test_addresses_are_found_in_what_people_type() -> None:
    said = "Read https://example.com/a?x=1, then example.org/news and www.test.example."
    assert addresses_in(said) == [
        "https://example.com/a?x=1",
        "https://example.org/news",
        "https://www.test.example/",
    ]


def test_the_ledger_knows_only_what_was_given_or_linked() -> None:
    ledger = Ledger()
    ledger.said("c1", "please read example.com/story")
    assert ledger.allows("c1", "http://example.com/story/")  # scheme and slash do not matter
    assert not ledger.allows("c1", "https://example.com/story?leak=secret")  # composed
    assert not ledger.allows("c2", "https://example.com/story")  # another conversation
    ledger.linked("c1", ["https://other.example/next"])
    assert ledger.allows("c1", "https://other.example/next")


# ── The fence ────────────────────────────────────────────────────────────


def test_a_page_is_read_from_the_address_that_was_checked() -> None:
    wire = Wire(
        response(
            "<html><head><title> A  title </title><script>steal()</script></head>"
            "<body><h1>Hello</h1><p>World <a href='/next'>next</a></p>"
            "<style>p{}</style><a href='https://other.example/x'>x</a> "
            "<a href='javascript:alert(1)'>bad</a> <a href='http://10.0.0.1/admin'>lan</a></body></html>"
        )
    )
    page = get("http://example.com/start", wire)
    assert wire.opened == ["93.184.216.34"]  # pinned to the vetted address, not re-resolved
    assert page.title == "A title"
    assert page.text.splitlines() == ["Hello", "World next", "x bad lan"]
    assert "steal" not in page.text and "p{}" not in page.text
    # Links are made absolute; anything that is not a web address is dropped.
    assert page.links == (
        "http://example.com/next",
        "https://other.example/x",
        "http://10.0.0.1/admin",
    )


@pytest.mark.parametrize(
    "addresses", [["127.0.0.1"], ["93.184.216.34", "192.168.1.5"], ["::1"], []]
)
def test_a_name_pointing_at_this_machine_or_its_network_is_refused(addresses: list[str]) -> None:
    wire = Wire(response("never served"))
    with pytest.raises(Refused) as refused:
        get("http://sneaky.example/", wire, names({"sneaky.example": addresses}))
    assert refused.value.kind in ("private_address", "unreachable")
    assert wire.opened == []  # no socket was opened at all


def test_a_redirect_into_the_local_network_is_refused() -> None:
    wire = Wire(response("", status=302, Location="http://router.lan/admin"))
    resolver = names({"example.com": ["93.184.216.34"], "router.lan": ["192.168.0.1"]})
    with pytest.raises(Refused) as refused:
        get("http://example.com/", wire, resolver)
    assert refused.value.kind == "private_address"
    assert wire.opened == ["93.184.216.34"]


def test_an_ordinary_redirect_is_followed_and_reported() -> None:
    wire = Wire(
        response("", status=301, Location="/moved"),
        response("Here now.", kind="text/plain"),
    )
    page = get("http://example.com/old", wire)
    assert (page.url, page.text) == ("http://example.com/moved", "Here now.")


def test_endless_redirects_stop() -> None:
    wire = Wire(*[response("", status=302, Location="/again") for _ in range(7)])
    with pytest.raises(Refused) as refused:
        get("http://example.com/", wire)
    assert refused.value.kind == "too_many_redirects"


def test_what_is_not_text_is_not_read() -> None:
    with pytest.raises(Refused) as refused:
        get("http://example.com/x.zip", Wire(response(b"PK\x03\x04", kind="application/zip")))
    assert refused.value.kind == "not_text"


def test_an_error_page_is_named_by_its_status() -> None:
    with pytest.raises(Refused) as refused:
        get("http://example.com/x", Wire(response("nope", status=404)))
    assert str(refused.value) == "the site answered 404"


# ── The tool ─────────────────────────────────────────────────────────────


def ctx(conversation: str = "c1") -> ToolContext:
    return ToolContext(
        owner_id="local", session_id=conversation, turn=1, call_id="t", via="text", granted_roots=()
    )


def tool(*responses: list[bytes]) -> tuple[WebFetch, Wire]:
    wire = Wire(*responses)
    t = WebFetch()
    t.use(PUBLIC, wire)
    return t, wire


def test_she_cannot_open_an_address_nobody_gave_her() -> None:
    t, wire = tool(response("secret page"))
    r = asyncio.run(t.execute({"url": "https://example.com/?d=private"}, ctx()))
    assert not r.ok and r.error_kind == "not_given" and "ask the user" in r.content
    assert wire.opened == []


def test_an_address_the_user_gave_opens_and_its_links_become_openable() -> None:
    t, _ = tool(
        response("<a href='https://other.example/more'>more</a>"),
        response("More words.", kind="text/plain"),
    )
    t.ledger.said("c1", "summarize example.com/story for me")
    first = asyncio.run(t.execute({"url": "https://example.com/story"}, ctx()))
    assert first.ok and "Links on the page:\n- https://other.example/more" in first.content
    second = asyncio.run(t.execute({"url": "https://other.example/more"}, ctx()))
    assert second.ok and "More words." in second.content
    # A link is openable in the conversation that read it, and nowhere else.
    elsewhere = asyncio.run(t.execute({"url": "https://other.example/more"}, ctx("c2")))
    assert not elsewhere.ok


def test_a_long_page_is_cut_and_says_so() -> None:
    t, _ = tool(response("word " * 5000, kind="text/plain"))
    t.ledger.said("c1", "example.com/long")
    r = asyncio.run(t.execute({"url": "example.com/long"}, ctx()))
    assert r.ok and r.content.endswith("[the rest of the page was cut]")
    assert len(r.content) < web.MAX_CHARS + 200


def test_a_dead_site_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    class Dead(Wire):
        async def connect_tcp(self, host: str, port: int, *a: Any, **kw: Any) -> Any:
            raise httpcore.ConnectError("refused")

    t = WebFetch()
    t.use(PUBLIC, Dead())
    t.ledger.said("c1", "example.com")
    r = asyncio.run(t.execute({"url": "https://example.com/"}, ctx()))
    assert (r.ok, r.error_kind) == (False, "unreachable")
