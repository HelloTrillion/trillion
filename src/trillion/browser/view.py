"""Her browser, seen in the app (the her browser spec, decisions 11 and 12).

The page's live picture is Chromium's screencast, sent only to the Trillion pages
watching (`/ws/browser`, behind the same door as every socket: `app.admit`). It is
never sent to the model, never written anywhere and never kept: each frame is handed
to the watchers and dropped. Nothing in this module can reach a model, a router or
a conversation; a structure test keeps it that way.

The user's mouse and keyboard reach the page only while they have control ("Take
control", "Needs you", or her browser opened outside a task). Every other input is
dropped here, before Chromium.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

Send = Callable[[dict[str, Any]], Awaitable[None]]
QUALITY = 60
MAX_TEXT = 1000
# Keys the view passes on by name; anything else is typed as text or dropped.
KEYS = frozenset(
    "Enter Tab Backspace Delete Escape ArrowUp ArrowDown ArrowLeft ArrowRight Home End "
    "PageUp PageDown Space".split()
)


class Screencast:
    """One watcher's live picture of a task's page, following it to a new tab."""

    def __init__(self, target: Any, send: Send) -> None:
        # `target` is a Task: anything with `.context`, `.page` and `.in_control`.
        self._target = target
        self._send = send
        self._cdp: Any = None
        self._page: Any = None
        self._follow: asyncio.Task[None] | None = None
        self._stopped = False

    async def start(self) -> None:
        await self._attach()
        self._follow = asyncio.get_running_loop().create_task(self._watch_tabs())

    async def _attach(self) -> None:
        await self._detach()
        page = self._target.page
        cdp = await self._target.context.new_cdp_session(page)
        cdp.on("Page.screencastFrame", lambda p: asyncio.ensure_future(self._frame(cdp, p)))
        await cdp.send(
            "Page.startScreencast",
            {"format": "jpeg", "quality": QUALITY, "maxWidth": 1280, "maxHeight": 800},
        )
        self._cdp, self._page = cdp, page
        # A page that is still draws no new frame: one picture now, so the view
        # never opens on nothing. Handed on like any frame, and kept nowhere.
        with contextlib.suppress(Exception):
            shot = await cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": QUALITY})
            size = page.viewport_size or {}
            await self._frame(
                cdp,
                {
                    "data": shot.get("data", ""),
                    "metadata": {
                        "deviceWidth": size.get("width"),
                        "deviceHeight": size.get("height"),
                    },
                },
            )

    async def _frame(self, cdp: Any, params: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):
            await cdp.send("Page.screencastFrameAck", {"sessionId": params.get("sessionId")})
        if self._stopped or cdp is not self._cdp:
            return
        meta = params.get("metadata") or {}
        try:
            await self._send(
                {
                    "type": "browser.frame",
                    "data": params.get("data", ""),
                    "width": meta.get("deviceWidth"),
                    "height": meta.get("deviceHeight"),
                    "url": str(self._page.url) if self._page is not None else None,
                    "in_control": bool(self._target.in_control),
                }
            )
        except Exception as e:
            logger.info("a browser frame wasn't sent: %s", type(e).__name__)

    async def _watch_tabs(self) -> None:
        while not self._stopped:
            await asyncio.sleep(0.5)
            try:
                if self._target.page is not self._page:
                    await self._attach()
            except Exception:
                return

    async def _detach(self) -> None:
        cdp, self._cdp = self._cdp, None
        if cdp is not None:
            with contextlib.suppress(Exception):
                await cdp.send("Page.stopScreencast")
            with contextlib.suppress(Exception):
                await cdp.detach()

    async def stop(self) -> None:
        self._stopped = True
        if self._follow is not None:
            self._follow.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._follow
        await self._detach()


async def give_input(target: Any, message: dict[str, Any]) -> bool:
    """The user's mouse or keyboard, to the page, only while they have control. True
    when it reached the page."""
    if not getattr(target, "in_control", False) or getattr(target, "closed", True):
        return False
    page = target.page
    kind = message.get("kind")
    try:
        if kind == "click":
            x, y = float(message["x"]), float(message["y"])
            await page.mouse.click(x, y)
        elif kind == "wheel":
            await page.mouse.wheel(0, max(-5000.0, min(5000.0, float(message.get("dy", 0)))))
        elif kind == "key":
            key = str(message.get("key", ""))
            if key not in KEYS:
                return False
            await page.keyboard.press(" " if key == "Space" else key)
        elif kind == "text":
            text = str(message.get("text", ""))[:MAX_TEXT]
            await page.keyboard.insert_text(text)
        else:
            return False
    except (KeyError, TypeError, ValueError):
        return False
    except Exception as e:
        logger.info("input didn't reach the page: %s", type(e).__name__)
        return False
    return True
