"""The safety broker: what an action in her browser really does (decisions 7-10).

It sits between every browser tool and Chromium, in the core, where a page can't
reach it. Each request a page makes is routed here before it leaves (Playwright's
request routing), and the proxy (`proxy.py`) checks every connection after DNS. What
an action is gets decided here, from what is actually about to go, never from the
model and never from a button's label:

- **Research mode** sends nothing. A request that isn't a GET is refused, whatever
  made it. A control that submits a form is refused before it is pressed.
- **Act mode** holds what a press sends: the requests are paused in Chromium, the
  card is written from them (the destination, normalised; the method; every field),
  and only the very requests the user approved are let go. Anything else that tries
  to send is refused, as in research mode.
- **Fields the agent never types in:** passwords, one-time codes, card details and
  anything in a payment frame. A form holding one is the user's to send.
- **Checkout and payment pages** stop the task. **A TLS error** stops the task.
- **Uploads and WebSockets** are refused in phase 1.
- **Where it may go:** an origin the user named, one reached in this task, a link a
  page showed, or one the user allowed on a chip. A task on a saved sign-in may go
  only to that site, enforced again at the connection (`may_visit`).
"""

from __future__ import annotations

import asyncio
import contextlib
import email.parser
import email.policy
import hashlib
import json
import logging
import re
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from trillion.browser.log import TaskLog, origin_of
from trillion.browser.proxy import clean_host
from trillion.tools import web

logger = logging.getLogger(__name__)

Mode = Literal["research", "act"]
Stop = Literal["tls", "payment", "search"]
# How long a press waits for what it sends, and for any more after the first.
HOLD_WAIT_S = 2.5
HOLD_MORE_S = 0.4
# Autocomplete values for fields the agent never types in (the HTML standard's names).
SENSITIVE_AUTOCOMPLETE = frozenset(
    {"current-password", "new-password", "one-time-code"}
    | {
        f"cc-{n}"
        for n in (
            "name given-name additional-name family-name number exp exp-month exp-year csc type"
        ).split()
    }
)
# Hosts of known payment frames and checkouts: a field in one is never typed in, and a
# page showing one is a checkout.
PAYMENT_HOSTS = (
    "js.stripe.com",
    "checkout.stripe.com",
    "m.stripe.network",
    "paypal.com",
    "paypalobjects.com",
    "braintreegateway.com",
    "braintree-api.com",
    "adyen.com",
    "checkout.com",
    "pay.google.com",
    "squareup.com",
    "squarecdn.com",
    "klarna.com",
    "affirm.com",
    "authorize.net",
    "worldpay.com",
    "shop.app",
)
_CHECKOUT_PATH = re.compile(r"/(checkout|payment|payments|pay|billing)(/|$|\?|\.)", re.IGNORECASE)
_REF = re.compile(r"^[a-z0-9]{1,16}$")


class Refused(Exception):
    """Refused by the broker. `kind` is a log detail; the message is safe to show."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True, kw_only=True)
class Element:
    """What the host found about an element, read from the page just before acting.
    The page could lie here; the routing below is what holds whatever it says."""

    tag: str
    type: str
    autocomplete: str
    href: str | None
    submits: bool
    editable: bool
    file: bool
    sensitive: bool
    form_sensitive: bool
    frame_url: str

    @property
    def in_payment_frame(self) -> bool:
        return payment_host(urlsplit(self.frame_url).hostname or "")


_CLASSIFY = """(el) => {
  const lower = (v) => (v || '').toString().toLowerCase();
  const tag = lower(el.tagName);
  const type = lower(el.getAttribute('type'));
  const ac = lower(el.getAttribute('autocomplete'));
  const form = el.form || el.closest('form');
  const link = el.closest('a[href]');
  const sensitiveAc = (v) => SENSITIVE.some((s) => lower(v).split(/\\s+/).includes(s));
  const isSensitive = (i) =>
    lower(i.getAttribute('type')) === 'password' || sensitiveAc(i.getAttribute('autocomplete'));
  let submits = false;
  if (tag === 'button') submits = !!form && (type === '' || type === 'submit');
  if (tag === 'input') submits = type === 'submit' || type === 'image';
  const notText = ['submit', 'button', 'image', 'reset', 'checkbox', 'radio', 'file',
                   'hidden', 'range', 'color'];
  const editable = el.isContentEditable || tag === 'textarea' || tag === 'select' ||
    (tag === 'input' && !notText.includes(type));
  let formSensitive = false;
  if (form) {
    for (const i of form.querySelectorAll('input')) {
      if (isSensitive(i) && i.value) formSensitive = true;
    }
  }
  return {
    tag, type, ac,
    href: link ? link.href : null,
    submits, editable,
    file: tag === 'input' && type === 'file',
    sensitive: tag === 'input' && isSensitive(el),
    formSensitive,
    frame: location.href,
  };
}"""


def payment_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in PAYMENT_HOSTS)


def shown_url(url: str) -> str:
    """A URL as a card shows it: the host normalised (no trailing dot, lower case, an
    international name in its ASCII form so a lookalike can't pass for another), the
    port only when it isn't the usual one, the path and the query as they are."""
    parts = urlsplit(url)
    host = clean_host(parts.hostname or "") or "?"
    try:
        port = parts.port
    except ValueError:
        port = None
    default = {"http": 80, "https": 443}.get(parts.scheme)
    netloc = host if port in (None, default) else f"{host}:{port}"
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))


def host_of(url: str) -> str:
    return clean_host(urlsplit(url).hostname or "") or ""


def same_site(host: str, site: str) -> bool:
    """`host` is `site` or one of its own names (`www.site`, `catalog.site`)."""
    host, site = host.rstrip(".").lower(), site.rstrip(".").lower()
    site = site.removeprefix("www.")
    return host == site or host.endswith("." + site)


@dataclass(kw_only=True)
class Held:
    """A request a press would send, paused in Chromium until the user decides."""

    route: Any
    method: str
    url: str
    content_type: str
    body: bytes

    def key(self) -> str:
        h = hashlib.sha256()
        for part in (self.method, self.url, self.content_type):
            h.update(part.encode("utf-8", "replace") + b"\x00")
        h.update(self.body)
        return h.hexdigest()


@dataclass(kw_only=True)
class _Window:
    kind: Literal["press", "follow"]
    expected: str | None = None
    # In act mode, once something was typed on the page: only the link itself leads on.
    strict: bool = False
    sent: int = 0
    off_link: int = 0


class Broker:
    def __init__(
        self,
        *,
        mode: Mode,
        named_hosts: Iterable[str],
        log: TaskLog,
        site: str | None = None,
    ) -> None:
        self.mode: Mode = mode
        self.named = frozenset(h for h in (clean_host(n) for n in named_hosts) if h)
        self.site = clean_host(site) if site else None
        self.log = log
        self.reached: set[str] = set()
        self.allowed: set[str] = set()
        self.links: set[str] = set()
        self.stopped: Stop | None = None
        self.held: list[Held] = []
        self.typed_on_page = False
        # The user has the browser (their step, or her browser opened outside a task):
        # what they send is theirs. Only the proxy's rules, and no uploads, still hold.
        self.user = False
        self._window: _Window | None = None
        self._held_changed = asyncio.Event()
        # Refusals of a page's own background sends, told once per origin.
        self._quiet: set[tuple[str, str]] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    # ── Where it may go ─────────────────────────────────────────────────────

    def may_visit(self, host: str) -> bool:
        """Asked by the proxy of every connection: a task on a saved sign-in stays on
        that site (decision 11a)."""
        return self.site is None or same_site(host, self.site)

    def opening(self, url: str) -> Literal["open", "chip"]:
        """Whether a URL the model gave may open now, or needs the user's chip first.
        Refused outright when it's outside the fence or off a signed-in site."""
        try:
            url = web.normalize(url)
        except web.Refused as e:
            raise Refused(e.kind, str(e)) from e
        host = host_of(url)
        if _private_literal(host):
            raise Refused("private_address", "that is on this computer or its network")
        if not self.may_visit(host):
            raise Refused("off_site", f"this task is signed in to {self.site} and stays there")
        origin = origin_of(url)
        if (
            origin in self.reached
            or origin in self.allowed
            or any(same_site(host, n) and (host == n or host == "www." + n) for n in self.named)
            or web.key(url) in self.links
        ):
            return "open"
        return "chip"

    def allow(self, url: str) -> None:
        origin = origin_of(url)
        if origin is not None:
            self.allowed.add(origin)

    def saw_links(self, urls: Iterable[str]) -> None:
        for u in urls:
            with contextlib.suppress(web.Refused):
                self.links.add(web.key(web.normalize(u)))

    def lock_to(self, url: str) -> None:
        """From now on, this task stays on the site of `url` (after the user signed in
        on it during the task, as a saved sign-in's task does)."""
        host = host_of(url)
        if self.site is None and host:
            self.site = host.removeprefix("www.")

    def reached_page(self, url: str) -> None:
        origin = origin_of(url)
        if origin is not None:
            self.reached.add(origin)

    # ── Routing: every request, before it leaves ────────────────────────────

    async def on_route(self, route: Any) -> None:
        req = route.request
        url = str(req.url)
        method = str(req.method).upper()
        scheme = urlsplit(url).scheme
        if scheme not in ("http", "https"):
            await self._abort(route)
            return
        frame = req.frame if _has_frame(req) else None
        main_nav = bool(req.is_navigation_request()) and (
            frame is None or frame.parent_frame is None
        )
        window = self._window
        body = req.post_data_buffer or b""
        if _uploads(req.headers.get("content-type", ""), body):
            await self._abort(route)
            self._tell_once("refused", url, "upload")
            return
        if self.user and window is None:
            await route.continue_()
            return
        if window is not None and window.kind == "press" and (method != "GET" or main_nav):
            self.held.append(
                Held(
                    route=route,
                    method=method,
                    url=url,
                    content_type=str(req.headers.get("content-type", "")),
                    body=bytes(body),
                )
            )
            self._held_changed.set()
            return
        if method not in ("GET", "HEAD"):
            await self._abort(route)
            if window is not None:
                window.sent += 1
            self._tell_once("refused", url, "sends")
            return
        if main_nav and window is not None and window.kind == "follow" and window.strict:
            if window.expected is None or web.key(url) != web.key(window.expected):
                await self._abort(route)
                window.off_link += 1
                return
        await route.continue_()

    async def on_websocket(self, ws: Any) -> None:
        self._tell_once("refused", str(ws.url).replace("ws", "http", 1), "websocket")
        with contextlib.suppress(Exception):
            await ws.close(code=1008, reason="not in phase 1")

    async def _abort(self, route: Any) -> None:
        with contextlib.suppress(Exception):
            await route.abort("blockedbyclient")

    def _tell_once(self, event: Literal["refused"], url: str, code: str) -> None:
        origin = origin_of(url) or ""
        if (origin, code) in self._quiet:
            return
        self._quiet.add((origin, code))
        self._spawn(self.log.event(event, url=url, code=code))

    def refused_at_proxy(self, kind: str, host: str, port: int) -> None:
        known = ("private_address", "unreachable", "bad_port", "off_site")
        code = kind if kind in known else "bad_address"
        netloc = f"[{host}]" if ":" in host else host
        scheme = "http" if port == 80 else "https"
        suffix = "" if port in (80, 443) else f":{port}"
        self._tell_once("refused", f"{scheme}://{netloc}{suffix}", code)

    def _spawn(self, work: Any) -> None:
        try:
            task = asyncio.get_running_loop().create_task(work)
        except RuntimeError:
            work.close()
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ── Windows: what a follow or a press may let out ───────────────────────

    @contextlib.asynccontextmanager
    async def following(self, expected: str | None) -> AsyncIterator[_Window]:
        window = _Window(kind="follow", expected=expected, strict=self.typed_on_page)
        self._window = window
        try:
            yield window
        finally:
            self._window = None

    @contextlib.asynccontextmanager
    async def pressing(self) -> AsyncIterator[_Window]:
        if self.mode != "act":
            raise Refused("not_act", "research mode: nothing is typed or sent")
        window = _Window(kind="press")
        self._held_changed.clear()
        self._window = window
        try:
            yield window
        finally:
            self._window = None

    async def wait_held(self, first_s: float = HOLD_WAIT_S, more_s: float = HOLD_MORE_S) -> None:
        """Wait for what a press sends: up to `first_s` for the first request, then
        `more_s` after each for any that come with it."""
        wait = first_s
        while True:
            self._held_changed.clear()
            try:
                await asyncio.wait_for(self._held_changed.wait(), wait)
            except TimeoutError:
                return
            wait = more_s

    async def release(self, keys: str) -> bool:
        """Let go exactly the held requests the user approved. False, and every one
        refused, if what is held is no longer what was approved."""
        held, self.held = self.held, []
        if held_key(held) != keys:
            for h in held:
                await self._abort(h.route)
            return False
        for h in held:
            with contextlib.suppress(Exception):
                await h.route.continue_()
        return True

    async def drop(self) -> None:
        held, self.held = self.held, []
        for h in held:
            await self._abort(h.route)

    # ── Pages ───────────────────────────────────────────────────────────────

    def check_page(self, url: str, frame_urls: Iterable[str], has_card_field: bool) -> None:
        """Called on each page she lands on: a checkout stops the task."""
        path = urlsplit(url).path
        if (
            payment_host(host_of(url))
            or _CHECKOUT_PATH.search(path)
            or has_card_field
            or any(payment_host(host_of(f)) for f in frame_urls)
        ):
            self.stopped = "payment"

    def tls_failed(self) -> None:
        self.stopped = "tls"

    async def close(self) -> None:
        await self.drop()
        for task in list(self._tasks):
            with contextlib.suppress(Exception):
                await task


# Words a search engine's block or check page says. Such a page is a stop, never a
# puzzle: her browser doesn't try to get past it.
_TURNED_AWAY = (
    "unusual traffic",
    "not a robot",
    "are you a robot",
    "captcha",
    "if this persists, please",
    "access denied",
    "403 - forbidden",
    "error getting results",
    "verify you are human",
)
_TURNED_AWAY_PATHS = ("/sorry/", "captcha", "error-pages", "home-error", "/blocked")


def search_refused(url: str, title: str, text: str, links: Iterable[str]) -> bool:
    """Whether a search engine turned her browser away: a block or check page, or a
    page with no results to follow."""
    words = f"{title}\n{text}".lower()
    if any(w in words for w in _TURNED_AWAY):
        return True
    if any(p in urlsplit(url).path.lower() for p in _TURNED_AWAY_PATHS):
        return True
    return sum(1 for u in links if u.startswith(("http://", "https://"))) < 3


def _private_literal(host: str) -> bool:
    """A host that is plainly this machine or its network without asking DNS: a
    private address written out, or a `localhost` name. Names are checked after DNS,
    by the proxy, on every request."""
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return not web.public(host)
    except ValueError:
        return False


def held_key(held: Iterable[Held]) -> str:
    h = hashlib.sha256()
    for one in held:
        h.update(one.key().encode())
    return h.hexdigest()


def _has_frame(req: Any) -> bool:
    try:
        return req.frame is not None
    except Exception:
        # A service worker's request has no frame; they're blocked anyway.
        return False


def _uploads(content_type: str, body: bytes) -> bool:
    """A multipart body carrying a file: an upload, refused in phase 1."""
    if "multipart/form-data" not in content_type.lower():
        return False
    return any(name is not None for name, _, _ in multipart_parts(content_type, body))


def valid_ref(ref: str) -> bool:
    return bool(_REF.match(ref))


async def classify(locator: Any) -> Element:
    found = await locator.evaluate(
        _CLASSIFY.replace("SENSITIVE", json.dumps(sorted(SENSITIVE_AUTOCOMPLETE))),
        timeout=5000,
    )
    if not isinstance(found, dict):
        raise Refused("bad_address", "that element can't be read")
    ac = str(found.get("ac", ""))
    return Element(
        tag=str(found.get("tag", "")),
        type=str(found.get("type", "")),
        autocomplete=ac,
        href=str(found["href"]) if found.get("href") else None,
        submits=bool(found.get("submits")),
        editable=bool(found.get("editable")),
        file=bool(found.get("file")),
        sensitive=bool(found.get("sensitive"))
        or any(s in ac.split() for s in SENSITIVE_AUTOCOMPLETE),
        form_sensitive=bool(found.get("formSensitive")),
        frame_url=str(found.get("frame", "")),
    )


# ── The card: written from what is held, exactly ───────────────────────────


@dataclass(frozen=True, kw_only=True)
class Card:
    headline: str
    detail: str


def multipart_parts(content_type: str, body: bytes) -> list[tuple[str | None, str, str]]:
    """(file name or None, field name, value) for each part of a multipart body."""
    parser = email.parser.BytesParser(policy=email.policy.HTTP)
    try:
        msg = parser.parsebytes(
            b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
        )
    except Exception:
        return []
    out: list[tuple[str | None, str, str]] = []
    if not msg.is_multipart():
        return out
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_param("filename", header="content-disposition")
        payload = part.get_payload(decode=True)
        value = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else ""
        out.append((str(filename) if filename is not None else None, str(name or ""), value))
    return out


def _flatten(prefix: str, value: Any, out: list[tuple[str, str]]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}.{k}" if prefix else str(k), v, out)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _flatten(f"{prefix}[{i}]", v, out)
    else:
        out.append((prefix or "(value)", "" if value is None else str(value)))


def fields_of(held: Held) -> list[tuple[str, str]] | str:
    """Every field a request sends, by name; or its whole body as text when it isn't
    in fields. Raises Refused when it can't be shown at all."""
    ctype = held.content_type.lower()
    body = held.body
    if not body:
        return []
    if "application/x-www-form-urlencoded" in ctype:
        return parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True)
    if "multipart/form-data" in ctype:
        return [(name, value) for _, name, value in multipart_parts(held.content_type, body)]
    if "json" in ctype:
        try:
            out: list[tuple[str, str]] = []
            _flatten("", json.loads(body), out)
            return out
        except ValueError:
            pass
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        raise Refused("sends", "it would send data that can't be shown on a card") from None


def _indent(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\n", "\n    ")


def card_for(held: list[Held]) -> Card:
    """The card for what a press would send: for each request, the method and the
    destination exactly as normalised, then every field and its value."""
    if not held:
        raise Refused("sends", "nothing was about to be sent")
    blocks: list[str] = []
    hosts: list[str] = []
    # The same request twice (a double submit) is shown once, saying so.
    times: dict[str, int] = {}
    unique: list[Held] = []
    for h in held:
        k = h.key()
        if k not in times:
            unique.append(h)
        times[k] = times.get(k, 0) + 1
    for h in unique:
        url = shown_url(h.url)
        hosts.append(host_of(h.url))
        again = times[h.key()]
        lines = [f"{h.method} {url}" + (f"  ({again} identical requests)" if again > 1 else "")]
        found = fields_of(h)
        if isinstance(found, str):
            lines.append("  body:")
            lines.append("    " + _indent(found))
        else:
            query = parse_qsl(urlsplit(h.url).query, keep_blank_values=True)
            if h.method == "GET" and query:
                lines.append("  in the address:")
                lines += [f"    {k}: {_indent(v)}" for k, v in query]
            if found:
                lines += [f"  {k}: {_indent(v)}" for k, v in found]
            elif h.method != "GET":
                lines.append("  (no fields)")
        blocks.append("\n".join(lines))
    distinct = list(dict.fromkeys(hosts))
    where = distinct[0] if len(distinct) == 1 else ", ".join(distinct)
    international = any("xn--" in host for host in distinct)
    verb = "Open" if all(h.method == "GET" for h in unique) else "Send to"
    headline = f"{verb} {where}" + (
        " (an international name: check it is the site you mean)" if international else ""
    )
    return Card(headline=headline, detail="\n\n".join(blocks))
