"""Settings → Speaking up (the speaking up spec). Behind the session token like every
/api/ route.

The settings, with what stands behind them: when each watcher last checked and what
last went wrong (its kind), and whether this computer's mic and Focus checks work.
Recent finds with Dismiss, and each watcher's notes with Clear: the user's own taps.

Reading the settings asks both probes, together. Saving them, or clearing notes, does
not ask again: it answers with the probes' status from the last read (asking once,
together, if there was none), so a tap on a switch never waits on the system.

A probe has three ways not to say yes or no: "can't tell" (this system can't say),
and "didn't answer" (it ran out of time, or was still busy with the last question),
which may well work next time.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from fastapi import APIRouter, Request
from fastapi import Path as Segment
from fastapi.responses import JSONResponse

from trillion.api.runtime import OWNER, Runtime
from trillion.attention.probes import Answer
from trillion.attention.settings import PREF, WATCHERS, SettingsError, merge
from trillion.attention.store import FindRecord
from trillion.storage.sqlite.attention_store import SqliteAttentionStore

KEY = Segment(min_length=1, max_length=300, pattern=r"^[A-Za-z0-9_.:@+-]+$")
WATCHER = Segment(min_length=1, max_length=40)
BROWSERS = frozenset({"chrome", "edge", "firefox", "safari", "other"})

# Why a check can't tell here, in a few words, by system.
_MIC_WHY = {
    "darwin": "needs macOS 14.2 or later",
    "win32": "Windows did not say which apps use the microphone",
    # `pactl -f json` came with PulseAudio 16; pipewire-pulse has it too.
    "linux": "needs pactl (PulseAudio 16 or later, or pipewire-pulse) or pw-dump",
}
_FOCUS_WHY = {
    "darwin": "macOS keeps Focus private from apps here",
    "win32": "Windows does not tell apps about Focus",
    "linux": "needs GNOME, or a desktop that reports Do Not Disturb",
}
_SAME_BROWSER = "a call in this same browser can't be told apart; the meeting check covers it"
_NO_ANSWER = "it took too long, or was still busy; it may answer next time"
# A find claimed this long ago and not told was lost to a stop or a failure: it is
# never told now (D5).
LOST_AFTER_MS = 5 * 60_000


def _platform() -> str:
    return "linux" if sys.platform.startswith("linux") else sys.platform


def _probe(asked: tuple[Answer, bool], whys: dict[str, str]) -> dict[str, str]:
    answer, answered = asked
    if not answered:
        return {"status": "didn't answer", "why": _NO_ANSWER}
    if answer is None:
        return {"status": "can't tell", "why": whys.get(_platform(), "this system can't say")}
    return {"status": "works", "why": ""}


def _find(f: FindRecord, now_ms: int) -> dict[str, Any]:
    lost = f.status == "claimed" and now_ms - f.first_seen_ms > LOST_AFTER_MS
    return {
        "key": f.key,
        "watcher": f.watcher,
        "first_seen_ms": f.first_seen_ms,
        "status": "lost" if lost else f.status,
        "notice_id": f.notice_id,
        "title": f.title,
        "detail": f.detail,
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

    # One change at a time: each reads what the last one saved.
    saving = asyncio.Lock()
    # The probes' status from the last read, for answers that don't ask again.
    seen: dict[str, Any] = {}

    async def probes_now() -> dict[str, Any]:
        q = rt.quiet
        if q is None:
            return {}
        mic_asked, focus_asked = await asyncio.gather(q.probe("mic"), q.probe("focus"))
        mic = _probe(mic_asked, _MIC_WHY)
        if mic["status"] == "works" and q.page_host() in BROWSERS:
            mic["note"] = _SAME_BROWSER
        found = {"mic": mic, "focus": _probe(focus_asked, _FOCUS_WHY)}
        seen.clear()
        seen.update(found)
        return found

    async def status(*, ask: bool = True) -> dict[str, Any]:
        states = {s.watcher: s for s in await store().states(OWNER)}
        watchers: dict[str, Any] = {}
        for name in WATCHERS:
            s = states.get(name)
            watchers[name] = {
                "last_check_ms": s.last_check_ms if s else None,
                "last_error": s.last_error if s else None,
                "notes": s.notes if s else "",
            }
        probes = await probes_now() if ask or not seen else dict(seen)
        return {"watchers": watchers, "probes": probes}

    async def described(*, ask: bool = True) -> dict[str, Any]:
        return {"settings": rt.attention_settings.to_json(), "status": await status(ask=ask)}

    @router.get("/settings")
    async def read() -> dict[str, Any]:
        return await described()

    @router.put("/settings")
    async def save(request: Request) -> Any:
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "bad_request"}, status_code=400)
        async with saving:
            before = rt.attention_settings
            try:
                chosen = merge(before, body)
            except SettingsError as e:
                return JSONResponse({"error": "bad_setting", "why": str(e)}, status_code=422)
            await rt.preferences.put(OWNER, PREF, chosen.to_json(), rt.clock())
            rt.attention_settings = chosen
            # Turned on again, a watcher starts from now, never from where it stopped
            # (D2): what came while it was off is not news to tell.
            for name in WATCHERS:
                if chosen.on(name) and not before.on(name) and rt.attention_store is not None:
                    await rt.attention_store.reset(OWNER, name)
        rt.helpers_changed()
        return await described(ask=False)

    async def finds() -> dict[str, Any]:
        now = rt.clock()
        return {"finds": [_find(f, now) for f in await store().recent(OWNER, now)]}

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
        return await described(ask=False)

    return router
