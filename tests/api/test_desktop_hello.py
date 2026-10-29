"""The desktop app's challenge: proof that the server on its port is the one it started (I5)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trillion.api.app import DESKTOP_HELLO, create_app
from trillion.api.runtime import Runtime, open_runtime
from trillion.secrets.store import MemoryStore

HOSTS = frozenset({"testserver"})
TOKEN = "A" * 20 + "b_-" + "9" * 20
NONCE = "n0nce-of_sixteen-plus"


def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: str | None) -> Runtime:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    return asyncio.run(
        open_runtime(persona=tmp_path, secrets=MemoryStore(), env={}, session=session)
    )


def test_the_mac_proves_the_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rt = runtime(tmp_path, monkeypatch, TOKEN)
    try:
        reply = TestClient(create_app(rt, hosts=HOSTS)).get(DESKTOP_HELLO, params={"n": NONCE})
        assert reply.status_code == 200
        want = hmac.new(TOKEN.encode(), NONCE.encode(), hashlib.sha256).hexdigest()
        assert reply.json() == {"mac": want}
        assert TOKEN not in reply.text
    finally:
        asyncio.run(rt.close())


@pytest.mark.parametrize("nonce", ["", "short", "x" * 65, "has space and more!!", "a/b" * 8])
def test_a_bad_nonce_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nonce: str
) -> None:
    rt = runtime(tmp_path, monkeypatch, TOKEN)
    try:
        reply = TestClient(create_app(rt, hosts=HOSTS)).get(DESKTOP_HELLO, params={"n": nonce})
        assert reply.status_code == 400
        assert "mac" not in reply.text
    finally:
        asyncio.run(rt.close())


def test_absent_without_the_apps_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rt = runtime(tmp_path, monkeypatch, None)
    try:
        reply = TestClient(create_app(rt, hosts=HOSTS)).get(DESKTOP_HELLO, params={"n": NONCE})
        assert reply.status_code == 404
    finally:
        asyncio.run(rt.close())


def test_a_foreign_host_is_still_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rt = runtime(tmp_path, monkeypatch, TOKEN)
    try:
        client = TestClient(create_app(rt, hosts=HOSTS))
        reply = client.get(DESKTOP_HELLO, params={"n": NONCE}, headers={"host": "evil.test"})
        assert reply.status_code == 403
    finally:
        asyncio.run(rt.close())
