"""Every paid call goes through the spend gate (BRN-7): the runtime hands out only
metered providers, nothing else calls a provider, and only the meter records usage."""

# ruff: noqa: F811 - the fixtures are shared with the server tests

from __future__ import annotations

import ast
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_server import App, home, make  # noqa: F401
from tests.brain.fakes import ScriptedProvider, say, use
from trillion.api.runtime import (
    ListenSettings,
    Runtime,
    SpeechSettings,
    open_runtime,
)
from trillion.brain.config import ANTHROPIC_KEY, MODEL, OPENAI_BASE_URL, ModelSettings
from trillion.secrets.store import MemoryStore
from trillion.spend.metered import MeteredHearing, MeteredModel, MeteredVoice

SRC = Path(__file__).resolve().parents[2] / "src" / "trillion"


@pytest.fixture
async def rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    granted = tmp_path / "granted"
    granted.mkdir()
    secrets = MemoryStore()
    for name in ("anthropic/api_key", "openai/api_key", "elevenlabs/api_key", "deepgram/api_key"):
        secrets.set(name, "test-key")
    runtime = await open_runtime(
        share_rules=lambda _p: None,
        granted_roots=(granted,),
        persona=tmp_path,
        secrets=secrets,
        env={},
    )
    yield runtime
    await runtime.close()


def handed_out(rt: Runtime) -> list[Any]:
    """Every model the runtime hands to work it starts on its own."""
    assert rt.scheduler is not None and rt.attention is not None
    assert rt.refresher is not None and rt.channels is not None
    return [
        rt.provider,
        rt.scheduler._routines._provider(),  # routines and timed asks
        rt.attention._provider(),  # the watchers
        rt.refresher._provider(),  # board fill
        rt.channels._answerer.provider(),  # channels
    ]


# ── What the runtime hands out ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("settings", "pid"),
    [
        (ModelSettings(kind="anthropic", model="claude-opus-5-5"), "anthropic"),
        (ModelSettings(kind="openai", model="gpt-6-sol"), "openai"),
        (
            ModelSettings(kind="custom", model="m", base_url="https://openrouter.ai/api/v1"),
            "custom:openrouter.ai",
        ),
        (
            ModelSettings(kind="local", model="gemma", base_url="http://localhost:11434/v1"),
            "local",
        ),
    ],
)
async def test_every_model_the_runtime_hands_out_is_metered_after_a_swap(
    rt: Runtime, settings: ModelSettings, pid: str
) -> None:
    await rt.use_model(settings)
    for provider in handed_out(rt):
        assert isinstance(provider, MeteredModel)
        assert provider is rt.provider
        assert provider.provider_id == pid


async def test_a_model_set_directly_is_metered_too(rt: Runtime) -> None:
    rt.provider = ScriptedProvider()
    assert isinstance(rt.provider, MeteredModel) and rt.provider.provider_id is None
    await rt.use_model(None)
    assert rt.provider is None


@pytest.mark.parametrize(
    ("settings", "pid"),
    [
        (SpeechSettings(kind="elevenlabs", voice="v1"), "elevenlabs"),
        (SpeechSettings(kind="system", voice="v1"), None),
        (
            SpeechSettings(kind="custom", voice="af", base_url="https://voice.example.com/v1"),
            "openai-voice:voice.example.com",
        ),
    ],
)
async def test_the_voice_is_metered_after_a_swap(
    rt: Runtime, settings: SpeechSettings, pid: str | None
) -> None:
    await rt.use_speech(settings)
    assert isinstance(rt.tts, MeteredVoice) and rt.tts.provider_id == pid
    await rt.use_speech(None)
    assert rt.tts is None


@pytest.mark.parametrize(
    ("settings", "pid"),
    [
        (ListenSettings(kind="deepgram", choice="en"), "deepgram"),
        (ListenSettings(kind="whisper"), None),
    ],
)
async def test_hearing_is_metered_after_a_swap(
    rt: Runtime, settings: ListenSettings, pid: str | None
) -> None:
    await rt.use_listening(settings)
    assert isinstance(rt.stt, MeteredHearing) and rt.stt.provider_id == pid


async def test_what_was_chosen_is_metered_from_the_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRILLION_HOME", str(tmp_path / "home"))
    secrets = MemoryStore()
    secrets.set("elevenlabs/api_key", "test-key")
    secrets.set("deepgram/api_key", "test-key")
    first = await open_runtime(persona=tmp_path, secrets=secrets, env={})
    try:
        await first.use_speech(SpeechSettings(kind="elevenlabs", voice="v1"))
        await first.use_listening(ListenSettings(kind="deepgram", choice="en"))
        await first.preferences.put("local", "speech", {"kind": "elevenlabs", "voice": "v1"}, 1)
        await first.preferences.put("local", "listening", {"kind": "deepgram", "choice": "en"}, 1)
    finally:
        await first.close()
    env = {ANTHROPIC_KEY: "test-key"}
    again = await open_runtime(persona=tmp_path, secrets=secrets, env=env)
    try:
        assert isinstance(again.provider, MeteredModel)
        assert again.provider.provider_id == "anthropic"
        assert isinstance(again.tts, MeteredVoice) and again.tts.provider_id == "elevenlabs"
        assert isinstance(again.stt, MeteredHearing) and again.stt.provider_id == "deepgram"
    finally:
        await again.close()
    env = {OPENAI_BASE_URL: "https://api.deepseek.com/v1", MODEL: "deepseek-flash"}
    custom = await open_runtime(persona=tmp_path, secrets=secrets, env=env)
    try:
        assert isinstance(custom.provider, MeteredModel)
        assert custom.provider.provider_id == "custom:api.deepseek.com"
    finally:
        await custom.close()


# ── Counted once ─────────────────────────────────────────────────────────


def test_a_turn_records_one_row_per_model_call_and_nothing_twice(home: Path) -> None:
    provider = ScriptedProvider(
        use("memory_recall", {"query": "coffee"}),
        say("Black, no sugar."),
        say("Coffee"),  # her naming the conversation
        names=True,
    )
    app = App(home, provider)
    try:
        before = app.rows("SELECT COUNT(*) FROM usage")[0][0]
        with app.connect() as chat:
            chat.say("how do I take my coffee?")
            sid = chat.session["id"] if "id" in chat.session else None
            chat.until("conversation.title")
        rows = app.rows("SELECT conversation_id, caller, input_tokens FROM usage ORDER BY id")
    finally:
        app.close()
    assert before == 0
    # Two calls in the turn, then her naming it: one row each, never one for the turn too.
    assert len(rows) == 3
    assert [r[1] for r in rows] == ["conversation", "conversation", "background"]
    assert provider.callers == ["conversation", "conversation", "background"]
    conversation = provider.conversations[0]
    assert conversation is not None and set(provider.conversations) == {conversation}
    assert {r[0] for r in rows} == {conversation}
    assert sid is None or sid == conversation


# ── Nothing goes around it ───────────────────────────────────────────────

# The calls named `stream`, `synthesize` or `open` that reach a provider, allowed where
# they are. A provider's own module talks to its service; the meter wraps them all; the
# rest are handed the runtime's metered provider (rt.provider, rt.tts, rt.stt), which the
# tests above check is always metered.
HANDED = "is handed the runtime's metered provider"
ALLOWED_MODULES = {
    "spend/metered.py": "the meter: the one place a wrapped provider is called",
    "brain/providers/anthropic.py": "a provider's own module, talking to its service",
    "brain/providers/openai_compat.py": "a provider's own module, talking to its service",
    "voice/providers/elevenlabs.py": "a provider's own module, talking to its service",
    "voice/providers/openai_compat.py": "a provider's own module, talking to its service",
    "voice/providers/deepgram.py": "a provider's own module, talking to its service",
    "voice/providers/whisper.py": "a provider's own module, on this computer",
    "voice/providers/system.py": "a provider's own module, on this computer",
}
ALLOWED_CALLS = {
    ("brain/loop.py", "run_turn", "stream"): f"a turn's model calls: {HANDED}",
    ("api/draft.py", "Draft._run", "stream"): f"an early draft (VOI-12): {HANDED}",
    ("brain/compact.py", "summarize", "stream"): f"the session's summary: {HANDED}",
    ("brain/title.py", "name.ask", "stream"): f"naming a conversation: {HANDED}",
    ("workspace/fill.py", "fill.ask", "stream"): f"a live widget's fill-in: {HANDED}",
    ("attention/mail.py", "UrgentEmail.judge.ask", "stream"): f"a watcher's judge: {HANDED}",
    ("attention/words.py", "ask.run", "stream"): f"a watcher's line: {HANDED}",
    ("channels/answer.py", "Answerer.answer", "stream"): f"a channel's answer: {HANDED}",
    ("api/speech.py", "Speaker._synthesize", "synthesize"): f"her voice: {HANDED}",
    ("api/listening.py", "Listener.start", "open"): f"hearing: {HANDED}",
}


def _source() -> Iterator[tuple[str, ast.Module]]:
    for path in sorted(SRC.rglob("*.py")):
        yield path.relative_to(SRC).as_posix(), ast.parse(path.read_text(), str(path))


def _calls(tree: ast.Module) -> Iterator[tuple[str, ast.Call]]:
    """Every call, with the dotted name of the function or method it is made in."""

    def walk(node: ast.AST, scope: list[str]) -> Iterator[tuple[str, ast.Call]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                yield from walk(child, [*scope, child.name])
                continue
            if isinstance(child, ast.Call):
                yield ".".join(scope), child
            yield from walk(child, scope)

    yield from walk(tree, [])


def _reaches_a_provider(call: ast.Call) -> str | None:
    """The method's name when this call can reach a paid provider. The receiver cannot
    be typed from the source, so: any `synthesize`; a `stream` sent a model's messages
    or system prompt; an `open` given a recognizer's choice; and any of the three on a
    receiver named like a provider."""
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in ("stream", "synthesize", "open"):
        return None
    keywords = {k.arg for k in call.keywords}
    receiver = ast.unparse(func.value).lower()
    named = any(word in receiver for word in ("provider", "tts", "stt", "model", "voice"))
    if func.attr == "synthesize" or named:
        return func.attr
    if func.attr == "stream" and keywords & {"messages", "system"}:
        return func.attr
    if func.attr == "open" and "choice" in keywords:
        return func.attr
    return None


def test_nothing_calls_a_provider_except_through_the_meter() -> None:
    found: set[tuple[str, str, str]] = set()
    for module, tree in _source():
        if module in ALLOWED_MODULES:
            continue
        for scope, call in _calls(tree):
            method = _reaches_a_provider(call)
            if method is not None:
                found.add((module, scope, method))
    stray = found - set(ALLOWED_CALLS)
    assert not stray, f"a provider call outside the meter: {sorted(stray)}"
    # And the allow-list holds nothing that is no longer there.
    assert found == set(ALLOWED_CALLS)


def test_the_scan_sees_a_provider_call_written_anywhere() -> None:
    tree = ast.parse(
        "async def f(p, http):\n"
        "    p.stream(system=[], messages=[], tools=[], max_tokens=1)\n"
        "    p.synthesize('hi', voice='v')\n"
        "    await p.open(choice='en')\n"
        "    http.stream('GET', 'https://example.com')\n"
    )
    found = [_reaches_a_provider(call) for _, call in _calls(tree)]
    assert [m for m in found if m] == ["stream", "synthesize", "open"]


def test_only_the_meter_records_usage() -> None:
    callers = [
        (module, scope)
        for module, tree in _source()
        for scope, call in _calls(tree)
        if (isinstance(call.func, ast.Attribute) and call.func.attr == "record_usage")
        or (isinstance(call.func, ast.Name) and call.func.id == "record_usage")
    ]
    assert callers == [("spend/metered.py", "Meter.record")]
