"""Settings → Connected tools, for Slack (her tools as the user) and for talking to
her from Slack and Telegram (SUR-5).

Behind the session token like every /api/ route, and never a tool. A token is
checked with Slack or Telegram before it is kept, kept only in the keychain, and
never sent back.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from trillion.api.runtime import Runtime
from trillion.channels.manager import Channels, ChannelSetupError
from trillion.connectors.slack.connection import SlackConnection, SlackSetupError
from trillion.secrets.store import SecretStoreError

Platform = Literal["slack", "telegram"]


def _fail(status: int, error: str, detail: str = "") -> JSONResponse:
    return JSONResponse({"error": error, "detail": detail}, status_code=status)


class UserToken(BaseModel):
    token: str = Field(max_length=300)


class Choices(BaseModel):
    tools: dict[str, Literal["read", "ask"]]


class SlackChat(BaseModel):
    bot_token: str = Field(max_length=300)
    app_token: str = Field(max_length=300)


class TelegramChat(BaseModel):
    token: str = Field(max_length=200)


class Person(BaseModel):
    platform: Platform
    id: str = Field(max_length=64)


class Turn(BaseModel):
    platform: Platform
    on: bool


class Forget(BaseModel):
    platform: Platform


def create_slack_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    def slack() -> SlackConnection:
        if rt.slack is None:
            raise RuntimeError("Slack is not running")
        return rt.slack

    def channels() -> Channels:
        if rt.channels is None:
            raise RuntimeError("channels are not running")
        return rt.channels

    async def state() -> dict[str, Any]:
        await slack().check()
        return {"slack": slack().describe(), "channels": channels().describe()}

    def keychain() -> JSONResponse | None:
        if not rt.secrets.available():
            return _fail(409, "no_keychain", "this computer has no keychain to keep it in")
        return None

    @router.get("/api/slack")
    async def read() -> dict[str, Any]:
        return await state()

    @router.put("/api/slack/user")
    async def connect(body: UserToken) -> Any:
        if (refused := keychain()) is not None:
            return refused
        try:
            await slack().connect(body.token)
        except SlackSetupError as e:
            return _fail(400, "invalid", str(e))
        except SecretStoreError:
            return _fail(500, "keychain_refused", "the keychain would not save it")
        return await state()

    @router.patch("/api/slack/tools")
    async def choose(body: Choices) -> Any:
        try:
            slack().set_free(body.tools)
        except SlackSetupError as e:
            return _fail(400, "always_asks", str(e))
        return await state()

    @router.post("/api/slack/user/forget")
    async def disconnect() -> Any:
        slack().disconnect()
        return await state()

    @router.put("/api/channels/slack")
    async def slack_chat(body: SlackChat) -> Any:
        if (refused := keychain()) is not None:
            return refused
        try:
            await channels().set_slack(body.bot_token, body.app_token)
        except ChannelSetupError as e:
            return _fail(400, "invalid", str(e))
        except SecretStoreError:
            return _fail(500, "keychain_refused", "the keychain would not save it")
        return await state()

    @router.put("/api/channels/telegram")
    async def telegram_chat(body: TelegramChat) -> Any:
        if (refused := keychain()) is not None:
            return refused
        try:
            await channels().set_telegram(body.token)
        except ChannelSetupError as e:
            return _fail(400, "invalid", str(e))
        except SecretStoreError:
            return _fail(500, "keychain_refused", "the keychain would not save it")
        return await state()

    @router.post("/api/channels/allow")
    async def allow(body: Person) -> Any:
        try:
            channels().allow(body.platform, body.id)
        except ChannelSetupError as e:
            return _fail(404, "unknown", str(e))
        return await state()

    @router.post("/api/channels/ignore")
    async def ignore(body: Person) -> Any:
        channels().ignore(body.platform, body.id)
        return await state()

    @router.post("/api/channels/disallow")
    async def disallow(body: Person) -> Any:
        channels().disallow(body.platform, body.id)
        return await state()

    @router.post("/api/channels/turn")
    async def turn(body: Turn) -> Any:
        channels().turn(body.platform, body.on)
        return await state()

    @router.post("/api/channels/forget")
    async def forget(body: Forget) -> Any:
        channels().forget(body.platform)
        return await state()

    return router
