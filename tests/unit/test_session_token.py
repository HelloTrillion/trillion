"""The session token: the desktop app's when it hands one over, else a fresh one (D2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trillion.api.runtime import (
    SESSION_TOKEN_ENV,
    SessionTokenError,
    open_runtime,
    session_token,
)
from trillion.secrets.store import MemoryStore

GOOD = "A" * 20 + "b_-" + "9" * 20  # 43 URL-safe characters, as token_urlsafe(32) makes
REFUSAL = "TRILLION_SESSION_TOKEN must be at least 32 URL-safe characters"


def test_the_apps_token_is_used() -> None:
    assert len(GOOD) == 43
    assert session_token({SESSION_TOKEN_ENV: GOOD}) == GOOD


@pytest.mark.parametrize("bad", ["a" * 10, "a" * 40 + "/", "a" * 20 + " " + "a" * 20, ""])
def test_a_short_or_malformed_token_is_refused(bad: str) -> None:
    with pytest.raises(SessionTokenError) as e:
        session_token({SESSION_TOKEN_ENV: bad})
    assert str(e.value) == REFUSAL


def test_unset_makes_a_fresh_one_each_time() -> None:
    a, b = session_token({}), session_token({})
    assert a != b
    assert len(a) >= 43


async def test_the_runtime_holds_the_apps_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    rt = await open_runtime(persona=tmp_path, secrets=MemoryStore(), env={SESSION_TOKEN_ENV: GOOD})
    try:
        assert rt.token == GOOD
    finally:
        await rt.close()


async def test_the_runtime_makes_its_own_without_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(SESSION_TOKEN_ENV, GOOD)  # the real environment is not read
    rt = await open_runtime(persona=tmp_path, secrets=MemoryStore(), env={})
    try:
        assert rt.token != GOOD
        assert len(rt.token) >= 43
    finally:
        await rt.close()
