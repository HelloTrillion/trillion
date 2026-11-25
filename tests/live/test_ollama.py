"""With only a local Ollama, a full typed conversation works, memory and all (the
Phase 0 definition of done, item 5), against a real model, not a scripted one.

These skip unless `TRILLION_LIVE_MODEL` and `TRILLION_LIVE_URL` are set: CI's nightly
`ollama` job sets them, and anyone can run them for free on their own computer:

    TRILLION_LIVE_MODEL=gemma4:e4b TRILLION_LIVE_URL=http://localhost:11434/v1 \\
        uv run pytest tests/live -rs

The server is the same one the API tests drive, with the real model in place of the
scripted one; the keychain and the environment are never read. A small model is not
the same every time, so the memory checks get one more try each, and say so when
they need it. The confirmation rule gets none: it must hold every time.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

import tests.api.test_server as server
from tests.api.test_server import App, Chat, reply
from trillion.brain import config
from trillion.brain.config import ModelSettings
from trillion.secrets.store import MemoryStore

MODEL = os.environ.get("TRILLION_LIVE_MODEL", "").strip()
URL = os.environ.get("TRILLION_LIVE_URL", "").strip()
# A turn on a computer with no GPU, as CI's runner is: her first request is about
# 13,300 tokens, which four CPUs take minutes to read before the first word. The app
# itself waits up to 10 minutes for a model of the user's own (`sse.LOCAL_TIMEOUT`),
# and that is what these test: this wait, the page's, is only longer.
TURN_S = 900.0
TRIES = 2

pytestmark = pytest.mark.skipif(
    not (MODEL and URL),
    reason="set TRILLION_LIVE_MODEL and TRILLION_LIVE_URL to run against a real model",
)
logger = logging.getLogger(__name__)


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[App]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    # A real model on a CPU takes far longer than a scripted one.
    monkeypatch.setattr(server, "RECEIVE_TIMEOUT_S", TURN_S)
    provider = config.build(ModelSettings(kind="custom", model=MODEL, base_url=URL), MemoryStore())
    built = App(tmp_path, provider)
    try:
        yield built
    finally:
        built.close()


# What she did on the last turn, for a failure to show: a small model's miss is read
# from this, not guessed at.
LAST: list[str] = []


def did(msgs: list[dict[str, Any]]) -> str:
    tools = [str(m.get("name") or m.get("tool")) for m in msgs if m["type"] == "tool.start"]
    said = " ".join(reply(msgs).split())
    usage = next((m.get("usage") for m in msgs if m["type"] == "turn.complete"), None)
    return f"tools={tools} usage={usage} said={said[:300]!r}"


def turn(chat: Chat, text: str) -> list[dict[str, Any]]:
    msgs = chat.say(text)
    LAST[:] = [did(msgs)]
    errors = [m for m in msgs if m["type"] == "error"]
    assert not errors, errors
    assert msgs[-1]["type"] == "turn.complete", msgs[-1]
    return msgs


def tries(check: Callable[[], bool], what: str) -> None:
    for attempt in range(1, TRIES + 1):
        if check():
            if attempt > 1:
                logger.warning("live: %s needed try %d of %d", what, attempt, TRIES)
            return
        logger.warning("live: %s, try %d: %s", what, attempt, LAST[-1] if LAST else "-")
    pytest.fail(f"{what}: not in {TRIES} tries with {MODEL}; last turn {LAST[-1] if LAST else '-'}")


def test_a_typed_turn_gets_a_reply_and_is_stored(app: App) -> None:
    with app.connect() as chat:
        said = reply(turn(chat, "Hi! In one short sentence, how are you?"))
    assert said.strip(), "she said nothing"
    kinds = [r[0] for r in app.rows("SELECT kind FROM messages ORDER BY id")]
    assert kinds[0] == "user" and "assistant" in kinds


def test_she_remembers_and_recalls_it_in_a_new_conversation(app: App) -> None:
    def remembered() -> bool:
        with app.connect() as chat:
            turn(chat, "Please remember this for later: my sister's name is Priya.")
        return any("priya" in m.text.lower() for m in app.rt.memory.current())

    tries(remembered, "a memory_write with the name")

    def recalled() -> bool:
        # No resume: a new conversation, so only memory can carry the name.
        with app.connect() as chat:
            said = reply(turn(chat, "What's my sister's name? Just the name."))
        return "priya" in said.lower()

    tries(recalled, "the name, in a new conversation")


def test_a_file_is_never_written_without_a_tap(app: App) -> None:
    note = app.granted / "note.txt"
    with app.connect() as chat:
        msgs = turn(
            chat,
            f"Create a file at {note} containing the word hi. Use your file tool.",
        )
        cards = [m for m in msgs if m["type"] == "confirm.request"]
        # Asked, not done: nothing is on the disk before the user answers.
        assert not note.exists()
        if cards:
            assert chat.tap("cancel", cards[-1]["pending_id"])["status"] == "cancelled"
            chat.until("turn.complete")
        else:
            logger.warning("live: %s raised no card for a file write: %s", MODEL, did(msgs))
    assert not note.exists()
