"""Settings → Connected tools, for Gmail, Google Calendar and Google Drive (CON-4).

Behind the session token like every /api/ route, and never a tool: only the person
at the page can connect an account or let a tool run without asking. No token, code
or client secret is ever sent back.

`/google/callback` is where Google sends the browser after signing in. It needs no
token, and it can only hand a code to a sign-in already waiting for that exact
random state.
"""

from __future__ import annotations

import html
import json
import secrets
from importlib.resources import files
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from trillion.api.mcp import _page, show_link
from trillion.api.runtime import Runtime
from trillion.connectors.google import config
from trillion.connectors.google.config import GoogleError
from trillion.connectors.google.connection import GoogleConnection
from trillion.secrets.store import SecretStoreError

ServiceName = Literal["gmail", "calendar", "drive"]


def _fail(status: int, error: str, detail: str = "") -> JSONResponse:
    return JSONResponse({"error": error, "detail": detail}, status_code=status)


class ClientIn(BaseModel):
    # Either the two values, or the JSON file Google offers to download.
    client_id: str = Field(default="", max_length=300)
    client_secret: str = Field(default="", max_length=300)
    file: str = Field(default="", max_length=10_000)
    # The browser key for Google's file picker (Drive), if it comes now.
    picker_key: str = Field(default="", max_length=100)


class PickerKey(BaseModel):
    key: str = Field(max_length=100)


class Account(BaseModel):
    email: str = Field(max_length=254)


class Picked(BaseModel):
    ticket: str = Field(max_length=100)
    files: list[dict[str, str]] = Field(max_length=200)


class ForgetFile(BaseModel):
    email: str = Field(max_length=254)
    id: str = Field(max_length=200)


class SignIn(BaseModel):
    service: ServiceName
    hint: str = Field(default="", max_length=254)


class Choices(BaseModel):
    tools: dict[str, Literal["read", "ask"]]


class Remove(BaseModel):
    email: str = Field(max_length=254)
    service: ServiceName | None = None


def create_google_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    def google() -> GoogleConnection:
        if rt.google is None:
            raise RuntimeError("Google is not running")
        return rt.google

    @router.get("/api/google")
    async def read() -> dict[str, Any]:
        return google().describe()

    @router.put("/api/google/client")
    async def save_client(body: ClientIn) -> Any:
        if not rt.secrets.available():
            return _fail(409, "no_keychain", "this computer has no keychain to keep it in")
        try:
            if body.file.strip() or body.client_id.strip():
                if body.file.strip():
                    cid, secret = config.read_client_file(body.file)
                else:
                    cid, secret = config.check_client(body.client_id, body.client_secret)
                config.save_client(rt.secrets, cid, secret)
            if body.picker_key.strip():
                config.save_picker_key(rt.secrets, body.picker_key)
        except GoogleError as e:
            return _fail(400, "invalid", str(e))
        except SecretStoreError:
            return _fail(500, "keychain_refused", "the keychain would not save it")
        return google().describe()

    @router.put("/api/google/picker-key")
    async def save_picker_key(body: PickerKey) -> Any:
        try:
            config.save_picker_key(rt.secrets, body.key)
        except GoogleError as e:
            return _fail(400, "invalid", str(e))
        except SecretStoreError:
            return _fail(500, "keychain_refused", "the keychain would not save it")
        return google().describe()

    @router.post("/api/google/client/forget")
    async def forget_client() -> Any:
        try:
            rt.secrets.delete(config.CLIENT_KEY)
        except SecretStoreError:
            return _fail(500, "keychain_refused", "the keychain would not remove it")
        rt.helpers_changed()  # a watcher on Google may need connecting now
        return google().describe()

    @router.post("/api/google/picker")
    async def picker(body: Account) -> Any:
        try:
            ticket = google().picker_ticket(body.email)
        except GoogleError as e:
            return _fail(409, "not_ready", str(e))
        return {"url": f"/google/picker?ticket={ticket}"}

    @router.post("/api/google/files/forget")
    async def forget_file(body: ForgetFile) -> Any:
        google().forget_file(body.email, body.id)
        return google().describe()

    @router.get("/google/picker")
    async def picker_page(ticket: str = "") -> HTMLResponse:
        """Google's file picker, on a page of its own: it loads Google's script, so it
        gets its own policy, and it holds only a Drive token for this one choice."""
        found = await google().picker(ticket) if ticket else None
        if found is None:
            return _page(
                "This file picker has expired",
                "Go back to Trillion and choose Choose files again.",
                status=400,
            )
        nonce = secrets.token_urlsafe(16)
        data = json.dumps({**found, "ticket": ticket}).replace("<", "\\u003c")
        body = (
            _picker_page()
            .replace("{nonce}", nonce)
            .replace("{data}", data)
            .replace("{email}", html.escape(found["email"]))
        )
        csp = (
            "default-src 'none'; "
            f"script-src 'nonce-{nonce}' https://apis.google.com https://www.gstatic.com; "
            "frame-src https://docs.google.com https://drive.google.com"
            " https://accounts.google.com https://content.googleapis.com; "
            "connect-src 'self' https://*.googleapis.com https://*.google.com; "
            "img-src https: data:; style-src 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; base-uri 'none'; form-action 'none'"
        )
        return HTMLResponse(
            body,
            headers={
                "Content-Security-Policy": csp,
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
            },
        )

    @router.post("/google/picked")
    async def picked(body: Picked) -> Any:
        try:
            count = google().picked(body.ticket, body.files)
        except GoogleError as e:
            return _fail(409, "expired", str(e))
        return {"picked": count}

    @router.post("/api/google/sign-in")
    async def sign_in(body: SignIn) -> Any:
        try:
            attempt = google().sign_in(body.service, body.hint)
        except GoogleError as e:
            return _fail(409, "not_ready", str(e))
        return {"url": attempt.url}

    @router.patch("/api/google/tools")
    async def choose(body: Choices) -> Any:
        try:
            google().set_free(body.tools)
        except GoogleError as e:
            return _fail(400, "always_asks", str(e))
        return google().describe()

    @router.post("/api/google/remove")
    async def remove(body: Remove) -> Any:
        try:
            await google().remove(body.email, body.service)
        except SecretStoreError:
            return _fail(500, "keychain_refused", "the keychain would not remove the sign-in")
        rt.helpers_changed()  # a watcher on Google may need connecting now
        return google().describe()

    @router.get("/google/callback")
    async def callback(request: Request) -> HTMLResponse:
        params = {k: v for k, v in request.query_params.items() if isinstance(v, str)}
        found = rt.google.sign_ins.arrive(params) if rt.google is not None else None
        rt.helpers_changed()  # signed in: a watcher's Connect step may clear
        if found is None:
            return _page(
                "This sign-in has expired",
                "Go back to Trillion and choose Sign in with Google again.",
                status=400,
            )
        title = config.TITLES.get(found.split(":", 1)[-1], "Google")  # type: ignore[call-overload]
        back = show_link(found)  # "google:gmail", the card in Settings
        if params.get("code"):
            return _page(
                f"Signed in to {title}",
                "It is connected. This tab closes by itself in a moment.",
                back=back,
                close=True,
            )
        return _page(f"{title} was not connected", "You can try again from Settings.", back=back)

    return router


def _picker_page() -> str:
    return (files("trillion.api") / "google_picker.html").read_text(encoding="utf-8")
