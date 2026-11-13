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
from tests.voice.fakes import FakeSTT
from tests.voice.test_speaker import FakeVoice
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


def background(rt: Runtime) -> list[Any]:
    """Every model the runtime hands to work it starts on its own."""
    assert rt.scheduler is not None and rt.attention is not None
    assert rt.refresher is not None and rt.channels is not None
    return [
        rt.background_provider,  # titles and interrupted sends
        rt.scheduler._routines._provider(),  # routines and timed asks
        rt.attention._provider(),  # the watchers
        rt.refresher._provider(),  # board fill
        rt.channels._answerer.provider(),  # channels
    ]


def handed_out(rt: Runtime) -> list[Any]:
    return [rt.provider, *background(rt)]


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
        (
            ModelSettings(kind="custom", model="gemma", base_url="http://127.0.0.1:8080/v1"),
            "local",
        ),
        (
            ModelSettings(kind="custom", model="gpt-6-sol", base_url="https://api.openai.com/v1"),
            "openai",
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


async def test_background_work_keeps_the_usual_model_through_a_switch(rt: Runtime) -> None:
    """A switch is for the conversation only (decision 7, BRN-11): background work is
    handed the usual model, metered against its own cap."""
    from trillion.spend.desk import Switch

    await rt.use_model(ModelSettings(kind="anthropic", model="claude-opus-5-5"))
    local = ModelSettings(kind="local", model="gemma", base_url="http://localhost:11434/v1")
    rt.spend._switches["anthropic"] = Switch(
        leg="model",
        to="local/gemma",
        label="gemma",
        month=rt.spend.month(),
        until_ms=rt.spend.until(),
        settings=local.to_json(),
    )
    await rt.use_switches(frozenset({"model"}))
    assert isinstance(rt.provider, MeteredModel) and rt.provider.provider_id == "local"
    usual = rt.background_provider
    assert isinstance(usual, MeteredModel) and usual.provider_id == "anthropic"
    for provider in background(rt):
        assert provider is usual


async def test_a_model_set_directly_is_metered_too(rt: Runtime) -> None:
    rt.provider = ScriptedProvider()
    assert isinstance(rt.provider, MeteredModel) and rt.provider.provider_id is None
    await rt.use_model(None)
    assert rt.provider is None


async def test_one_set_directly_counts_against_the_cap_of_what_is_chosen(rt: Runtime) -> None:
    await rt.use_model(ModelSettings(kind="anthropic", model="claude-opus-5-5"))
    rt.provider = ScriptedProvider()
    assert isinstance(rt.provider, MeteredModel) and rt.provider.provider_id == "anthropic"
    await rt.use_speech(SpeechSettings(kind="elevenlabs", voice="v1"))
    rt.tts = FakeVoice()
    assert isinstance(rt.tts, MeteredVoice) and rt.tts.provider_id == "elevenlabs"
    await rt.use_listening(ListenSettings(kind="deepgram", choice="en"))
    rt.stt = FakeSTT()
    assert isinstance(rt.stt, MeteredHearing) and rt.stt.provider_id == "deepgram"


async def test_closing_waits_for_rows_still_being_written(rt: Runtime) -> None:
    order: list[str] = []
    stop = rt.stop_background

    async def stopped() -> None:
        await stop()
        order.append("stopped")

    async def drained() -> None:
        if "drained" not in order:
            # The database is still open.
            await rt.conversations._conn.execute("SELECT 1")
            order.append("drained")

    rt.stop_background = stopped  # type: ignore[method-assign]
    rt.meter.drain = drained  # type: ignore[method-assign]
    await rt.close()
    assert order == ["stopped", "drained"]
    rt.stop_background = stop  # type: ignore[method-assign]


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
    for base_url, pid in [
        ("https://api.deepseek.com/v1", "custom:api.deepseek.com"),
        ("http://localhost:11434/v1", "local"),
        ("https://api.openai.com/v1", "openai"),
    ]:
        env = {OPENAI_BASE_URL: base_url, MODEL: "some-model"}
        custom = await open_runtime(persona=tmp_path, secrets=secrets, env=env)
        try:
            assert isinstance(custom.provider, MeteredModel)
            assert custom.provider.provider_id == pid
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

# Every `stream`, `synthesize` and `open` in the source, called or only named, and every
# `getattr` of one, is a provider call unless it is allowed here. The receiver cannot
# be typed from the source, so the list is of where each one is and what it is on.
HANDED = "is handed the runtime's metered provider, which the tests above check"
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
# (module, function, method, receiver): calls on the metered provider.
ALLOWED_CALLS = {
    ("brain/loop.py", "run_turn", "stream", "provider"): f"a turn's model calls: {HANDED}",
    ("api/draft.py", "Draft._run", "stream", "provider"): f"an early draft: {HANDED}",
    ("brain/compact.py", "summarize", "stream", "provider"): f"a summary: {HANDED}",
    ("brain/title.py", "name.ask", "stream", "provider"): f"naming: {HANDED}",
    ("memory/notes.py", "ask", "stream", "provider"): f"notes from chats: {HANDED}",
    ("workspace/fill.py", "fill.ask", "stream", "provider"): f"a widget's fill-in: {HANDED}",
    ("attention/mail.py", "UrgentEmail.judge.ask", "stream", "provider"): f"a judge: {HANDED}",
    ("attention/words.py", "ask.run", "stream", "provider"): f"a watcher's line: {HANDED}",
    ("channels/answer.py", "Answerer.answer", "stream", "provider"): f"a channel: {HANDED}",
    ("api/speech.py", "Speaker._synthesize", "synthesize", "self._provider"): f"voice: {HANDED}",
    ("api/listening.py", "Listener.start", "open", "self._provider"): f"hearing: {HANDED}",
    ("browser/service.py", "BrowserService._wrap_up", "stream", "provider"): (
        f"a browsing task's last word: {HANDED}"
    ),
}
# (module, receiver): what is not a provider at all.
NOT_PROVIDERS = {
    ("api/session.py", "clock"): "the event loop's clock: `clock.open` is not a call",
    ("brain/sse.py", "client"): "an HTTP client's streamed request",
    ("tools/web.py", "pool"): "an HTTP connection pool's streamed request",
    ("voice/models.py", "http"): "an HTTP client downloading a model file",
    ("voice/models.py", "part"): "a file being written",
    ("tools/files.py", "p"): "a file being read",
    ("cli/main.py", "webbrowser"): "opening the app in the browser",
    ("skills/parse.py", "os"): "a file descriptor",
    ("spine/router.py", "self"): "a response's own fields, read by name",
    ("spine/audit.py", "os"): "a file descriptor",
    ("spine/deny_floor.py", "os"): "a file descriptor",
    ("tools/beneath.py", "os"): "a file descriptor",
    ("secrets/fallback.py", "os"): "a file descriptor",
    ("browser/runtime.py", "path"): "a file being hashed",
    ("browser/service.py", "task"): "a browsing task opening its browser context",
}
NAMES = frozenset({"stream", "synthesize", "open"})


def _source() -> Iterator[tuple[str, ast.Module]]:
    for path in sorted(SRC.rglob("*.py")):
        yield (
            path.relative_to(SRC).as_posix(),
            ast.parse(path.read_text(encoding="utf-8"), str(path)),
        )


def _nodes(tree: ast.Module) -> Iterator[tuple[str, ast.AST]]:
    """Every node, with the dotted name of the function or class it is in."""

    def walk(node: ast.AST, scope: list[str]) -> Iterator[tuple[str, ast.AST]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                yield from walk(child, [*scope, child.name])
                continue
            yield ".".join(scope), child
            yield from walk(child, scope)

    yield from walk(tree, [])


def _provider_uses(tree: ast.Module) -> Iterator[tuple[str, str, str]]:
    """(function, method, receiver) for every `x.stream`, `x.synthesize` or `x.open`,
    called or not, and every `getattr(x, "stream")` and the like."""
    for scope, node in _nodes(tree):
        if isinstance(node, ast.Attribute) and node.attr in NAMES:
            yield scope, node.attr, ast.unparse(node.value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
        ):
            name = node.args[1]
            if not isinstance(name, ast.Constant) or name.value in NAMES:
                # A name worked out at run time could be any of them.
                yield scope, "getattr", ast.unparse(node.args[0])


def test_nothing_calls_a_provider_except_through_the_meter() -> None:
    found: set[tuple[str, str, str, str]] = set()
    excused: set[tuple[str, str]] = set()
    for module, tree in _source():
        if module in ALLOWED_MODULES:
            continue
        for scope, method, receiver in _provider_uses(tree):
            if (module, receiver) in NOT_PROVIDERS:
                excused.add((module, receiver))
            else:
                found.add((module, scope, method, receiver))
    stray = found - set(ALLOWED_CALLS)
    assert not stray, f"a provider call outside the meter: {sorted(stray)}"
    # And neither list holds anything that is no longer there.
    assert found == set(ALLOWED_CALLS)
    assert excused == set(NOT_PROVIDERS)


@pytest.mark.parametrize(
    "line",
    [
        "p.stream(system=[], messages=[], tools=[], max_tokens=1)",
        "p.stream(msgs, tools=t)",
        "llm.stream(messages)",
        "brain.stream(**kw)",
        "s = rt.provider.stream",
        "getattr(rt.provider, 'stream')",
        "getattr(rt.provider, name)",
        "tts.synthesize('hi', voice='v')",
        "speaker.synthesize",
        "await ears.open(choice='en')",
        "f = rt.stt.open",
    ],
)
def test_the_scan_sees_a_provider_use_however_it_is_written(line: str) -> None:
    tree = ast.parse(f"async def f():\n    {line}\n")
    assert list(_provider_uses(tree)), line


# ── Nothing builds one around it ─────────────────────────────────────────

# Making a provider: what may make one, and where. The runtime makes the ones it
# holds through its `metered_*` helpers; anything else only lists, checks or prepares,
# which costs nothing, or makes something that is not a provider at all.
CONSTRUCTORS = frozenset(
    {
        "build",
        "build_speech",
        "build_listening",
        "resolve",
        "provider_from_env",
        "AnthropicProvider",
        "OpenAICompatProvider",
        "ElevenLabs",
        "CompatibleSpeech",
        "Deepgram",
        "LocalWhisper",
        "SystemVoice",
    }
)
FREE = "only lists, checks or prepares: free, and never handed out"
NOT_A_PROVIDER = "builds something that is not a provider"
BUILDERS = {
    "brain/config.py": "where a model provider is made, for the runtime's helpers to meter",
    "voice/catalog.py": "where a voice or hearing provider is made, for the same",
}
ALLOWED_BUILDS = {
    ("api/runtime.py", "build_speech", "option.build"): "the builder metered_speech calls",
    ("api/runtime.py", "build_listening", "option.build"): "the builder metered_listening calls",
    ("api/runtime.py", "metered_speech", "build_speech"): "metered as it is made",
    ("api/runtime.py", "metered_listening", "build_listening"): "metered as it is made",
    ("api/runtime.py", "metered_resolve", "resolve"): "metered as it is made",
    ("api/settings.py", "create_router.voices", "option.build"): f"Settings: {FREE}",
    ("api/settings.py", "create_router.listen_choices", "option.build"): f"Settings: {FREE}",
    ("api/settings.py", "create_router.prepare_listening", "option.build"): f"Settings: {FREE}",
    ("cli/doctor.py", "check_model", "config.resolve"): f"the doctor: {FREE}",
    ("cli/doctor.py", "check_speech", "option.build"): f"the doctor: {FREE}",
    ("cli/doctor.py", "check_listening.ask", "option.build"): f"the doctor: {FREE}",
    ("cli/doctor.py", "check_credentials", "speech.build"): f"the doctor: {FREE}",
    ("cli/doctor.py", "check_credentials", "listen.build"): f"the doctor: {FREE}",
    ("cli/main.py", "_serve", "provider_from_env"): "fails a half-made setting early",
    ("api/mcp.py", "create_mcp_router.add", "catalog.build"): NOT_A_PROVIDER,
    ("connectors/google/tools.py", "GmailDraft.run", "self.build"): NOT_A_PROVIDER,
    ("connectors/google/tools.py", "GmailSend.run", "self.build"): NOT_A_PROVIDER,
    ("scheduler/runner.py", "Scheduler._routine.work", "routines.build"): NOT_A_PROVIDER,
    ("workspace/blocks.py", "_blocks", "build"): NOT_A_PROVIDER,
    ("workspace/widgets.py", "validate_widget", "build"): NOT_A_PROVIDER,
}


def _builds(tree: ast.Module) -> Iterator[tuple[str, str]]:
    """(function, callee) for every call that may make a provider. A `resolve` on
    anything but the model config is a path's, and is left out."""
    for scope, node in _nodes(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in CONSTRUCTORS:
            yield scope, func.id
        elif isinstance(func, ast.Attribute) and func.attr in CONSTRUCTORS:
            if func.attr == "resolve" and ast.unparse(func.value) != "config":
                continue
            yield scope, ast.unparse(func)


def test_nothing_makes_a_provider_outside_the_metered_helpers() -> None:
    found = {
        (module, scope, callee)
        for module, tree in _source()
        if module not in BUILDERS
        for scope, callee in _builds(tree)
    }
    stray = found - set(ALLOWED_BUILDS)
    assert not stray, f"a provider made outside the metered helpers: {sorted(stray)}"
    assert found == set(ALLOWED_BUILDS)


def test_only_the_meter_records_usage() -> None:
    callers = [
        (module, scope)
        for module, tree in _source()
        for scope, node in _nodes(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == "record_usage")
            or (isinstance(node.func, ast.Name) and node.func.id == "record_usage")
        )
    ]
    assert callers == [("spend/metered.py", "Meter.record")]
