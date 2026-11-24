"""A local Ollama model with too little room for her says so (the Ollama context spec):
once a launch after a reply, and in place of "the model provider failed" when it
refuses. The server reads it from Ollama; here that read is a stand-in."""

from __future__ import annotations

from typing import Any

import pytest

from tests.api.test_server import App, home, make, only
from tests.brain.fakes import say
from trillion.brain import ollama
from trillion.brain.types import ProviderError
from trillion.persona.prompt import degraded

# The server tests' fixtures, used here by name.
FIXTURES = (home, make)
OLLAMA = "http://localhost:11434/v1"


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """What Ollama would say: 4,096 tokens, and every time it was asked."""
    asked: list[tuple[str, str]] = []

    async def stand_in(base_url: str, model: str, **_: Any) -> int | None:
        asked.append((base_url, model))
        return 4096

    monkeypatch.setattr(ollama, "served_context", stand_in)
    return asked


def local(app: App, url: str = OLLAMA) -> App:
    app.provider.base_url = url  # type: ignore[union-attr]
    return app


def notices(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [m for m in msgs if m["type"] == "notice" and m.get("code") == "context"]


def test_after_a_reply_she_is_told_once_a_launch(make: Any, served: list[Any]) -> None:
    app = local(make(say("Hi."), say("Hi again.")))
    with app.connect() as chat:
        first = chat.say("hi")
        assert first[-1]["type"] == "turn.complete"
        (told,) = notices(first)
        assert told["text"] == ollama.line(4096)
    with app.connect() as chat:
        again = chat.say("hi")
        assert notices(again) == []
    # Read from Ollama once, then known for the rest of the launch.
    assert served == [(OLLAMA, app.provider.model)]  # type: ignore[union-attr]


def test_a_refusal_names_the_context_and_the_fix(make: Any, served: list[Any]) -> None:
    app = local(make(ProviderError("bad_request", "too long")))
    with app.connect() as chat:
        msgs = chat.say("hi")
    assert only(msgs, "error")["sentence"] == ollama.line(4096)


def test_a_model_with_room_enough_says_nothing(make: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    async def roomy(base_url: str, model: str, **_: Any) -> int | None:
        return 131_072

    monkeypatch.setattr(ollama, "served_context", roomy)
    app = local(make(say("Hi."), ProviderError("bad_request", "x")))
    with app.connect() as chat:
        assert notices(chat.say("hi")) == []
        assert only(chat.say("hi"), "error")["sentence"] == degraded("model", "bad_request")


def test_a_hosted_service_is_never_asked(make: Any, served: list[Any]) -> None:
    app = local(make(say("Hi."), ProviderError("bad_request", "x")), "https://openrouter.ai/api/v1")
    with app.connect() as chat:
        assert notices(chat.say("hi")) == []
        assert only(chat.say("hi"), "error")["sentence"] == degraded("model", "bad_request")
    assert served == []
