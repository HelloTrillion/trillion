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

from trillion.api.runtime import (
    LISTEN_PREF,
    MODEL_PREF,
    OWNER,
    SPEECH_PREF,
    ListenSettings,
    Runtime,
    SpeechSettings,
)
from trillion.brain import config
from trillion.brain.config import NEEDS_KEY, TAKES_KEY, ConfigError, Kind, ModelSettings
from trillion.brain.types import ProviderError
from trillion.secrets.store import SecretStoreError
from trillion.voice.catalog import (
    LISTENING,
    SPEECH,
    ListenOption,
    SpeechOption,
    listen_option,
    speech_option,
)
from trillion.voice.types import VoiceError

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


class SpeechProbe(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: str = Field(max_length=40)
    base_url: str | None = Field(default=None, max_length=2048)
    api_key: str | None = Field(default=None, min_length=1, max_length=_KEY_LIMIT)


class SpeechSave(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # None turns speech off.
    kind: str | None = Field(default=None, max_length=40)
    voice: str = Field(default="", max_length=200)
    base_url: str | None = Field(default=None, max_length=2048)
    api_key: str | None = Field(default=None, min_length=1, max_length=_KEY_LIMIT)


class SpeechForget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(max_length=40)


class ListenProbe(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: str = Field(max_length=40)
    api_key: str | None = Field(default=None, min_length=1, max_length=_KEY_LIMIT)


class ListenSave(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # None turns listening off.
    kind: str | None = Field(default=None, max_length=40)
    choice: str = Field(default="", max_length=200)
    api_key: str | None = Field(default=None, min_length=1, max_length=_KEY_LIMIT)


class ListenPrepare(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: str = Field(max_length=40)
    choice: str = Field(max_length=200)


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
            url = config.endpoint(body.kind, body.base_url)
            if body.api_key and url is not None and config.keyless(url):
                # Said out loud rather than quietly dropped: the user believes it is in use.
                return _fail(400, "key_needs_https")
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

    # ── Speech: the same rules as the model, generated from the catalog (VOI-11a) ──

    def describe_speech() -> dict[str, Any]:
        options = []
        for o in SPEECH:
            value = rt.secrets.get(o.secret) if o.needs_key else None
            options.append(
                {
                    "kind": o.kind,
                    "title": o.title,
                    "note": o.note,
                    "where": o.where,
                    "needs_key": o.needs_key,
                    "free": o.free,
                    "key_link": o.key_link,
                    "takes_url": o.takes_url,
                    "key": {"saved": True, "ends": value[-4:] if len(value) >= 20 else ""}
                    if value
                    else None,
                }
            )
        s = rt.speech
        return {
            "options": options,
            "saved": {"kind": s.kind, "voice": s.voice, "base_url": s.base_url} if s else None,
            "active": rt.tts is not None,
            "keychain": rt.secrets.available(),
        }

    def speech_url(option: SpeechOption, base_url: str | None) -> str | None:
        """The checked address, by the rule a compatible model's follows (#33)."""
        if not option.takes_url:
            return None
        if not base_url:
            raise ConfigError("bad_url", "this voice needs an address")
        return config.endpoint("custom", base_url)

    async def voices(
        option: SpeechOption, api_key: str | None, base_url: str | None
    ) -> list[dict[str, Any]] | JSONResponse:
        if option.needs_key and not (api_key or rt.secrets.get(option.secret)):
            return _fail(400, "key_required")
        # A new key is tried from memory, so a rejected one is never stored.
        store = _Overlay(rt.secrets, {option.secret: api_key} if api_key else {})
        try:
            found = await option.build(store, speech_url(option, base_url)).voices()
        except ConfigError as e:
            return _fail(400, e.code)
        except VoiceError as e:
            logger.info("voice list for %s failed: %s", option.kind, e.kind)
            return _fail(502, e.kind)
        return [{"id": v.id, "name": v.name} for v in found]

    @router.get("/speech")
    async def read_speech() -> dict[str, Any]:
        return describe_speech()

    @router.post("/speech/voices")
    async def speech_voices(body: SpeechProbe) -> Any:
        option = speech_option(body.kind)
        if option is None:
            return _fail(400, "unknown_provider")
        found = await voices(option, body.api_key, body.base_url)
        return found if isinstance(found, JSONResponse) else {"voices": found}

    @router.put("/speech")
    async def save_speech(body: SpeechSave) -> Any:
        if body.kind is None:
            await rt.preferences.put(OWNER, SPEECH_PREF, None, rt.clock())
            await rt.use_speech(None)
            return describe_speech()
        option = speech_option(body.kind)
        if option is None:
            return _fail(400, "unknown_provider")
        if body.api_key and not rt.secrets.available():
            return _fail(409, "no_keychain")
        if option.where == "core":
            found = await voices(option, body.api_key, body.base_url)
            if isinstance(found, JSONResponse):
                return found
            if body.voice not in {v["id"] for v in found}:
                return _fail(400, "unknown_voice")
        try:
            if body.api_key:
                rt.secrets.set(option.secret, body.api_key)
        except SecretStoreError:
            return _fail(500, "keychain_refused")
        # Already checked by the voice list above, so this cannot raise here.
        chosen = SpeechSettings(
            kind=option.kind, voice=body.voice, base_url=speech_url(option, body.base_url)
        )
        await rt.preferences.put(OWNER, SPEECH_PREF, chosen.to_json(), rt.clock())
        await rt.use_speech(chosen)
        logger.info("speech settings saved: %s", option.kind)
        return describe_speech()

    @router.post("/speech/forget-key")
    async def forget_speech(body: SpeechForget) -> Any:
        option = speech_option(body.kind)
        if option is None or not option.needs_key:
            return _fail(400, "unknown_provider")
        try:
            rt.secrets.delete(option.secret)
        except SecretStoreError:
            return _fail(500, "keychain_refused")
        await rt.use_speech(rt.speech)
        logger.info("key removed: %s", option.kind)
        return describe_speech()

    # ── Listening: the same shape again, from its own catalog (VOI-11a) ──

    def describe_listening() -> dict[str, Any]:
        options = []
        for o in LISTENING:
            value = rt.secrets.get(o.secret) if o.needs_key else None
            options.append(
                {
                    "kind": o.kind,
                    "title": o.title,
                    "note": o.note,
                    "needs_key": o.needs_key,
                    "free": o.free,
                    "key_link": o.key_link,
                    "install_hint": o.install_hint,
                    "key": {"saved": True, "ends": value[-4:] if len(value) >= 20 else ""}
                    if value
                    else None,
                }
            )
        s = rt.listening
        return {
            "options": options,
            "saved": {"kind": s.kind, "choice": s.choice} if s else None,
            "active": rt.stt is not None,
            "keychain": rt.secrets.available(),
        }

    async def listen_choices(
        option: ListenOption, api_key: str | None
    ) -> list[dict[str, Any]] | JSONResponse:
        if option.needs_key and not (api_key or rt.secrets.get(option.secret)):
            return _fail(400, "key_required")
        store = _Overlay(rt.secrets, {option.secret: api_key} if api_key else {})
        try:
            found = await option.build(store).choices()
        except VoiceError as e:
            logger.info("listening check for %s failed: %s", option.kind, e.kind)
            return _fail(400 if e.kind == "not_installed" else 502, e.kind)
        return [{"id": c.id, "name": c.name, "download": c.download} for c in found]

    @router.get("/listening")
    async def read_listening() -> dict[str, Any]:
        return describe_listening()

    @router.post("/listening/choices")
    async def listening_choices(body: ListenProbe) -> Any:
        option = listen_option(body.kind)
        if option is None:
            return _fail(400, "unknown_provider")
        found = await listen_choices(option, body.api_key)
        return found if isinstance(found, JSONResponse) else {"choices": found}

    @router.post("/listening/prepare")
    async def prepare_listening(body: ListenPrepare) -> Any:
        """The one-time download a local recognizer needs. Only ever started from here,
        by the user, and every file is checked before it is kept (ONB-7)."""
        option = listen_option(body.kind)
        if option is None:
            return _fail(400, "unknown_provider")
        try:
            await option.build(rt.secrets).prepare(body.choice)
        except VoiceError as e:
            logger.info("listening setup for %s failed: %s", option.kind, e.kind)
            return _fail(400 if e.kind in ("not_installed", "unknown_choice") else 502, e.kind)
        found = await listen_choices(option, None)
        return found if isinstance(found, JSONResponse) else {"choices": found}

    @router.put("/listening")
    async def save_listening(body: ListenSave) -> Any:
        if body.kind is None:
            await rt.preferences.put(OWNER, LISTEN_PREF, None, rt.clock())
            await rt.use_listening(None)
            return describe_listening()
        option = listen_option(body.kind)
        if option is None:
            return _fail(400, "unknown_provider")
        if body.api_key and not rt.secrets.available():
            return _fail(409, "no_keychain")
        found = await listen_choices(option, body.api_key)
        if isinstance(found, JSONResponse):
            return found
        chosen = next((c for c in found if c["id"] == body.choice), None)
        if chosen is None:
            return _fail(400, "unknown_choice")
        if chosen["download"] is not None:
            return _fail(400, "needs_download")
        try:
            if body.api_key:
                rt.secrets.set(option.secret, body.api_key)
        except SecretStoreError:
            return _fail(500, "keychain_refused")
        settings = ListenSettings(kind=option.kind, choice=body.choice)
        await rt.preferences.put(OWNER, LISTEN_PREF, settings.to_json(), rt.clock())
        await rt.use_listening(settings)
        logger.info("listening settings saved: %s", option.kind)
        return describe_listening()

    @router.post("/listening/forget-key")
    async def forget_listening(body: SpeechForget) -> Any:
        option = listen_option(body.kind)
        if option is None or not option.needs_key:
            return _fail(400, "unknown_provider")
        try:
            rt.secrets.delete(option.secret)
        except SecretStoreError:
            return _fail(500, "keychain_refused")
        await rt.use_listening(rt.listening)
        logger.info("key removed: %s", option.kind)
        return describe_listening()

    return router


class _Overlay:
    """A keychain with a not-yet-saved key laid over it, for checking that key."""

    def __init__(self, base: Any, extra: dict[str, str]) -> None:
        self._base = base
        self._extra = extra

    def available(self) -> bool:
        return bool(self._base.available())

    def get(self, name: str) -> str | None:
        return self._extra.get(name) or self._base.get(name)

    def set(self, name: str, value: str) -> None:
        raise SecretStoreError("read-only")

    def delete(self, name: str) -> None:
        raise SecretStoreError("read-only")
