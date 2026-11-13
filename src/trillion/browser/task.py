"""One browsing task: its own context, its broker, its log, and what it waits on.

A task owns a fresh browser context (decision 2a) for as long as it runs, and closes
it when it ends. What it waits on (an `Ask`) is one of: a chip ("Open example.com?"),
a card for a press, the user's step ("Needs you: sign in"), or a saved sign-in's chip.
Each `Ask`'s words are the host's, made from what the broker found (the host it is
on, the kind of step), never from what the page or the model wrote.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from trillion.browser.broker import Broker, Mode, Refused, host_of
from trillion.browser.log import TaskLog, origin_of
from trillion.browser.quarantine import Kept, Quarantine
from trillion.browser.runtime import BrowserRuntime
from trillion.browser.snapshot import Snapshot, take

logger = logging.getLogger(__name__)

AskKind = Literal["chip", "card", "step", "signin"]
StepKind = Literal["sign_in", "captcha", "passkey", "other"]
State = Literal["queued", "working", "needs_you", "done"]
STEP_LIMIT = 30
TIME_LIMIT_S = 300.0
NAV_TIMEOUT_MS = 20_000
SETTLE_S = 0.4
STEP_WORDS: dict[str, str] = {
    "sign_in": "sign in",
    "captcha": "prove you're a person",
    "passkey": "use your passkey",
    "other": "take a step",
}


@dataclass(kw_only=True)
class Ask:
    kind: AskKind
    id: str
    text: str
    url: str | None
    at_ms: int
    # The pending action's id, for a card.
    pending_id: str | None = None
    answer: asyncio.Future[str] = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "text": self.text,
            "origin": origin_of(self.url),
            "at_ms": self.at_ms,
            "pending_id": self.pending_id,
        }


class Task:
    def __init__(
        self,
        *,
        runtime: BrowserRuntime,
        log: TaskLog,
        quarantine: Quarantine,
        request: str,
        mode: Mode,
        named_hosts: frozenset[str],
        clock: Callable[[], int],
        task_id: str | None = None,
        site: str | None = None,
        storage_state: Mapping[str, Any] | None = None,
        search_url: str = "https://html.duckduckgo.com/html/?q={q}",
        attended: bool = True,
        step_limit: int = STEP_LIMIT,
        changed: Callable[[], None] = lambda: None,
    ) -> None:
        self.id = task_id or secrets.token_hex(8)
        self.runtime = runtime
        self.log = log
        self.quarantine = quarantine
        self.request = request
        self.mode: Mode = mode
        self.clock = clock
        self.site = site
        self.storage_state = storage_state
        self.search_url = search_url
        self.attended = attended
        self.step_limit = step_limit
        self.changed = changed
        self.broker = Broker(mode=mode, named_hosts=named_hosts, log=log, site=site)
        self.broker.allow(search_url.replace("{q}", ""))
        self.state: State = "queued"
        self.ask: Ask | None = None
        self.steps = 0
        self.answer: str | None = None
        self._in_control = False
        self.downloads: list[Kept] = []
        self.pressing: tuple[str, str] | None = None
        self.last_snapshot: Snapshot | None = None
        self.started_ms = clock()
        self._context: Any = None
        self._egress: Any = None
        self._pages: list[Any] = []
        self._work: set[asyncio.Task[None]] = set()
        self._refused_hosts: dict[str, str] = {}
        self.closed = False

    # ── The context ─────────────────────────────────────────────────────────

    async def open(self) -> None:
        def refused(kind: str, host: str, port: int) -> None:
            self._refused_hosts[host] = kind
            self.broker.refused_at_proxy(kind, host, port)

        self._egress = self.runtime.egress(may_visit=self.broker.may_visit, refused=refused)
        settings = await self._egress.start()
        self._context = await self.runtime.new_context(settings, storage_state=self.storage_state)
        await self._context.route("**/*", self.broker.on_route)
        await self._context.route_web_socket("**/*", self.broker.on_websocket)
        self._context.on("page", self._adopt)
        page = await self._context.new_page()
        await self.log.event("started", code="asked" if self.mode == "act" else None)
        if self.site is not None:
            await self.log.event("signin", url=f"https://{self.site}", code="allowed")
        self._adopt(page)

    def _adopt(self, page: Any) -> None:
        if page in self._pages:
            return
        self._pages.append(page)
        page.on("dialog", lambda d: self._spawn(_dismiss(d)))
        page.on("download", lambda d: self._spawn(self._download(d)))
        page.on(
            "filechooser",
            lambda _: self._spawn(self.log.event("refused", url=page.url, code="upload")),
        )
        page.on("framenavigated", lambda f: self._navigated(page, f))
        page.on("requestfailed", self._failed)
        page.on("close", lambda _: self._closed_page(page))

    def _navigated(self, page: Any, frame: Any) -> None:
        if frame.parent_frame is None and page is self.page:
            self.broker.typed_on_page = False

    def _failed(self, request: Any) -> None:
        try:
            failure = str(request.failure or "")
            main = request.is_navigation_request() and request.frame.parent_frame is None
        except Exception:
            return
        if main and "ERR_CERT" in failure:
            self.broker.tls_failed()

    def _closed_page(self, page: Any) -> None:
        with contextlib.suppress(ValueError):
            self._pages.remove(page)

    @property
    def in_control(self) -> bool:
        """The user has the browser: the model is shown nothing and acts on nothing."""
        return self._in_control

    @in_control.setter
    def in_control(self, value: bool) -> None:
        self._in_control = value
        self.broker.user = value

    async def reopen(self, *, site: str, storage_state: Mapping[str, Any]) -> None:
        """Start again in a fresh context that has the saved sign-in it was allowed, and
        stay on its site."""
        if self._context is not None:
            with contextlib.suppress(Exception):
                await self._context.close()
        if self._egress is not None:
            await self._egress.close()
        self._pages.clear()
        self.site, self.storage_state = site, storage_state
        self.broker.site = site
        await self.open()

    @property
    def page(self) -> Any:
        if not self._pages:
            raise Refused("failed", "the page was closed")
        return self._pages[-1]

    @property
    def context(self) -> Any:
        return self._context

    def _spawn(self, work: Awaitable[None]) -> None:
        async def run() -> None:
            try:
                await work
            except Exception as e:
                logger.warning("browser task work failed: %s", type(e).__name__)

        task = asyncio.get_running_loop().create_task(run())
        self._work.add(task)
        task.add_done_callback(self._work.discard)

    async def _download(self, download: Any) -> None:
        url = str(download.url)
        try:
            file_id, path = self.quarantine.place(self.id, str(download.suggested_filename))
            await download.save_as(str(path))
            size = self.quarantine.settle(path)
        finally:
            with contextlib.suppress(Exception):
                await download.delete()
        if size is None:
            await self.log.event("download", url=url, code="too_big")
            return
        kind = _kind(path.name)
        self.downloads.append(
            Kept(
                id=file_id,
                task_id=self.id,
                name=path.name,
                origin=origin_of(url),
                size=size,
                kind=kind,
                path=path,
            )
        )
        await self.log.event("download", url=url, code="quarantined")
        self.changed()

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.ask is not None and not self.ask.answer.done():
            self.ask.answer.set_result("cancelled")
        await self.broker.close()
        if self._context is not None:
            with contextlib.suppress(Exception):
                await self._context.close()
        for work in list(self._work):
            work.cancel()
        if self._egress is not None:
            await self._egress.close()
        self.state = "done"
        self.changed()

    # ── Going places ────────────────────────────────────────────────────────

    def refused_host(self, url: str) -> str | None:
        """Why the proxy refused this URL's host, or any host since the last move (a
        redirect's), if it did."""
        kind = self._refused_hosts.pop(host_of(url), None)
        if kind is None and self._refused_hosts:
            kind = self._refused_hosts.popitem()[1]
        return kind

    async def goto(self, url: str) -> None:
        """Open `url` in this task's page, as the broker and the proxy allow."""
        self._refused_hosts.clear()
        try:
            response = await self.page.goto(
                url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS
            )
        except Exception as e:
            await self._nav_error(url, e)
            return
        if response is None or response.status in (403, 502):
            kind = self.refused_host(url)
            if kind is not None:
                raise Refused(kind, _REFUSED.get(kind, "that address is refused"))
        await self.landed()

    async def _nav_error(self, url: str, e: Exception) -> None:
        text = str(e)
        if "ERR_CERT" in text or self.broker.stopped == "tls":
            self.broker.tls_failed()
            await self.log.event("stopped", url=url, code="tls")
            raise Refused("tls", "the site's security certificate isn't valid, so the task stopped")
        kind = self.refused_host(url)
        if kind is not None:
            raise Refused(kind, _REFUSED.get(kind, "that address is refused"))
        if "ERR_BLOCKED_BY_CLIENT" in text:
            raise Refused("sends", "that would have sent something, so it was refused")
        raise Refused("unreachable", "the page could not be opened")

    async def settle(self) -> None:
        await asyncio.sleep(SETTLE_S)
        with contextlib.suppress(Exception):
            await self.page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)

    async def landed(self) -> None:
        """After any move: note where it is, and stop at a checkout."""
        page = self.page
        url = str(page.url)
        if self.broker.stopped == "tls":
            raise Refused("tls", "the site's security certificate isn't valid, so the task stopped")
        if not url.startswith(("http://", "https://")):
            return
        self.broker.reached_page(url)
        await self.log.event("opened", url=url)
        frames = [str(f.url) for f in page.frames]
        try:
            card_field = bool(
                await page.evaluate(
                    "() => !!document.querySelector('input[autocomplete^=\"cc-\"]')"
                )
            )
        except Exception:
            card_field = False
        self.broker.check_page(url, frames, card_field)
        if self.broker.stopped == "payment":
            await self.log.event("stopped", url=url, code="payment")

    async def read(self) -> Snapshot:
        snap = await take(self.page)
        self.broker.saw_links(snap.links)
        self.last_snapshot = snap
        return snap

    def locate(self, ref: str) -> Any:
        return self.page.locator(f"aria-ref={ref}")

    # ── What it waits on ────────────────────────────────────────────────────

    def raise_ask(
        self, kind: AskKind, text: str, url: str | None, pending_id: str | None = None
    ) -> Ask:
        if self.ask is not None and not self.ask.answer.done():
            self.ask.answer.set_result("superseded")
        self.ask = Ask(
            kind=kind,
            id=secrets.token_hex(6),
            text=text,
            url=url,
            at_ms=self.clock(),
            pending_id=pending_id,
        )
        self.state = "needs_you"
        self.changed()
        return self.ask

    def answer_ask(self, ask_id: str, answer: str) -> bool:
        ask = self.ask
        if ask is None or ask.id != ask_id or ask.answer.done():
            return False
        ask.answer.set_result(answer)
        return True

    def clear_ask(self) -> None:
        self.ask = None
        if not self.closed:
            self.state = "working"
        self.changed()

    def to_json(self) -> dict[str, Any]:
        try:
            url = str(self.page.url) if self._pages else None
        except Exception:
            url = None
        return {
            "id": self.id,
            "state": self.state,
            "mode": self.mode,
            "origin": origin_of(url),
            "url": url if url and url.startswith(("http://", "https://")) else None,
            "since_ms": self.started_ms,
            "steps": self.steps,
            "step_limit": self.step_limit,
            "ask": self.ask.to_json() if self.ask is not None else None,
            "in_control": self.in_control,
            "site": self.site,
            "downloads": [d.to_json() for d in self.downloads],
        }


_REFUSED = {
    "private_address": "that is on this computer or its network, and her browser never goes there",
    "unreachable": "that site could not be found",
    "bad_port": "only the standard web ports are opened",
    "off_site": "this task is signed in to one site and stays there",
    "bad_address": "that is not an address she can open",
}


async def _dismiss(dialog: Any) -> None:
    with contextlib.suppress(Exception):
        await dialog.dismiss()


def _kind(name: str) -> str:
    import mimetypes

    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"
