"""The airplane test (PRD §4, TESTING.md §6): no network at all.

Every connection off this machine is refused. Trillion still starts, serves its
page, opens settings, reads and writes memory, keeps rules, runs `doctor`, and
holds a whole conversation with a model on this computer. Only the hosted
providers fail, and each one fails fast, with her sentence for it, never a hang.
"""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

import asyncio
import socket
import time
from typing import Any

import pytest

from tests.api.test_server import App, home, make, only, reply  # noqa: F401
from tests.brain.fakes import say, use
from trillion.brain.providers.anthropic import AnthropicProvider
from trillion.cli import doctor
from trillion.persona.prompt import degraded
from trillion.secrets.store import MemoryStore
from trillion.voice.catalog import listen_option, speech_option

KEY = "sk-" + "test-" * 6 + "abcd"  # built from pieces: never a real credential
LOCAL = frozenset({"localhost", "127.0.0.1", "::1", "testserver"})
# Far more than any refused connection needs; a hang would blow straight through it.
FAST_S = 10.0


@pytest.fixture(autouse=True)
def airplane(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Refuse to look up any host that is not this machine, as a dead network does."""
    asked: list[str] = []
    real = socket.getaddrinfo

    def offline(host: Any, *args: Any, **kw: Any) -> Any:
        name = host.decode() if isinstance(host, bytes) else str(host)
        if name not in LOCAL:
            asked.append(name)
            raise socket.gaierror(socket.EAI_NONAME, "the network is off")
        return real(host, *args, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", offline)
    return asked


def timed(work: Any) -> tuple[Any, float]:
    start = time.monotonic()
    out = work()
    return out, time.monotonic() - start


def test_everything_on_this_machine_still_works(make: Any, airplane: list[str]) -> None:
    app = make(
        use("memory_write", {"text": "Sam takes coffee black.", "moves": False}),
        say("Noted."),
        use("memory_recall", {"query": "coffee"}),
        say("Black."),
    )
    assert app.client.get("/").status_code == 200
    for part in ("model", "speech", "listening", "persona"):
        assert app.client.get(f"/api/settings/{part}", headers=app.auth).status_code == 200
    with app.connect() as chat:
        assert reply(chat.say("I take my coffee black")) == "Noted."
        assert reply(chat.say("how do I take my coffee?")) == "Black."
    recalled = str(app.provider.calls[-1][1][-1].blocks)
    assert "Sam takes coffee black." in recalled  # read back from disk, offline
    assert app.rt.memory.count() == 1
    assert airplane == []  # and nothing tried to leave the machine


def test_doctor_runs_offline_and_does_not_hang_when_not_told(
    home: Any, airplane: list[str]
) -> None:
    from trillion.brain.config import ANTHROPIC_KEY

    hosted = {ANTHROPIC_KEY: KEY}  # a model it would have to reach over the network
    for offline in (True, False):
        found, took = timed(
            lambda o=offline: asyncio.run(
                doctor.examine(doctor.Options(port=0, offline=o), secrets=MemoryStore(), env=hosted)
            )
        )
        checks = {c.name: c for c in found}
        assert took < FAST_S and {"Database", "Model", "Memory"} <= set(checks)
        if offline:
            assert checks["Model"].detail.endswith("not tested: offline")
        else:
            assert (
                checks["Model"].status == "fail"
                and "could not be reached" in checks["Model"].detail
            )
    assert airplane  # the second pass really did try the network


def test_a_hosted_model_fails_fast_with_her_sentence(make: Any, airplane: list[str]) -> None:
    app = make()
    app.rt.provider = AnthropicProvider(api_key=lambda: KEY, model="claude-test")
    with app.connect() as chat:
        msgs, took = timed(lambda: chat.say("hello?"))
    error = only(msgs, "error")
    assert (error["code"], error["kind"]) == ("provider", "unreachable")
    assert error["sentence"] == degraded("model", "unreachable")
    assert took < FAST_S and airplane  # it tried, and gave up quickly
    assert KEY not in str(msgs)


def test_a_hosted_voice_says_why_and_the_reply_stays_as_text(
    make: Any, airplane: list[str]
) -> None:
    from trillion.api.runtime import SpeechSettings

    app = make(say("Roses like sun."))
    secrets = MemoryStore()
    elevenlabs = speech_option("elevenlabs")
    assert elevenlabs is not None
    secrets.set(elevenlabs.secret, KEY)
    app.rt.tts = elevenlabs.build(secrets, None)
    app.rt.speech = SpeechSettings(kind="elevenlabs", voice="v1")
    with app.connect() as chat:
        chat.ws.send_json({"type": "turn.text", "text": "roses?"})
        msgs, took = timed(lambda: chat.until("turn.complete"))
        later = chat.until("error") if not [m for m in msgs if m["type"] == "error"] else []
    errors = [m for m in msgs + later if m["type"] == "error"]
    assert reply(msgs) == "Roses like sun."
    assert errors[0]["code"] == "speech" and errors[0]["kind"] == "unreachable"
    assert errors[0]["sentence"] == degraded("voice", "unreachable")
    assert took < FAST_S


def test_hosted_ears_say_why_and_typing_still_works(make: Any, airplane: list[str]) -> None:
    from trillion.api.runtime import ListenSettings

    app = make(say("Typed is fine."))
    secrets = MemoryStore()
    deepgram = listen_option("deepgram")
    assert deepgram is not None
    secrets.set(deepgram.secret, KEY)
    app.rt.stt = deepgram.build(secrets)
    app.rt.listening = ListenSettings(kind="deepgram", choice="nova-3")
    with app.connect() as chat:
        chat.ws.send_json({"type": "listen.start"})
        msgs, took = timed(lambda: chat.until("error"))
        assert msgs[-1]["sentence"] == degraded("hearing", "unreachable")
        assert took < FAST_S
        assert reply(chat.say("and typed?")) == "Typed is fine."
