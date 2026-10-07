"""Settings the user makes in the app: which model, and its key (SEC-1, SEC-7, SEC-8).

This is a privileged path. It is reachable only with the session token, from
the app's own origin, and never by the model: no tool can read or write here.
A key travels from the page to this process on the loopback interface, goes
into the operating system's keychain, and is never returned, stored in the
database, or logged. The app only ever shows that a key exists and how it ends.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from trillion.api.runtime import MODEL_PREF, OWNER, Runtime
from trillion.brain import config
from trillion.brain.config import NEEDS_KEY, TAKES_KEY, ConfigError, Kind, ModelSettings
from trillion.brain.types import ProviderError
from trillion.secrets.store import SecretStoreError

logger = logging.getLogger(__name__)

_KEY_LIMIT = 512


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Kind
    base_url: str | None = Field(default=None, max_length=2048)
    # A new key, or None to use the one already in the keychain.
    api_key: str | None = Field(default=None, min_length=1, max_length=_KEY_LIMIT)


class Probe(_Body):
    pass


class Save(_Body):
    model: str = Field(min_length=1, max_length=200)


class Forget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Kind


def _fail(status: int, code: str) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


def _describe(rt: Runtime) -> dict[str, Any]:
    """What the page may know. Never a key: only whether one exists and its last four."""
    keys: dict[str, Any] = {}
    for kind in sorted(TAKES_KEY):
        value = rt.secrets.get(config.secret_name(kind))
        # The ending is shown only for a key long enough that four characters reveal little.
        keys[kind] = (
            {"saved": True, "ends": value[-4:] if len(value) >= 20 else ""} if value else None
        )
    p, s = rt.provider, rt.settings
    return {
        "source": rt.source,
        "active": {"provider": p.name, "model": p.model} if p is not None else None,
        "saved": {"kind": s.kind, "model": s.model, "base_url": s.base_url} if s else None,
        "keys": keys,
        "keychain": rt.secrets.available(),
        "defaults": {"local": config.LOCAL_URL},
    }


def create_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/settings")

    def key_for(body: _Body) -> str | None:
        if body.kind not in TAKES_KEY:
            return None
        return body.api_key or rt.secrets.get(config.secret_name(body.kind))

    async def probe(body: _Body) -> list[dict[str, Any]] | JSONResponse:
        key = key_for(body)
        if body.kind in NEEDS_KEY and not key:
            return _fail(400, "key_required")
        try:
            found = await config.list_models(body.kind, body.base_url, key)
        except ConfigError as e:
            return _fail(400, e.code)
        except ProviderError as e:
            # The kind only: a provider's error text can echo what it was sent.
            logger.info("model list for %s failed: %s", body.kind, e.kind)
            return _fail(502, e.kind)
        return [{"id": m.id, "label": m.label, "max_output": m.max_output} for m in found]

    @router.get("/model")
    async def read() -> dict[str, Any]:
        return _describe(rt)

    @router.post("/model/models")
    async def models(body: Probe) -> Any:
        found = await probe(body)
        return found if isinstance(found, JSONResponse) else {"models": found}

    @router.put("/model")
    async def save(body: Save) -> Any:
        if rt.source == "env":
            return _fail(409, "set_by_environment")
        if body.api_key and not rt.secrets.available():
            return _fail(409, "no_keychain")
        # Prove the settings work before anything is stored: a typo in a key is
        # caught here, by a call that costs nothing.
        found = await probe(body)
        if isinstance(found, JSONResponse):
            return found
        chosen = next((m for m in found if m["id"] == body.model), None)
        if chosen is None:
            return _fail(400, "unknown_model")
        try:
            base_url = config.endpoint(body.kind, body.base_url) if body.kind != "openai" else None
            if body.api_key:
                rt.secrets.set(config.secret_name(body.kind), body.api_key)
        except ConfigError as e:
            return _fail(400, e.code)
        except SecretStoreError:
            return _fail(500, "keychain_refused")
        settings = ModelSettings(
            kind=body.kind, model=body.model, base_url=base_url, max_output=chosen["max_output"]
        )
        await rt.preferences.put(OWNER, MODEL_PREF, settings.to_json(), rt.clock())
        await rt.use_model(settings)
        logger.info("model settings saved: %s", body.kind)
        return _describe(rt)

    @router.post("/model/forget-key")
    async def forget(body: Forget) -> Any:
        """Remove a key from the keychain at once (SEC-7)."""
        try:
            rt.secrets.delete(config.secret_name(body.kind))
        except SecretStoreError:
            return _fail(500, "keychain_refused")
        if rt.source != "env":
            await rt.use_model(rt.settings)
        logger.info("key removed: %s", body.kind)
        return _describe(rt)

    return router
