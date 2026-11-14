"""Settings → Her browser and the in-app view (the her browser spec, decisions 11-12, 17).

Behind the session token like every /api/ route, and `/ws/browser` behind the same
door as every socket (`app.admit`): Host, Origin, then a hello with the token. It is a
socket of its own, not the conversation's, so the live picture never travels where a
turn's messages do.

Nothing here can approve anything but a card the view showed, and only by the user's
click on it: `cards/{id}/shown` first (the card is on screen), then `cards/{id}` with
the decision. A chip or a step is answered with `asks/{id}`; a chip's "always" also
turns on `open_any` (opening any public site without asking). Errors are plain words.

The Brave Search API's key goes from the page to `brave-key`, straight into the
operating system's keychain, as a model provider's key does (`api/settings.py`): it is
checked with one search first, and never written to a file, the database or a log. The
state says only whether one is saved.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from trillion.api.app import admit
from trillion.api.runtime import OWNER, Runtime
from trillion.browser import brave
from trillion.browser.browse import service_of, settings_of
from trillion.browser.runtime import (
    APPROX_MB,
    CHROMIUM_VERSION,
    NotInstalled,
    playwright_available,
)
from trillion.browser.settings import ENGINES, PREF
from trillion.browser.view import give_input
from trillion.secrets.store import SecretStoreError

logger = logging.getLogger(__name__)

_ID = r"^[a-f0-9]{8,32}$"
FREE = "free"
ABOUT = (
    "Her own browser, separate from yours, running in the background. It can't see your "
    "files, and it's signed in only where you signed it in. It isn't a virtual machine yet."
)


class Answer(BaseModel):
    # "always": a chip's "Always allow", which also turns on opening any site.
    answer: Literal["allowed", "always", "declined", "done"]


class Decision(BaseModel):
    decision: Literal["confirm", "cancel"]


class Control(BaseModel):
    take: bool


class BraveKey(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: str = Field(min_length=1, max_length=brave.KEY_MAX)


# What a key Brave didn't take says. A key at its limit is saved, and says so.
_KEY_WORDS = {
    "key_refused": "Brave didn't accept that key. Check it and try again",
    "unreachable": "Brave couldn't be reached to check the key. Try again in a moment",
    "unavailable": "Brave didn't answer as expected, so the key wasn't saved. Try again",
}


class Goto(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


def sentence(text: str) -> str:
    """A reason as the user reads it: a sentence, capital first and a full stop last.
    The core's reasons are written for the model, in lower case and of "its network"."""
    text = text.strip().replace("this machine or its network", "this computer or your network")
    text = text.replace("this computer or its network", "this computer or your network")
    if not text:
        return "That didn't work. Try again in a moment."
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "?", "!")) else text + "."


def _fail(error: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": sentence(error)}, status_code=status)


def create_browser_router(rt: Runtime) -> APIRouter:
    router = APIRouter()
    installing: dict[str, Any] = {"task": None, "error": None}

    async def state() -> dict[str, Any]:
        svc = service_of(rt)
        settings = await settings_of(rt)
        installed = svc.runtime.install.state() if svc is not None else None
        task = installing["task"]
        return {
            "about": ABOUT,
            "available": svc is not None and playwright_available(),
            "installed": installed is not None,
            "version": installed.version if installed is not None else CHROMIUM_VERSION,
            "size_mb": APPROX_MB,
            "installing": task is not None and not task.done(),
            "install_error": installing["error"],
            "settings": settings.to_json(),
            "engines": [{"id": k, "name": v[0]} for k, v in ENGINES.items()],
            # Whether a Brave Search API key is saved: never the key, nor how it ends.
            "brave": {
                "engine": brave.ENGINE,
                "saved": bool(rt.secrets.get(brave.SECRET)),
                "keychain": rt.secrets.available(),
                "key_link": brave.KEY_LINK,
            },
            "sites": svc.signins.sites() if svc is not None else [],
            "tasks": svc.listing() if svc is not None else [],
            "free": svc.free_json() if svc is not None else None,
        }

    @router.get("/api/browser")
    async def get_state() -> Any:
        return await state()

    @router.put("/api/browser/settings")
    async def put_settings(body: dict[str, Any]) -> Any:
        settings = (await settings_of(rt)).changed(body)
        await rt.preferences.put(OWNER, PREF, settings.to_json(), rt.clock())
        return await state()

    @router.put("/api/browser/brave-key")
    async def save_brave_key(body: BraveKey) -> Any:
        """Check the key with one search, then keep it in the keychain. Only the kind of
        a failure is ever logged, never the key or Brave's reply."""
        if not rt.secrets.available():
            return _fail("this computer has no keychain to keep the key in", 409)
        svc = service_of(rt)
        why = await brave.check(
            body.key, transport=svc.search_transport if svc is not None else None
        )
        if why is not None and why != "limit_reached":
            logger.info("brave search key not saved: %s", why)
            return _fail(_KEY_WORDS.get(why, _KEY_WORDS["unavailable"]))
        try:
            rt.secrets.set(brave.SECRET, body.key)
        except SecretStoreError:
            return _fail("the keychain refused to keep the key", 500)
        logger.info("brave search key saved")
        out = await state()
        if why == "limit_reached":
            out["note"] = sentence(
                "The key is saved, but Brave's monthly limit is reached: searches use Bing "
                "until it resets"
            )
        return out

    @router.delete("/api/browser/brave-key")
    async def forget_brave_key() -> Any:
        """Remove the key from the keychain at once (SEC-7)."""
        try:
            rt.secrets.delete(brave.SECRET)
        except SecretStoreError:
            return _fail("the keychain refused to remove the key", 500)
        logger.info("brave search key removed")
        return await state()

    @router.post("/api/browser/install")
    async def install() -> Any:
        svc = service_of(rt)
        if svc is None or not playwright_available():
            return _fail("her browser needs the browser extra: uv sync --extra browser", 409)
        if installing["task"] is not None and not installing["task"].done():
            return await state()

        async def fetch() -> None:
            installing["error"] = None
            try:
                await svc.runtime.install.fetch()
            except NotInstalled as e:
                installing["error"] = str(e)
            except Exception as e:
                logger.warning("fetching her browser failed: %s", type(e).__name__)
                installing["error"] = "fetching her browser failed; try again"

        installing["task"] = asyncio.ensure_future(fetch())
        return await state()

    @router.delete("/api/browser/sites/{site}")
    async def forget(site: str) -> Any:
        svc = service_of(rt)
        if svc is None or not svc.signins.remove(site):
            return _fail("no such site", 404)
        return await state()

    @router.post("/api/browser/tasks/{task_id}/asks/{ask_id}")
    async def answer(task_id: str, ask_id: str, body: Answer) -> Any:
        svc = service_of(rt)
        if svc is None or not await svc.answer(task_id, ask_id, body.answer):
            return _fail("that isn't waiting any more", 409)
        if body.answer == "always":
            # From now on she opens any public site without asking, as if switched on
            # in Settings → Her browser, where it can be switched off again.
            settings = (await settings_of(rt)).changed({"open_any": True})
            await rt.preferences.put(OWNER, PREF, settings.to_json(), rt.clock())
        return await state()

    @router.post("/api/browser/tasks/{task_id}/cards/{pending_id}/shown")
    async def shown(task_id: str, pending_id: str) -> Any:
        svc = service_of(rt)
        if svc is None or not await svc.shown(task_id, pending_id):
            return _fail("that card isn't waiting any more", 409)
        return {"ok": True}

    @router.post("/api/browser/tasks/{task_id}/cards/{pending_id}")
    async def tap(task_id: str, pending_id: str, body: Decision) -> Any:
        svc = service_of(rt)
        response = await svc.tap(task_id, pending_id, body.decision) if svc is not None else None
        if response is None:
            return _fail("that card isn't waiting any more", 409)
        return {"status": response.status, **(await state())}

    @router.get("/api/browser/tasks/{task_id}/card")
    async def card(task_id: str) -> Any:
        svc = service_of(rt)
        found = await svc.card(task_id) if svc is not None else None
        if found is None:
            return _fail("no card is waiting", 404)
        return found

    @router.post("/api/browser/tasks/{task_id}/control")
    async def control(task_id: str, body: Control) -> Any:
        svc = service_of(rt)
        if svc is None:
            return _fail("her browser isn't running", 409)
        done = await (svc.take_control(task_id) if body.take else svc.give_back(task_id))
        if not done:
            return _fail("that task isn't running any more", 409)
        return await state()

    @router.post("/api/browser/tasks/{task_id}/stop")
    async def stop(task_id: str) -> Any:
        svc = service_of(rt)
        if svc is None or not await svc.stop(task_id):
            return _fail("that task isn't running any more", 409)
        return await state()

    @router.post("/api/browser/tasks/{task_id}/keep")
    async def keep(task_id: str) -> Any:
        svc = service_of(rt)
        site = await svc.keep_signin(task_id) if svc is not None else None
        if site is None:
            return _fail("there's no site here to stay signed in to", 409)
        return {"site": site, **(await state())}

    @router.get("/api/browser/tasks/{task_id}/log")
    async def log(task_id: str) -> Any:
        svc = service_of(rt)
        if svc is None or svc.log is None:
            return {"rows": []}
        rows = await svc.log.for_task(OWNER, task_id, rt.clock())
        return {
            "rows": [
                {"at_ms": r.at, "event": r.event, "origin": r.origin, "detail": r.detail}
                for r in rows
            ]
        }

    @router.post("/api/browser/free")
    async def open_free() -> Any:
        svc = service_of(rt)
        if svc is None:
            return _fail("her browser isn't set up yet", 409)
        try:
            await svc.open_free()
        except NotInstalled as e:
            return _fail(str(e), 409)
        except Exception as e:
            logger.warning("her browser didn't open: %s", type(e).__name__)
            return _fail("her browser didn't open; try again", 500)
        return await state()

    @router.delete("/api/browser/free")
    async def close_free() -> Any:
        svc = service_of(rt)
        if svc is not None:
            await svc.close_free()
        return await state()

    @router.post("/api/browser/free/goto")
    async def goto(body: Goto) -> Any:
        svc = service_of(rt)
        if svc is None:
            return _fail("her browser isn't open", 409)
        why = await svc.goto_free(body.url)
        if why is not None:
            return _fail(why, 400)
        return await state()

    @router.websocket("/ws/browser")
    async def watch(socket: WebSocket) -> None:
        if await admit(socket, rt) is None:
            return
        svc = service_of(rt)

        async def send(body: dict[str, Any]) -> None:
            await socket.send_text(json.dumps(body, ensure_ascii=False))

        cast: Any = None
        try:
            await send({"type": "browser.ready"})
            while True:
                frame = await socket.receive()
                if frame["type"] == "websocket.disconnect":
                    break
                raw = frame.get("text")
                if not isinstance(raw, str) or len(raw) > 8192:
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict) or svc is None:
                    continue
                if msg.get("type") == "watch":
                    if cast is not None:
                        await cast.stop()
                        cast = None
                    target = svc.target(str(msg.get("task", "")))
                    if target is not None:
                        cast = await svc.watch(target, send)
                elif msg.get("type") == "input":
                    target = svc.target(str(msg.get("task", "")))
                    if target is not None:
                        await give_input(target, msg)
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass
        finally:
            if cast is not None:
                await cast.stop()
            with contextlib.suppress(RuntimeError, WebSocketDisconnect, OSError):
                await socket.close()

    return router


ROUTES = (create_browser_router,)
