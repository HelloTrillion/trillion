"""Settings → Speaking up (the speaking up spec). Behind the session token like every
/api/ route.

The settings, with what stands behind them: when each watcher last checked and what
last went wrong (its kind), and whether this computer's mic and Focus checks work.
Recent finds with Dismiss, and each watcher's notes with Clear: the user's own taps.
"""

from __future__ import annotations

import sys
from typing import Any

from fastapi import APIRouter, Request
from fastapi import Path as Segment
from fastapi.responses import JSONResponse

from trillion.api.runtime import OWNER, Runtime
from trillion.attention.probes import Answer
from trillion.attention.quiet import ask_probe
from trillion.attention.settings import PREF, WATCHERS, SettingsError, merge
from trillion.attention.store import FindRecord
from trillion.storage.sqlite.attention_store import SqliteAttentionStore

KEY = Segment(min_length=1, max_length=300, pattern=r"^[A-Za-z0-9_.:@-]+$")
WATCHER = Segment(min_length=1, max_length=40)
BROWSERS = frozenset({"chrome", "edge", "firefox", "safari", "other"})

# Why a check can't tell here, in a few words, by system.
_MIC_WHY = {
    "darwin": "needs macOS 14.2 or later",
    "win32": "Windows did not say which apps use the microphone",
    "linux": "needs pactl or pw-dump",
}
_FOCUS_WHY = {
    "darwin": "macOS keeps Focus private from apps here",
    "win32": "Windows does not tell apps about Focus",
    "linux": "needs GNOME, or a desktop that reports Do Not Disturb",
}
_SAME_BROWSER = "a call in this same browser can't be told apart; the meeting check covers it"


def _platform() -> str:
    return "linux" if sys.platform.startswith("linux") else sys.platform


def _probe(answer: Answer, whys: dict[str, str]) -> dict[str, str]:
    if answer is None:
        return {"status": "can't tell", "why": whys.get(_platform(), "this system can't say")}
    return {"status": "works", "why": ""}


def _find(f: FindRecord) -> dict[str, Any]:
    return {
        "key": f.key,
        "watcher": f.watcher,
        "first_seen_ms": f.first_seen_ms,
        "status": f.status,
        "notice_id": f.notice_id,
        "title": f.title,
        "dismissed_ms": f.dismissed_ms,
    }


def _not_found() -> JSONResponse:
    return JSONResponse({"error": "not_found"}, status_code=404)


def create_attention_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/attention")

    def store() -> SqliteAttentionStore:
        if rt.attention_store is None:
            raise RuntimeError("the attention runner is not running")
        return rt.attention_store

    async def status() -> dict[str, Any]:
        states = {s.watcher: s for s in await store().states(OWNER)}
        watchers: dict[str, Any] = {}
        for name in WATCHERS:
            s = states.get(name)
            watchers[name] = {
                "last_check_ms": s.last_check_ms if s else None,
                "last_error": s.last_error if s else None,
                "notes": s.notes if s else "",
            }
        probes: dict[str, Any] = {}
        q = rt.quiet
        if q is not None:
            host = q.page_host()
            mic = _probe(await ask_probe("mic", q.mic, host), _MIC_WHY)
            if mic["status"] == "works" and host in BROWSERS:
                mic["note"] = _SAME_BROWSER
            probes = {"mic": mic, "focus": _probe(await ask_probe("focus", q.focus), _FOCUS_WHY)}
        return {"watchers": watchers, "probes": probes}

    async def described() -> dict[str, Any]:
        return {"settings": rt.attention_settings.to_json(), "status": await status()}

    @router.get("/settings")
    async def read() -> dict[str, Any]:
        return await described()

    @router.put("/settings")
    async def save(request: Request) -> Any:
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "bad_request"}, status_code=400)
        try:
            chosen = merge(rt.attention_settings, body)
        except SettingsError as e:
            return JSONResponse({"error": "bad_setting", "why": str(e)}, status_code=422)
        await rt.preferences.put(OWNER, PREF, chosen.to_json(), rt.clock())
        rt.attention_settings = chosen
        return await described()

    async def finds() -> dict[str, Any]:
        return {"finds": [_find(f) for f in await store().recent(OWNER, rt.clock())]}

    @router.get("/finds")
    async def listing() -> dict[str, Any]:
        return await finds()

    @router.post("/finds/{key}/dismiss")
    async def dismiss(key: str = KEY) -> Any:
        if not await store().dismiss(OWNER, key, rt.clock()):
            return _not_found()
        return await finds()

    @router.delete("/notes/{watcher}")
    async def clear_notes(watcher: str = WATCHER) -> Any:
        if watcher not in WATCHERS:
            return _not_found()
        await store().clear_notes(OWNER, watcher)
        return await described()

    return router
