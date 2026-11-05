"""Settings the user makes in the app: which model, and its key (SEC-1, SEC-7, SEC-8),
and what each provider may spend (BRN-7).

This is a privileged path. It is reachable only with the session token, from
the app's own origin, and never by the model: no tool can read or write here.
A key travels from the page to this process on the loopback interface, goes
into the operating system's keychain, and is never returned, stored in the
database, or logged. The app only ever shows that a key exists and how it ends.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated, Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt

from trillion.api.runtime import (
    LISTEN_PREF,
    MODEL_PREF,
    OWNER,
    SPEECH_PREF,
    THINK_EARLY_PREF,
    WIDGETS_FRESH_PREF,
    ListenSettings,
    Runtime,
    SpeechSettings,
    caps_json,
    model_provider_id,
)
from trillion.api.usage import leg_of, provider_row
from trillion.brain import config
from trillion.brain.config import NEEDS_KEY, TAKES_KEY, ConfigError, Kind, ModelSettings
from trillion.brain.providers.anthropic import DEFAULT_MODEL
from trillion.brain.types import ProviderError
from trillion.secrets.store import SecretStoreError
from trillion.spend import metered, prices
from trillion.spend.desk import MOST, friendly
from trillion.spend.gate import DEFAULT, UNCAPPED, Caps, CapUnit, default_for, unit_default
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


class ThinkEarly(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on: StrictBool


class WidgetsFresh(BaseModel):
    model_config = ConfigDict(extra="forbid")

    widgets_fresh: StrictBool


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


# A cap: null for none, else a finite number of dollars or units, never below zero.
_Amount = Annotated[StrictFloat | StrictInt, Field(ge=0, le=1e9, allow_inf_nan=False)]


class SpendCaps(BaseModel):
    model_config = ConfigDict(extra="forbid")

    soft: _Amount | None
    hard: _Amount | None
    # Left out: kept as it is, or the default for a provider with no caps yet.
    warn_pct: Annotated[StrictInt, Field(ge=1, le=100)] | None = None
    unit: CapUnit | None = None


# A provider id that can be capped (trillion.spend.prices): a known one, or a custom
# address's host. Never "local": a model on this computer has no cap.
_PROVIDER_ID = re.compile(
    r"^(?:anthropic|openai|elevenlabs|deepgram|(?:custom|openai-voice):[a-z0-9][a-z0-9.:-]{0,252})$"
)
# Enough for every model anyone sets a price for, and no more.
_MOST_PRICES = 500
_MOST_PRICED_PROVIDERS = 50
# The highest rate a price may give, in dollars per million tokens, thousand
# characters or minute: far above any real one, so a slip of the finger is refused.
_MOST_RATE = 1e6
_RATE_KEYS = frozenset({"unit", "input", "output", "cache_read", "cache_write", "per_unit"})

# Before an address is typed it is not known whether it is on this computer.
_ANY_ADDRESS = "Free if it runs on this computer; otherwise priced by the server you point it at."


def _capped_id(provider_id: str) -> str | None:
    """None when this is a provider id that takes caps, else the error code."""
    if provider_id in UNCAPPED:
        return "never_capped"
    return None if _PROVIDER_ID.match(provider_id) else "unknown_provider"


def cost(
    rt: Runtime, provider_id: str | None, model: str | None, *, known: bool = True
) -> dict[str, Any]:
    """What a key step shows before the key is saved: roughly what it costs, where its
    prices are, and the caps it will have (those saved, else its defaults). `known` is
    False while the provider id waits on an address still to be typed."""
    caps: Caps | None = None
    if provider_id is not None and provider_id not in UNCAPPED:
        caps = rt.gate.saved(provider_id) or default_for(provider_id)
    return {
        "provider_id": provider_id,
        "name": friendly(provider_id) if provider_id else None,
        "estimate": (
            prices.cost_line(provider_id, model, rt.meter.prices.rates()) if known else _ANY_ADDRESS
        ),
        "pricing_url": prices.pricing_url(provider_id),
        "caps": caps_json(caps) if caps is not None else None,
    }


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
        "costs": _model_costs(rt),
    }


def _model_costs(rt: Runtime) -> dict[str, Any]:
    """Each kind's cost line and caps, for the model step before a key is saved. A
    model chosen before is the one priced; for Anthropic, else its default model. A
    custom address is known only once typed (the model list then says)."""
    s = rt.settings

    def chosen(kind: str) -> str | None:
        return s.model if s is not None and s.kind == kind else None

    custom = prices.provider_id("custom", s.base_url) if s and s.kind == "custom" else None
    return {
        "anthropic": cost(rt, "anthropic", chosen("anthropic") or DEFAULT_MODEL),
        "openai": cost(rt, "openai", chosen("openai")),
        "custom": (
            cost(rt, custom, chosen("custom"))
            if custom is not None
            else cost(rt, None, None, known=False)
        ),
        "local": cost(rt, "local", None),
    }


def _check_prices(raw: dict[str, Any]) -> str | None:
    """None when every price is one `parse_user_prices` keeps, for a provider that
    takes caps, in that provider's unit; else the error code. Nothing is dropped."""
    if len(raw) > _MOST_PRICED_PROVIDERS:
        return "too_many"
    count = 0
    for pid, models in raw.items():
        if (code := _capped_id(pid)) is not None:
            return code
        if not isinstance(models, dict):
            return "bad_price"
        unit = prices.unit_of(pid)
        allowed = _RATE_KEYS - {"per_unit"} if unit == "tokens" else {"unit", "per_unit"}
        for model, rate in models.items():
            count += 1
            if not model or len(model) > 200:
                return "bad_model"
            if not isinstance(rate, dict) or not set(rate) <= allowed:
                return "bad_price"
            if rate.get("unit", "tokens") != unit:
                return "bad_unit"
            parsed = prices.parse_user_prices({pid: {model: rate}})
            if not parsed:
                return "bad_price"
            (kept,) = parsed.values()
            parts = (kept.input, kept.output, kept.cache_read, kept.cache_write, kept.per_unit)
            if any(v > _MOST_RATE for v in parts):
                return "bad_price"
            if unit == "tokens" and not any(parts) and prices.can_price(pid):
                # Free would stop the table's rate, or the reported cost, counting.
                return "zero_price"
    return "too_many" if count > _MOST_PRICES else None


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
        if isinstance(found, JSONResponse):
            return found
        # What each costs, before the key is saved (ONB-9).
        pid = prices.provider_id(body.kind, body.base_url)
        user = rt.meter.prices.rates()
        for m in found:
            m["estimate"] = prices.cost_line(pid, m["id"], user)
        return {"models": found, "cost": cost(rt, pid, None)}

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
        # A provider with no caps yet starts with the defaults (BRN-7).
        await rt.seed_caps([model_provider_id(settings, "settings", rt.env)])
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
                    "cost": speech_cost(o),
                }
            )
        s, last = rt.speech, rt.speech_last
        return {
            "options": options,
            "saved": {"kind": s.kind, "voice": s.voice, "base_url": s.base_url} if s else None,
            # What was chosen before speech was switched off, to offer again.
            "last": (
                {"kind": last.kind, "voice": last.voice, "base_url": last.base_url}
                if last
                else None
            ),
            "active": rt.tts is not None,
            "keychain": rt.secrets.available(),
        }

    def speech_cost(option: SpeechOption) -> dict[str, Any]:
        """A voice's cost line and caps. One at an address is known once it is typed:
        the saved one's, else the voice list says."""
        s = rt.speech
        if option.takes_url and not (s is not None and s.kind == option.kind and s.base_url):
            return cost(rt, None, None, known=False)
        base_url = s.base_url if s is not None and s.kind == option.kind else None
        return cost(rt, prices.voice_provider_id(option.kind, base_url), None)

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
        if isinstance(found, JSONResponse):
            return found
        pid = prices.voice_provider_id(option.kind, body.base_url)
        return {"voices": found, "cost": cost(rt, pid, None)}

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
        await rt.seed_caps([prices.voice_provider_id(chosen.kind, chosen.base_url)])
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
                    "turns": o.turns,
                    "key_link": o.key_link,
                    "install_hint": o.install_hint,
                    "key": {"saved": True, "ends": value[-4:] if len(value) >= 20 else ""}
                    if value
                    else None,
                    "cost": cost(rt, prices.hearing_provider_id(o.kind), None),
                }
            )
        s, last = rt.listening, rt.listening_last
        return {
            "options": options,
            "saved": {"kind": s.kind, "choice": s.choice} if s else None,
            "last": {"kind": last.kind, "choice": last.choice} if last else None,
            "active": rt.stt is not None,
            "think_early": rt.think_early,
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

    @router.put("/listening/think-early")
    async def save_think_early(body: ThinkEarly) -> Any:
        """Whether she starts a draft while the user may still be talking (VOI-12)."""
        rt.think_early = body.on
        await rt.preferences.put(OWNER, THINK_EARLY_PREF, body.on, rt.clock())
        return describe_listening()

    # ── The board: live widgets (the live widgets spec) ──

    @router.get("/workspace")
    async def read_workspace() -> dict[str, Any]:
        return {"widgets_fresh": rt.widgets_fresh}

    @router.put("/workspace")
    async def save_workspace(body: WidgetsFresh) -> dict[str, Any]:
        """Whether live widgets refresh while the board is seen. Off stops them all."""
        rt.widgets_fresh = body.widgets_fresh
        await rt.preferences.put(OWNER, WIDGETS_FRESH_PREF, body.widgets_fresh, rt.clock())
        if rt.refresher is not None:
            # Back on: a board on a screen catches up now, not at the next tick.
            rt.refresher.wake()
        return {"widgets_fresh": rt.widgets_fresh}

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
        await rt.seed_caps([prices.hearing_provider_id(option.kind)])
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

    # ── Spend: caps and prices per provider (BRN-7) ──
    # Declared before the caps route, which would otherwise take "prices" for an id.

    @router.get("/spend/prices")
    async def read_prices() -> dict[str, Any]:
        raw = await rt.preferences.get(OWNER, metered.PREF)
        return {"prices": raw if isinstance(raw, dict) else {}}

    @router.put("/spend/prices")
    async def save_prices(body: Annotated[dict[str, Any], Body()]) -> Any:
        """The user's own prices, `{provider_id: {model: rate}}`, replacing those saved.
        A rate it would not use is refused, never quietly dropped."""
        if (code := _check_prices(body)) is not None:
            return _fail(400, code)
        await rt.preferences.put(OWNER, metered.PREF, body, rt.clock())
        await rt.meter.prices.reload()
        # A provider now priced, whose caps are still the ones in its own unit that it
        # started with, is counted in dollars from here: in dollars it is held to the
        # same $20 and $50 as any other.
        for pid in body:
            if body[pid] and rt.gate.saved(pid) == unit_default(pid):
                await rt.write_caps(pid, DEFAULT)
        logger.info("spend prices saved")
        return {"prices": body}

    async def known(provider_id: str) -> bool:
        """Set up now, given caps before, or spent on this month."""
        if rt.gate.saved(provider_id) is not None:
            return True
        spent = rt.ledger.spent(provider_id)
        if spent.dollars or spent.all_units:
            return True
        return any(pid == provider_id for _leg, pid, _m in await rt.spend_set_up())

    @router.put("/spend/{provider_id}")
    async def save_caps(provider_id: str, body: SpendCaps) -> Any:
        """One provider's caps. Null removes a cap, and is kept as the user's choice."""
        if (code := _capped_id(provider_id)) is not None:
            return _fail(400, code)
        if not await known(provider_id):
            return _fail(400, "unknown_provider")
        before = rt.gate.saved(provider_id)
        start = before or default_for(provider_id)
        unit = body.unit or start.unit
        if unit not in ("dollars", prices.unit_of(provider_id)):
            return _fail(400, "bad_unit")
        if unit == "dollars" and not prices.can_price(provider_id, rt.meter.prices.rates()):
            # Nothing could count it: a dollar cap would never be reached.
            return _fail(400, "no_price")
        soft = None if body.soft is None else float(body.soft)
        hard = None if body.hard is None else float(body.hard)
        if soft is not None and hard is not None and hard < soft:
            return _fail(400, "hard_below_soft")
        if any(v is not None and v > MOST[unit] for v in (soft, hard)):
            return _fail(400, "too_high")
        warn_pct = body.warn_pct if body.warn_pct is not None else start.warn_pct
        await rt.write_caps(provider_id, Caps(soft, hard, warn_pct, unit))
        logger.info("spend caps saved: %s", provider_id)
        model = next(
            (m for _leg, pid, m in await rt.spend_set_up() if pid == provider_id and m), None
        )
        return await provider_row(rt, leg_of(provider_id), provider_id, model)

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
