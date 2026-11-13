"""Her browser's tools, each with its risk class (the her browser spec, decision 5).

These are a browsing task's only tools (decision 14): `task_registry` builds the
registry a browsing helper runs with, and nothing else is ever in it. They are not
entry points, so her own turn never has them; she starts a task with `browse`
(`browser/service.py`), and the task's tools act on that task's context alone.

| Tool | Risk |
|---|---|
| `browser_open`, `browser_search`, `browser_read`, `browser_find` | read |
| `browser_follow`, `browser_back` | read |
| `browser_type`, `browser_your_step`, `browser_done` | write |
| `browser_press` | irreversible, never recoverable: only a tap on its card |

`browser_press` is the one that sends. Its precheck presses the control with the
broker holding whatever it sends, so the card (`describe`) is written from the held
requests themselves, and its fingerprint is their hash: on approval the router checks
it again, and `execute` lets go exactly those requests and nothing else.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

from trillion.browser.broker import (
    Refused,
    card_for,
    classify,
    held_key,
    host_of,
    valid_ref,
)
from trillion.browser.snapshot import find, shown
from trillion.browser.task import STEP_WORDS, Task
from trillion.spine.card import ONLY_A_TAP
from trillion.spine.contract import (
    ASKS_BY_CARD,
    JSON,
    ActionSummary,
    BaseTool,
    Precondition,
    Risk,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from trillion.spine.registry import ToolRegistry

CATEGORY = "browser"
OPEN, SEARCH, READ, FIND = "browser_open", "browser_search", "browser_read", "browser_find"
FOLLOW, BACK, TYPE, PRESS = "browser_follow", "browser_back", "browser_type", "browser_press"
YOUR_STEP, DONE = "browser_your_step", "browser_done"
NAMES = (OPEN, SEARCH, READ, FIND, FOLLOW, BACK, TYPE, PRESS, YOUR_STEP, DONE)
TIMEOUT_S = 60.0
CLICK_TIMEOUT_MS = 5_000
_UNWATCHED = frozenset({"schedule", "routine", "refresh"})
_REF: dict[str, Any] = {
    "type": "string",
    "pattern": r"^[a-z0-9]{1,16}$",
    "description": "An element's reference from the page, such as e12.",
}
_STOPPED = {
    "tls": "the site's security certificate isn't valid, so the task stopped",
    "payment": (
        "this is a checkout or payment page: that needs the user, and a purchase is never "
        "made from her browser. Call browser_done and say so"
    ),
}


def _schema(props: dict[str, Any], required: tuple[str, ...]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(required),
        "additionalProperties": False,
    }


def _no(content: str, kind: str = "refused") -> ToolResult:
    return ToolResult(ok=False, content=content, effect="none", error_kind=kind)


class BrowserTool(BaseTool):
    """A tool of one task. Every one checks first that the task can act at all."""

    def __init__(self, task: Task) -> None:
        self.task = task

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        t = self.task
        if t.closed:
            return Precondition.failed("the task has ended")
        if t.broker.stopped is not None:
            return Precondition.failed(_STOPPED[t.broker.stopped])
        if t.in_control:
            return Precondition.failed("the user has the browser now; wait for them")
        if t.ask is not None and not self._continues(args):
            return Precondition.failed(
                f"waiting for the user ({t.ask.text}); stop here, you will be told their answer"
            )
        if t.steps >= t.step_limit and self.definition.name != DONE:
            return Precondition.failed(
                "out of steps: call browser_done now with what you found so far"
            )
        return Precondition.passed()

    def _continues(self, args: JSON) -> bool:
        """Whether this call carries on with what the task waits on (a press's card)."""
        return False

    async def _moved(self) -> str:
        """After a move: where it is now, and the page as text."""
        await self.task.landed()
        if self.task.broker.stopped is not None:
            return _STOPPED[self.task.broker.stopped]
        return shown(await self.task.read())


class BrowserOpen(BrowserTool):
    definition = ToolDefinition(
        name=OPEN,
        description=(
            "Open a web address in your browser and get the page as text. An address the "
            "user named, a site you already reached in this task, or a link a page showed "
            "opens at once; any other site first asks the user on a chip, so prefer "
            "following links. Pages on this computer or its network are always refused."
        ),
        input_schema=_schema(
            {"url": {"type": "string", "minLength": 1, "maxLength": 2048}}, ("url",)
        ),
        risk=Risk.READ,
        category=CATEGORY,
        timeout_s=TIMEOUT_S,
    )

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        t = self.task
        url = str(args.get("url", ""))
        try:
            decision = t.broker.opening(url)
        except Refused as e:
            await t.log.event("refused", url=url if "://" in url else f"https://{url}", code=e.kind)
            return _no(str(e), e.kind)
        from trillion.tools.web import normalize

        url = normalize(url)
        if decision == "chip":
            if not t.attended or ctx.via in _UNWATCHED:
                await t.log.event("chip", url=url, code="unattended")
                return _no("nobody is here to allow a new site; use the sites you have", "chip")
            ask = t.raise_ask("chip", f"Open {host_of(url)}?", url)
            await t.log.event("chip", url=url, code="asked")
            return _no(
                f'Asked the user on a chip: "{ask.text}". Stop here and wait; you will be '
                "told their answer.",
                "chip",
            )
        try:
            await t.goto(url)
            return ToolResult(ok=True, content=await self._moved(), effect="none")
        except Refused as e:
            await t.log.event("refused", url=url, code=e.kind)
            return _no(str(e), e.kind)


class BrowserSearch(BrowserTool):
    definition = ToolDefinition(
        name=SEARCH,
        description=(
            "Search the web with the search engine the user chose, and get the results page "
            "as text. Put only words from the user's request in the query."
        ),
        input_schema=_schema(
            {"query": {"type": "string", "minLength": 1, "maxLength": 200}}, ("query",)
        ),
        risk=Risk.READ,
        category=CATEGORY,
        timeout_s=TIMEOUT_S,
    )

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        t = self.task
        query = " ".join(str(args.get("query", "")).split())
        url = t.search_url.replace("{q}", quote_plus(query))
        await t.log.searched(url, query)
        try:
            await t.goto(url)
            return ToolResult(ok=True, content=await self._moved(), effect="none")
        except Refused as e:
            await t.log.event("refused", url=url, code=e.kind)
            return _no(str(e), e.kind)


class BrowserRead(BrowserTool):
    definition = ToolDefinition(
        name=READ,
        description=(
            "The page you are on, as text, with a reference (such as e12) for each element."
        ),
        input_schema=_schema({}, ()),
        risk=Risk.READ,
        category=CATEGORY,
        timeout_s=TIMEOUT_S,
    )

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=True, content=shown(await self.task.read()), effect="none")


class BrowserFind(BrowserTool):
    definition = ToolDefinition(
        name=FIND,
        description=(
            "The elements on this page whose text holds the words given, with their references."
        ),
        input_schema=_schema(
            {"text": {"type": "string", "minLength": 1, "maxLength": 200}}, ("text",)
        ),
        risk=Risk.READ,
        category=CATEGORY,
        timeout_s=TIMEOUT_S,
    )

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        from trillion.browser.snapshot import take

        raw = await take(self.task.page, budget=200_000)
        self.task.broker.saw_links(raw.links)
        lines = find(raw.text, str(args.get("text", "")))
        if not lines:
            return ToolResult(ok=True, content="Nothing on this page matches.", effect="none")
        return ToolResult(ok=True, content="\n".join(lines), effect="none")


class BrowserFollow(BrowserTool):
    definition = ToolDefinition(
        name=FOLLOW,
        description=(
            "Follow a link, or press a control that only opens or shows something (a tab, a "
            "menu, 'more'). It never sends anything: a control that submits a form is "
            "refused here."
        ),
        input_schema=_schema({"ref": _REF}, ("ref",)),
        risk=Risk.READ,
        category=CATEGORY,
        timeout_s=TIMEOUT_S,
    )

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        t = self.task
        ref = str(args.get("ref", ""))
        if not valid_ref(ref):
            return _no("that is not an element's reference")
        locator = t.locate(ref)
        try:
            if await locator.count() != 1:
                return _no("no such element on this page now; read the page again")
            el = await classify(locator)
        except Refused as e:
            return _no(str(e), e.kind)
        except Exception:
            return _no("no such element on this page now; read the page again")
        url = str(t.page.url)
        if el.file:
            await t.log.event("refused", url=url, code="upload")
            return _no("files are never uploaded from her browser", "upload")
        if el.submits:
            await t.log.event("refused", url=url, code="submits")
            return _no(
                "that control submits a form, and following never sends anything"
                + ("; use browser_press" if t.mode == "act" else ": this task only reads"),
                "submits",
            )
        async with t.broker.following(el.href) as window:
            try:
                await locator.click(timeout=CLICK_TIMEOUT_MS)
            except Exception:
                return _no("that element could not be pressed; read the page again")
            await t.settle()
        if window.sent:
            await t.log.event("refused", url=url, code="sends")
            return _no(
                "that tried to send something, so nothing was sent; the page may have changed",
                "sends",
            )
        if window.off_link:
            await t.log.event("refused", url=url, code="sends")
            return _no(
                "after typing, only a link itself leads on; use browser_press to send the form",
                "sends",
            )
        await t.log.event("followed", url=str(t.page.url))
        try:
            return ToolResult(ok=True, content=await self._moved(), effect="none")
        except Refused as e:
            return _no(str(e), e.kind)


class BrowserBack(BrowserTool):
    definition = ToolDefinition(
        name=BACK,
        description="Go back to the page before.",
        input_schema=_schema({}, ()),
        risk=Risk.READ,
        category=CATEGORY,
        timeout_s=TIMEOUT_S,
    )

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        t = self.task
        try:
            went = await t.page.go_back(wait_until="domcontentloaded", timeout=20_000)
        except Exception:
            went = None
        if went is None and len(t._pages) > 1:
            await t.page.close()
        await t.log.event("back", url=str(t.page.url))
        try:
            return ToolResult(ok=True, content=await self._moved(), effect="none")
        except Refused as e:
            return _no(str(e), e.kind)


class BrowserType(BrowserTool):
    definition = ToolDefinition(
        name=TYPE,
        description=(
            "Type text into a field on the page (only in a task that asked to act). Never "
            "a password, a code or card details: those are the user's step."
        ),
        input_schema=_schema(
            {"ref": _REF, "text": {"type": "string", "maxLength": 4000}}, ("ref", "text")
        ),
        risk=Risk.WRITE,
        category=CATEGORY,
        timeout_s=TIMEOUT_S,
    )

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        t = self.task
        url = str(t.page.url)
        if t.mode != "act":
            await t.log.event("refused", url=url, code="not_act")
            return _no("this task only reads: nothing is typed", "not_act")
        ref = str(args.get("ref", ""))
        if not valid_ref(ref):
            return _no("that is not an element's reference")
        locator = t.locate(ref)
        try:
            if await locator.count() != 1:
                return _no("no such element on this page now; read the page again")
            el = await classify(locator)
        except Exception:
            return _no("no such element on this page now; read the page again")
        if el.in_payment_frame or el.autocomplete.startswith("cc-") or " cc-" in el.autocomplete:
            t.broker.stopped = "payment"
            await t.log.event("refused", url=url, code="payment")
            return _no(_STOPPED["payment"], "payment")
        if el.sensitive:
            await t.log.event("refused", url=url, code="sensitive_field")
            return await _step(t, ctx, "sign_in", url)
        if not el.editable:
            return _no("that element is not a field you can type in")
        try:
            await locator.fill(str(args.get("text", "")), timeout=CLICK_TIMEOUT_MS)
        except Exception:
            return _no("that field could not be typed in")
        t.broker.typed_on_page = True
        await t.log.event("typed", url=url)
        return ToolResult(
            ok=True, content="Typed. Nothing is sent until you press.", effect="confirmed"
        )


class BrowserPress(BrowserTool):
    definition = ToolDefinition(
        name=PRESS,
        description=(
            "Press a button that sends or submits (only in a task that asked to act). The "
            "user sees a card with exactly where it goes and everything it sends, and only "
            "their tap lets it go. " + ASKS_BY_CARD + " " + ONLY_A_TAP
        ),
        input_schema=_schema({"ref": _REF}, ("ref",)),
        risk=Risk.IRREVERSIBLE,
        category=CATEGORY,
        timeout_s=TIMEOUT_S,
        recoverable=False,
    )

    def _continues(self, args: JSON) -> bool:
        t = self.task
        return (
            t.pressing is not None
            and t.pressing[0] == str(args.get("ref", ""))
            and bool(t.broker.held)
        )

    def target(self, args: JSON) -> str:
        return f"browser:{self.task.id}:press"

    def describe(self, args: JSON) -> ActionSummary:
        card = card_for(self.task.broker.held)
        return ActionSummary(text=card.headline, detail=card.detail)

    def fingerprint(self, args: JSON) -> str | None:
        return held_key(self.task.broker.held)

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        pre = await super().precheck(args, ctx)
        if not pre.ok:
            return pre
        t = self.task
        ref = str(args.get("ref", ""))
        if self._continues(args):
            # The card's own press, checked again on approval: what is held stands.
            return Precondition.passed()
        url = str(t.page.url)
        if t.mode != "act":
            await t.log.event("refused", url=url, code="not_act")
            return Precondition.failed("this task only reads: nothing is pressed or sent")
        if ctx.via in _UNWATCHED or not t.attended:
            return Precondition.failed("nobody is here to approve what it would send")
        if not valid_ref(ref):
            return Precondition.failed("that is not an element's reference")
        locator = t.locate(ref)
        try:
            if await locator.count() != 1:
                return Precondition.failed("no such element on this page now; read the page again")
            el = await classify(locator)
        except Exception:
            return Precondition.failed("no such element on this page now; read the page again")
        if el.file:
            await t.log.event("refused", url=url, code="upload")
            return Precondition.failed("files are never uploaded from her browser")
        if el.form_sensitive or el.sensitive or el.in_payment_frame:
            await t.log.event("refused", url=url, code="sensitive_field")
            await _step(t, ctx, "sign_in", url)
            return Precondition.failed(
                "that form holds a password, a code or card details: sending it is the user's "
                "step, and they were asked. Stop here and wait."
            )
        await t.broker.drop()
        try:
            async with t.broker.pressing():
                try:
                    # What it sends is held, so the navigation it starts can't finish:
                    # the click doesn't wait for it.
                    await locator.click(timeout=CLICK_TIMEOUT_MS, no_wait_after=True)
                except Exception:
                    return Precondition.failed(
                        "that element could not be pressed; read the page again"
                    )
                await t.broker.wait_held()
        except Refused as e:
            return Precondition.failed(str(e))
        if not t.broker.held:
            t.pressing = None
            return Precondition.failed(
                "pressing it sent nothing (it only changed the page); read the page again"
            )
        try:
            card_for(t.broker.held)
        except Refused as e:
            await t.broker.drop()
            return Precondition.failed(str(e))
        t.pressing = (ref, held_key(t.broker.held))
        await t.log.event("card", url=t.broker.held[0].url, code="asked")
        return Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        t = self.task
        pressing, t.pressing = t.pressing, None
        if pressing is None or not await t.broker.release(pressing[1]):
            return ToolResult(
                ok=False, content="what it would send changed, so nothing was sent", effect="none"
            )
        await t.log.event("sent", url=str(t.page.url))
        await t.settle()
        try:
            return ToolResult(
                ok=True, content="Sent.\n\n" + await self._moved(), effect="confirmed"
            )
        except Refused as e:
            return ToolResult(ok=True, content=f"Sent. Then: {e}", effect="confirmed")


async def _step(t: Task, ctx: ToolContext, kind: str, url: str) -> ToolResult:
    if not t.attended or ctx.via in _UNWATCHED:
        await t.log.event("step", url=url, code="unattended")
        return _no("that needs the user, and nobody is here; finish with what you have", "step")
    words = STEP_WORDS.get(kind, STEP_WORDS["other"])
    ask = t.raise_ask("step", f"Needs you: {words} on {host_of(url) or 'this page'}", url)
    await t.log.event("step", url=url, code=kind if kind in STEP_WORDS else "other")
    return ToolResult(
        ok=True,
        content=(
            f'Asked the user: "{ask.text}". Stop here and wait; you will be told when they '
            "are done."
        ),
        effect="confirmed",
    )


class BrowserYourStep(BrowserTool):
    definition = ToolDefinition(
        name=YOUR_STEP,
        description=(
            "Ask the user to do one step themselves in your browser: sign in, prove they "
            "are a person, or use a passkey. Then stop and wait until they are done."
        ),
        input_schema=_schema({"kind": {"type": "string", "enum": list(STEP_WORDS)}}, ("kind",)),
        risk=Risk.WRITE,
        category=CATEGORY,
        timeout_s=TIMEOUT_S,
    )

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        return await _step(self.task, ctx, str(args.get("kind", "other")), str(self.task.page.url))


class BrowserDone(BrowserTool):
    definition = ToolDefinition(
        name=DONE,
        description=(
            "End the task and give your answer: what you found, in a few plain sentences, "
            "with the addresses of the pages it came from."
        ),
        input_schema=_schema(
            {"answer": {"type": "string", "minLength": 1, "maxLength": 6000}}, ("answer",)
        ),
        risk=Risk.WRITE,
        category=CATEGORY,
        timeout_s=TIMEOUT_S,
    )

    async def precheck(self, args: JSON, ctx: ToolContext) -> Precondition:
        if self.task.closed:
            return Precondition.failed("the task has ended")
        if self.task.ask is not None:
            return Precondition.failed(
                f"waiting for the user ({self.task.ask.text}); stop here, you will be told "
                "their answer"
            )
        return Precondition.passed()

    async def execute(self, args: JSON, ctx: ToolContext) -> ToolResult:
        self.task.answer = str(args.get("answer", ""))
        await self.task.log.event("done", code="answered")
        return ToolResult(ok=True, content="Done. Say nothing more.", effect="confirmed")


TOOLS: tuple[type[BrowserTool], ...] = (
    BrowserOpen,
    BrowserSearch,
    BrowserRead,
    BrowserFind,
    BrowserFollow,
    BrowserBack,
    BrowserType,
    BrowserPress,
    BrowserYourStep,
    BrowserDone,
)


def task_registry(task: Task) -> ToolRegistry:
    """The only tools a browsing task has: these, bound to this task. No memory, no
    files, no accounts, no board, no rules, no helpers (decision 14)."""
    registry = ToolRegistry()
    for cls in TOOLS:
        registry.register(cls(task), module=__name__)
    return registry


def offered(registry: ToolRegistry) -> ToolRegistry:
    """What the browsing model is shown: the browser tools, without the spine's meta
    tools (a press is approved only by the user's tap on its card)."""
    return registry.offered(lambda d: d.name in NAMES and d.category == CATEGORY)
