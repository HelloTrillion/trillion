"""`trillion doctor`: what is set up on this machine, what is not, and how to fix it (ONB-5).

It measures and never repairs: nothing here creates a file, applies a migration or
changes a setting. The database is opened read-only. The only things it contacts are
the providers the user already chose, and only to ask what they offer, which costs
nothing and proves a key works. `--offline` skips even that.

No credential is ever printed. A key is shown as present, with its length and its
last four characters at most (SEC-4, SEC-9).
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Literal

from trillion import __version__, paths
from trillion.brain import config
from trillion.brain.config import ConfigError, ModelSettings
from trillion.brain.types import ProviderError
from trillion.secrets.store import SecretStore
from trillion.storage import inspect
from trillion.voice.catalog import LISTENING, SPEECH, listen_option, speech_option
from trillion.voice.types import VoiceError

Status = Literal["ok", "info", "warn", "fail"]
OWNER = "local"
NETWORK_TIMEOUT_S = 8.0
# Folders that sync clients own. A database or a memory file inside one can lose writes (OPS-2).
_SYNCED = ("onedrive", "dropbox", "google drive", "googledrive", "icloud", "mobile documents")


@dataclass(frozen=True, kw_only=True)
class Check:
    name: str
    status: Status
    detail: str
    # What to do about it, as something the user can act on. Empty when there is nothing to do.
    fix: str = ""


@dataclass(frozen=True, kw_only=True)
class Options:
    port: int = 7777
    host: str = "127.0.0.1"
    # Skip every check that needs the network.
    offline: bool = False
    # Also test every saved credential, not only the ones in use.
    secrets: bool = False


def shown(value: str) -> str:
    """A credential as diagnostics may show it: never the value (SEC-4)."""
    ending = f", ending in {value[-4:]}" if len(value) >= 20 else ""
    return f"{len(value)} characters{ending}"


# ── This machine ─────────────────────────────────────────────────────────


def check_build() -> Check:
    v = sys.version_info
    detail = f"Trillion {__version__}, Python {v.major}.{v.minor}.{v.micro}, {platform.system()}"
    if (v.major, v.minor) < (3, 13):
        return Check(
            name="Version",
            status="fail",
            detail=detail,
            fix="Trillion needs Python 3.13 or newer. Reinstall with: uv tool install trillion",
        )
    return Check(name="Version", status="ok", detail=detail)


def check_home(home: Path) -> Check:
    name = "Data folder"
    if any(mark in part.lower() for part in home.parts for mark in _SYNCED):
        return Check(
            name=name,
            status="warn",
            detail=f"{home} is inside a synced folder",
            fix="Sync clients can cut off a write and lose a memory. Move it out, or point "
            "TRILLION_HOME at a folder that is not synced.",
        )
    if not home.exists():
        return Check(name=name, status="info", detail=f"{home} will be made on the first run")
    try:
        with tempfile.NamedTemporaryFile(dir=home, prefix=".doctor-"):
            pass
    except OSError:
        return Check(
            name=name,
            status="fail",
            detail=f"{home} cannot be written to",
            fix="Make the folder yours to write, or point TRILLION_HOME at one that is.",
        )
    return Check(name=name, status="ok", detail=str(home))


def check_database(path: Path) -> Check:
    name = "Database"
    if not path.exists():
        return Check(name=name, status="info", detail="not made yet: the first run creates it")
    try:
        state = inspect.schema(path)
    except inspect.Unreadable:
        return Check(
            name=name,
            status="fail",
            detail=f"{path} could not be read",
            fix="Stop Trillion if it is running and try again. If it still fails, the file is "
            "damaged: keep a copy, then move it aside so a new one is made.",
        )
    healthy, applied = state.sound, state.applied
    shipped = inspect.shipped()
    if not healthy:
        return Check(
            name=name,
            status="fail",
            detail="the file failed its integrity check",
            fix="Keep a copy of the file, then move it aside so a new one is made.",
        )
    if set(applied) - set(shipped):
        return Check(
            name=name,
            status="fail",
            detail="it was last used by a newer Trillion than this one",
            fix="Update Trillion: uv tool upgrade trillion",
        )
    if any(applied[v] != shipped[v] for v in applied):
        return Check(
            name=name,
            status="fail",
            detail="its history does not match this build",
            fix="This build's migrations differ from the ones that made the database. "
            "Reinstall the released version.",
        )
    pending = len(set(shipped) - set(applied))
    if pending:
        s = "" if pending == 1 else "s"
        return Check(name=name, status="info", detail=f"{pending} update{s} will apply on start")
    return Check(name=name, status="ok", detail=f"up to date ({len(applied)} migrations)")


def _is_trillion(host: str, port: int) -> bool:
    """Whether the thing on the port answers the way this app's own server does.

    A settings request with no token is refused in a way that is ours: a 401 whose
    whole body is one word. A page that merely mentions the name does not count."""
    try:
        urllib.request.urlopen(f"http://{host}:{port}/api/settings/persona", timeout=1.5).close()
    except urllib.error.HTTPError as e:
        return e.code == 401 and e.read(64) == b"unauthenticated"
    except (OSError, ValueError):
        return False
    return False


def check_port(host: str, port: int) -> Check:
    name = "Port"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if os.name == "posix":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            if _is_trillion(host, port):
                return Check(
                    name=name, status="ok", detail=f"{port}: Trillion is already running on it"
                )
            return Check(
                name=name,
                status="warn",
                detail=f"{port} is in use by something else",
                fix=f"Stop what is using it, or start on another: trillion run --port {port + 1}",
            )
    return Check(name=name, status="ok", detail=f"{port} is free")


def _newest_source(found: Path) -> float:
    """In a source checkout, when the page's sources last changed. 0 for an installed build."""
    root = found.parents[2] if found.name == "dist" and len(found.parents) > 2 else None
    if root is None or not (found.parent / "src").is_dir():
        return 0.0
    sources = [found.parent / "src", found.parent / "index.html"]
    sources += [pkg / "src" for pkg in (root / "packages").glob("*")]
    newest = 0.0
    for source in sources:
        files = source.rglob("*") if source.is_dir() else [source]
        newest = max([newest, *(f.stat().st_mtime for f in files if f.is_file())])
    return newest


def check_page(found: Path | None) -> Check:
    fix = "In a source checkout: pnpm install && pnpm -r build"
    if found is None:
        return Check(
            name="App page",
            status="fail",
            detail="the page the browser opens has not been built",
            fix=fix,
        )
    built = found / "index.html"
    if built.is_file() and _newest_source(found) > built.stat().st_mtime:
        return Check(
            name="App page",
            status="warn",
            detail=f"{found} is older than its source, so the browser gets the old app",
            fix=fix,
        )
    return Check(name="App page", status="ok", detail=str(found))


def check_keychain(secrets: SecretStore, backend: str = "") -> Check:
    name = "Keychain"
    if secrets.available():
        return Check(name=name, status="ok", detail=backend or "available")
    fix = "Without one, a key cannot be saved in the app. A local model still works."
    if sys.platform.startswith("linux"):
        fix = (
            "Install and unlock a Secret Service keychain (GNOME Keyring or KWallet). "
            "Without one, a key cannot be saved in the app. A local model still works."
        )
    return Check(name=name, status="warn", detail="this machine has no system keychain", fix=fix)


def _answers(*command: str) -> str | None:
    """A sound tool's output, or None if it is missing, fails or hangs."""
    if shutil.which(command[0]) is None:
        return None
    try:
        # Only the fixed sound-tool commands above, never anything the user typed.
        done = subprocess.run(command, capture_output=True, text=True, timeout=3, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def _linux_sound() -> tuple[bool, bool]:
    """Whether a sound server answers, and whether it has a microphone.

    pactl speaks for both PulseAudio and PipeWire's Pulse layer, which is what the
    browser itself talks to. A monitor source is speaker output, not a microphone."""
    if shutil.which("pactl"):
        if _answers("pactl", "info") is None:
            return False, False
        sources = _answers("pactl", "list", "short", "sources") or ""
        names = [line.split("\t")[1] for line in sources.splitlines() if "\t" in line]
        return True, any(not n.endswith(".monitor") for n in names)
    if _answers("wpctl", "status") is None:
        return False, False
    return True, _answers("wpctl", "inspect", "@DEFAULT_AUDIO_SOURCE@") is not None


def check_microphone() -> Check:
    name = "Microphone"
    told = "your browser opens it and asks permission the first time you press Speak"
    if sys.platform.startswith("linux"):
        server = "PipeWire" if shutil.which("wpctl") or shutil.which("pw-cli") else None
        server = server or ("PulseAudio" if shutil.which("pactl") else None)
        if server is not None:
            running, mic = _linux_sound()
            if not running:
                return Check(
                    name=name,
                    status="warn",
                    detail=f"{server} is installed but not running",
                    fix=f"Start {server} (or log out and back in) so the browser has a "
                    "microphone to open.",
                )
            if not mic:
                return Check(
                    name=name,
                    status="warn",
                    detail=f"{server} is running, but no microphone is connected",
                    fix="Plug in or turn on a microphone. You can still type to her.",
                )
            return Check(name=name, status="ok", detail=f"{server} has a microphone; {told}")
        return Check(
            name=name,
            status="warn",
            detail="no PipeWire or PulseAudio was found",
            fix="Install PipeWire (or PulseAudio) so the browser has a microphone to open.",
        )
    return Check(name=name, status="info", detail=told)


# ── What the user chose ──────────────────────────────────────────────────


async def _within[T](call: Awaitable[T]) -> T:
    return await asyncio.wait_for(call, NETWORK_TIMEOUT_S)


_WHY: Mapping[str, str] = {
    "unauthorized": "the key was refused",
    "forbidden": "the key is not allowed to do this",
    "rate_limited": "the provider is rate limiting this key",
    "unreachable": "it could not be reached",
    "timeout": "it did not answer in time",
    "not_installed": "its optional part is not installed",
}


def _why(kind: str) -> str:
    return _WHY.get(kind, f"it failed ({kind})")


async def _try(call: Callable[[], Awaitable[Any]]) -> str | None:
    """Run one provider check. None if it worked, otherwise a safe word for why not."""
    try:
        await _within(call())
    except TimeoutError:
        return "timeout"
    except (ProviderError, VoiceError) as e:
        return e.kind
    except ConfigError as e:
        return e.code
    except OSError:
        return "unreachable"
    return None


async def check_model(
    settings: ModelSettings | None,
    secrets: SecretStore,
    env: Mapping[str, str],
    *,
    offline: bool,
) -> Check:
    name = "Model"
    base, key = env.get(config.OPENAI_BASE_URL, "").strip(), None
    try:
        provider, source = config.resolve(settings, secrets, env)
    except ConfigError as e:
        return Check(
            name=name,
            status="fail",
            detail=f"the environment's model settings are not usable: {e}",
            fix="Fix or unset the TRILLION_ variables for the model.",
        )
    if provider is None:
        if settings is not None:
            return Check(
                name=name,
                status="fail",
                detail=f"{settings.model} is chosen, but its key is gone from the keychain",
                fix="Open the app and enter the key again in the model settings.",
            )
        return Check(name=name, status="info", detail="none chosen yet: the app asks on first run")
    if source == "env":
        kind: config.Kind = "custom" if base else "anthropic"
        key = env.get(config.OPENAI_KEY if base else config.ANTHROPIC_KEY) or None
        url = base or None
    elif settings is not None:
        kind, url = settings.kind, settings.base_url
        key = secrets.get(config.secret_name(kind))
    else:  # pragma: no cover - resolve() only builds from the environment or from settings
        return Check(name=name, status="info", detail="none chosen yet")
    said = f"{provider.model} ({'environment' if source == 'env' else 'chosen in the app'})"
    if offline:
        return Check(name=name, status="info", detail=f"{said}, not tested: offline")

    found: list[Any] = []

    async def ask() -> None:
        found.extend(await config.list_models(kind, url, key))

    problem = await _try(ask)
    if problem is not None:
        local = kind == "local" or (url is not None and config.keyless(url))
        fix = (
            "Start the model server on this computer (for Ollama: ollama serve) and try again."
            if local
            else "Check the key and your connection, then run: trillion doctor"
        )
        return Check(name=name, status="fail", detail=f"{said}: {_why(problem)}", fix=fix)
    if not any(m.id == provider.model for m in found):
        return Check(
            name=name,
            status="warn",
            detail=f"{said} answered, but does not list that model",
            fix="Open the app and choose a model it offers.",
        )
    return Check(name=name, status="ok", detail=f"{said} answers")


async def check_speech(raw: Any, secrets: SecretStore, *, offline: bool) -> Check:
    name = "Voice"
    option = speech_option(str(raw.get("kind"))) if isinstance(raw, dict) else None
    if option is None:
        return Check(name=name, status="info", detail="off: she answers in text")
    if option.where == "client":
        return Check(name=name, status="ok", detail=f"{option.title}: spoken by your browser")
    if option.needs_key and not secrets.get(option.secret):
        return Check(
            name=name,
            status="fail",
            detail=f"{option.title} is chosen, but its key is gone from the keychain",
            fix="Open the app and enter the key again in the voice settings.",
        )
    if offline:
        return Check(name=name, status="info", detail=f"{option.title}, not tested: offline")
    url = str(raw["base_url"]) if raw.get("base_url") else None
    problem = await _try(lambda: option.build(secrets, url).voices())
    if problem is not None:
        return Check(
            name=name,
            status="fail",
            detail=f"{option.title}: {_why(problem)}",
            fix="Open the voice settings in the app and check the key or the address.",
        )
    return Check(name=name, status="ok", detail=f"{option.title} answers")


async def check_listening(raw: Any, secrets: SecretStore, *, offline: bool) -> Check:
    name = "Hearing"
    option = listen_option(str(raw.get("kind"))) if isinstance(raw, dict) else None
    if option is None:
        return Check(name=name, status="info", detail="off: you type to her")
    if option.needs_key and not secrets.get(option.secret):
        return Check(
            name=name,
            status="fail",
            detail=f"{option.title} is chosen, but its key is gone from the keychain",
            fix="Open the app and enter the key again in the mic settings.",
        )
    if option.needs_key and offline:
        return Check(name=name, status="info", detail=f"{option.title}, not tested: offline")
    choices: list[Any] = []

    async def ask() -> None:
        choices.extend(await option.build(secrets).choices())

    problem = await _try(ask)
    if problem == "not_installed":
        hint = f" Install it with: {option.install_hint}" if option.install_hint else ""
        return Check(
            name=name,
            status="fail",
            detail=f"{option.title} is chosen, but it is not installed",
            fix=f"The part of Trillion that does this is optional.{hint}",
        )
    if problem is not None:
        return Check(
            name=name,
            status="fail",
            detail=f"{option.title}: {_why(problem)}",
            fix="Open the mic settings in the app and check the key.",
        )
    chosen = next((c for c in choices if c.id == raw.get("choice")), None)
    if chosen is not None and chosen.download is not None:
        return Check(
            name=name,
            status="warn",
            detail=f"{option.title} is chosen, but its model has not been downloaded",
            fix="Open the mic settings in the app and press the download button once.",
        )
    return Check(name=name, status="ok", detail=f"{option.title} is ready")


def check_introduction(directory: Path) -> Check:
    from trillion.persona import setup

    profile = setup.load(directory)
    if profile is None:
        return Check(
            name="Names", status="info", detail="not introduced yet: she asks on the first run"
        )
    detail = f"you are {profile.user_name}, she is {profile.agent_name}"
    return Check(name="Names", status="ok", detail=detail)


def check_memory(directory: Path) -> Check:
    from trillion.memory import files as memory_files

    if not directory.exists():
        return Check(name="Memory", status="info", detail="nothing saved yet")
    scan = memory_files.scan(directory)
    total = len(scan.found)
    said = "nothing saved yet" if total == 0 else f"{total} saved"
    if scan.unreadable or not scan.complete:
        bad = len(scan.unreadable)
        return Check(
            name="Memory",
            status="warn",
            detail=f"{said}; {bad} file{'s' if bad != 1 else ''} could not be read",
            fix=f"Look in {directory} for a file that is not a memory, or was cut off.",
        )
    return Check(name="Memory", status="ok" if total else "info", detail=said)


# ── Every saved credential (SEC-9) ───────────────────────────────────────


async def check_credentials(
    secrets: SecretStore, env: Mapping[str, str], *, offline: bool, backend: str = ""
) -> list[Check]:
    """One line per place a key can live: whether it is there and whether it works."""
    out = [
        Check(
            name="Secret store",
            status="ok" if secrets.available() else "warn",
            detail=(backend or "the system keychain") if secrets.available() else "none",
        )
    ]

    async def report(
        name: str, value: str | None, test: Callable[[], Awaitable[Any]] | None
    ) -> None:
        if not value:
            out.append(Check(name=name, status="info", detail="not set"))
            return
        if offline or test is None:
            out.append(Check(name=name, status="info", detail=f"{shown(value)}, not tested"))
            return
        problem = await _try(test)
        if problem is None:
            out.append(Check(name=name, status="ok", detail=f"{shown(value)}, works"))
        else:
            out.append(
                Check(
                    name=name,
                    status="fail",
                    detail=f"{shown(value)}: {_why(problem)}",
                    fix="Replace it in the app's settings.",
                )
            )

    hosted: tuple[config.Kind, ...] = ("anthropic", "openai")
    for kind in hosted:
        value = secrets.get(config.secret_name(kind))
        await report(f"Key: {kind}", value, partial(config.list_models, kind, None, value))
    # A compatible service's key belongs to an address only the saved settings know.
    await report("Key: custom", secrets.get(config.secret_name("custom")), None)
    for speech in SPEECH:
        if speech.needs_key:
            await report(
                f"Key: {speech.kind}",
                secrets.get(speech.secret),
                speech.build(secrets, None).voices,
            )
    for listen in LISTENING:
        if listen.needs_key:
            await report(
                f"Key: {listen.kind}",
                secrets.get(listen.secret),
                listen.build(secrets).choices,
            )
    for variable in (config.ANTHROPIC_KEY, config.OPENAI_KEY):
        value = env.get(variable)
        if value:
            out.append(Check(name=f"Env: {variable}", status="info", detail=f"{shown(value)}, set"))
    return out


# ── The whole examination ────────────────────────────────────────────────


async def examine(
    options: Options,
    *,
    secrets: SecretStore,
    env: Mapping[str, str] | None = None,
    page: Path | None = None,
    backend: str = "",
) -> list[Check]:
    env = os.environ if env is None else env
    prefs = inspect.preferences(paths.db_path(), OWNER)
    checks = [
        check_build(),
        check_home(paths.home()),
        check_database(paths.db_path()),
        check_page(page),
        check_port(options.host, options.port),
        check_keychain(secrets, backend),
        await check_model(
            ModelSettings.from_json(prefs.get("model")), secrets, env, offline=options.offline
        ),
        await check_speech(prefs.get("speech"), secrets, offline=options.offline),
        await check_listening(prefs.get("listening"), secrets, offline=options.offline),
        check_microphone(),
        check_introduction(paths.persona_dir()),
        check_memory(paths.memory_dir()),
    ]
    if options.secrets:
        checks += await check_credentials(secrets, env, offline=options.offline, backend=backend)
    return checks


def render(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    lines = []
    for c in checks:
        lines.append(f"{c.status:<4}  {c.name:<{width}}  {c.detail}")
        if c.fix:
            lines.append(f"{'':<4}  {'':<{width}}  -> {c.fix}")
    failed = sum(c.status == "fail" for c in checks)
    warned = sum(c.status == "warn" for c in checks)
    if failed:
        verdict = f"{failed} thing{'s' if failed != 1 else ''} to fix before Trillion will work."
    elif warned:
        verdict = f"Ready, with {warned} thing{'s' if warned != 1 else ''} worth a look."
    else:
        verdict = "Everything checks out."
    return "\n".join([*lines, "", verdict]) + "\n"


def healthy(checks: list[Check]) -> bool:
    return not any(c.status == "fail" for c in checks)
